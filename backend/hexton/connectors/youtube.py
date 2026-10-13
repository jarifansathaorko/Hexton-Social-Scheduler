"""The YouTube connector.

Implements YouTube Data API v3 and resumable uploads over httpx.
- When unconfigured (no Google Client ID), enforces the Phase 0 safety gate.
- When configured with credentials, performs live API calls:
  - Resumable protocol with Location header persistence
  - Status probe and range resumption
  - Chunked transfer over documented Google endpoint
  - Channel identity discovery via channels.list(mine=true)
  - Publish status updates and deletion
  - OAuth token refresh and revocation
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from hexton.connectors.base import (
    ChannelSummary,
    ConnectorError,
    PlatformConnector,
    ProbeResult,
    TokenSet,
    UploadOutcome,
    UploadStart,
    VideoStatus,
)
from hexton.domain.jobstate import FailureKind

log = logging.getLogger(__name__)

_GATE = (
    "The real YouTube connector is not enabled in this build. Phase 0 recorded "
    "FEAS-01..FEAS-05 as blocked pending a Google Cloud project, an OAuth consent "
    "configuration, and a test channel; wiring the live connector before those pass "
    "would put an unverified integration on the critical path. See "
    "08-phase0-feasibility-report.md §9 for the prerequisites."
)


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_range_header(value: str | None) -> int:
    """Return highest confirmed byte index from Range header or -1."""
    if not value:
        return -1
    value = value.strip()
    if not value.lower().startswith("bytes="):
        return -1
    spec = value[len("bytes=") :].strip()
    if "-" not in spec:
        return -1
    high = spec.split("-", 1)[1].strip()
    if not high:
        return -1
    try:
        return int(high)
    except ValueError:
        return -1


def _map_http_error(resp: httpx.Response, action: str) -> ConnectorError:
    status = resp.status_code
    text = resp.text[:300]
    if status == 401:
        return ConnectorError(
            f"{action} failed: Unauthorized (401). {text}",
            kind=FailureKind.AUTHORIZATION,
            retryable=False,
        )
    if status == 403:
        if "quota" in text.lower():
            return ConnectorError(
                f"{action} failed: YouTube API quota exceeded. {text}",
                kind=FailureKind.QUOTA,
                retryable=False,
            )
        return ConnectorError(
            f"{action} failed: Forbidden (403). {text}",
            kind=FailureKind.AUTHORIZATION,
            retryable=False,
        )
    if status == 404:
        return ConnectorError(
            f"{action} failed: Resource not found (404). {text}",
            kind=FailureKind.PERMANENT,
            retryable=False,
        )
    if status == 429 or status in {500, 502, 503, 504}:
        return ConnectorError(
            f"{action} failed: Transient error ({status}). {text}",
            kind=FailureKind.TRANSIENT,
            retryable=True,
        )
    if status == 400:
        return ConnectorError(
            f"{action} failed: Invalid request (400). {text}",
            kind=FailureKind.VALIDATION,
            retryable=False,
        )
    return ConnectorError(
        f"{action} failed: HTTP {status}. {text}",
        kind=FailureKind.PERMANENT,
        retryable=False,
    )


def _parse_video(raw: dict[str, Any]) -> VideoStatus:
    video_id = raw.get("id", "")
    status = raw.get("status", {})
    snippet = raw.get("snippet", {})
    processing = raw.get("processingDetails", {})
    return VideoStatus(
        video_id=video_id,
        privacy_status=status.get("privacyStatus", "private"),
        upload_status=status.get("uploadStatus", "uploaded"),
        processing_status=processing.get("processingStatus"),
        publish_at=_parse_iso(status.get("publishAt")),
        title=snippet.get("title"),
        raw=raw,
    )


class YouTubeConnector(PlatformConnector):
    """The YouTube Data API connector."""

    provider = "youtube"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        enabled: bool = False,
        api_base_url: str = "https://www.googleapis.com",
        upload_base_url: str = "https://www.googleapis.com",
        oauth_base_url: str = "https://oauth2.googleapis.com",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.enabled = enabled or bool(client_id)
        self.reaches_platform = self.enabled
        self.api_base_url = api_base_url.rstrip("/")
        self.upload_base_url = upload_base_url.rstrip("/")
        self.oauth_base_url = oauth_base_url.rstrip("/")

    def _check_gate(self) -> None:
        if not self.enabled:
            raise ConnectorError(_GATE, kind=FailureKind.PERMANENT, retryable=False)

    def list_owned_channels(self, access_token: str) -> list[ChannelSummary]:
        self._check_gate()
        url = f"{self.api_base_url}/youtube/v3/channels"
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.get(
                    url,
                    params={"part": "snippet,statistics", "mine": "true"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error querying channels: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code != 200:
            raise _map_http_error(resp, "list_owned_channels")

        data = resp.json() or {}
        items = data.get("items", [])
        results: list[ChannelSummary] = []
        for item in items:
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            sub_count = int(stats["subscriberCount"]) if stats.get("subscriberCount") else None
            results.append(
                ChannelSummary(
                    channel_id=item["id"],
                    title=snippet.get("title", ""),
                    handle=snippet.get("customUrl"),
                    subscriber_count=sub_count,
                    is_brand_candidate=False,
                )
            )
        return results

    def start_resumable_upload(
        self,
        access_token: str,
        *,
        metadata: dict[str, Any],
        total_bytes: int,
        content_type: str,
        endpoint: str,
    ) -> UploadStart:
        self._check_gate()
        target_endpoint = endpoint
        if self.upload_base_url != "https://www.googleapis.com" and target_endpoint.startswith(
            "https://www.googleapis.com"
        ):
            target_endpoint = (
                self.upload_base_url + target_endpoint[len("https://www.googleapis.com") :]
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(total_bytes),
            "X-Upload-Content-Type": content_type,
        }
        with httpx.Client(timeout=60.0) as client:
            try:
                resp = client.post(
                    target_endpoint,
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    json=metadata,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error starting upload: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code != 200:
            raise _map_http_error(resp, "start_resumable_upload")

        location = resp.headers.get("Location")
        if not location:
            raise ConnectorError(
                "200 response carried no Location header",
                kind=FailureKind.PERMANENT,
                retryable=False,
            )
        return UploadStart(session_uri=location)

    def probe_upload(self, access_token: str, session_uri: str, total_bytes: int) -> ProbeResult:
        self._check_gate()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_bytes}",
        }
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.put(session_uri, content=b"", headers=headers)
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error probing upload: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code in (200, 201):
            body = resp.json() or {}
            video = _parse_video(body)
            return ProbeResult(bytes_received=total_bytes, completed=True, video=video)

        if resp.status_code == 308:
            high = _parse_range_header(resp.headers.get("Range"))
            received = high + 1 if high >= 0 else 0
            return ProbeResult(bytes_received=received, completed=False)

        if resp.status_code == 404:
            raise ConnectorError(
                "The resumable session has expired (HTTP 404).",
                kind=FailureKind.AMBIGUOUS,
                retryable=False,
            )

        raise _map_http_error(resp, "probe_upload")

    def perform_upload(
        self,
        access_token: str,
        *,
        session_uri: str,
        source_path: str,
        total_bytes: int,
        content_type: str,
        start_offset: int,
        on_progress: Any = None,
        chunk_size: int = 8 * 1024 * 1024,
    ) -> UploadOutcome:
        self._check_gate()
        path = Path(source_path)
        if not path.is_file():
            raise ConnectorError(
                f"Source file does not exist: {path}",
                kind=FailureKind.VALIDATION,
                retryable=False,
            )

        offset = start_offset

        with httpx.Client(timeout=120.0) as client:
            while offset < total_bytes:
                first = offset
                last = min(first + chunk_size, total_bytes) - 1
                length = last - first + 1

                with path.open("rb") as fh:
                    fh.seek(first)
                    chunk_data = fh.read(length)

                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(len(chunk_data)),
                    "Content-Type": content_type,
                    "Content-Range": f"bytes {first}-{last}/{total_bytes}",
                }

                try:
                    resp = client.put(session_uri, content=chunk_data, headers=headers)
                except httpx.HTTPError as exc:
                    is_last = last == total_bytes - 1
                    if is_last:
                        return UploadOutcome(video_id="", bytes_sent=total_bytes, ambiguous=True)
                    raise ConnectorError(
                        f"Network error during upload chunk: {exc}",
                        kind=FailureKind.TRANSIENT,
                        retryable=True,
                    ) from exc

                if resp.status_code in (200, 201):
                    body = resp.json() or {}
                    video_id = body.get("id") or ""
                    if on_progress:
                        on_progress(total_bytes, total_bytes)
                    return UploadOutcome(video_id=video_id, bytes_sent=total_bytes, ambiguous=False)

                if resp.status_code == 308:
                    high = _parse_range_header(resp.headers.get("Range"))
                    new_offset = high + 1 if high >= 0 else first + len(chunk_data)
                    offset = new_offset
                    if on_progress:
                        on_progress(offset, total_bytes)
                    continue

                if resp.status_code == 404:
                    raise ConnectorError(
                        "Resumable session expired during upload (HTTP 404).",
                        kind=FailureKind.AMBIGUOUS,
                        retryable=False,
                    )

                raise _map_http_error(resp, "perform_upload")

        return UploadOutcome(video_id="", bytes_sent=offset, ambiguous=True)

    def get_video(self, access_token: str, video_id: str) -> VideoStatus | None:
        self._check_gate()
        url = f"{self.api_base_url}/youtube/v3/videos"
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.get(
                    url,
                    params={"part": "snippet,status,processingDetails", "id": video_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error getting video: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise _map_http_error(resp, "get_video")

        items = (resp.json() or {}).get("items", [])
        if not items:
            return None
        return _parse_video(items[0])

    def list_recent_upload_ids(self, access_token: str, channel_id: str) -> list[str]:
        self._check_gate()
        channel_url = f"{self.api_base_url}/youtube/v3/channels"
        with httpx.Client(timeout=30.0) as client:
            try:
                c_resp = client.get(
                    channel_url,
                    params={"part": "contentDetails", "id": channel_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if c_resp.status_code != 200:
                    return []
                items = (c_resp.json() or {}).get("items", [])
                if not items:
                    return []
                uploads_playlist = (
                    items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                )
                if not uploads_playlist:
                    return []

                p_resp = client.get(
                    f"{self.api_base_url}/youtube/v3/playlistItems",
                    params={
                        "part": "contentDetails",
                        "playlistId": uploads_playlist,
                        "maxResults": "10",
                    },
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if p_resp.status_code != 200:
                    return []
                p_items = (p_resp.json() or {}).get("items", [])
                return [
                    item.get("contentDetails", {}).get("videoId")
                    for item in p_items
                    if item.get("contentDetails", {}).get("videoId")
                ]
            except Exception:
                return []

    def update_video_status(
        self,
        access_token: str,
        video_id: str,
        *,
        privacy: str,
        publish_at: datetime | None,
        full_snippet: dict[str, Any],
    ) -> VideoStatus:
        self._check_gate()
        url = f"{self.api_base_url}/youtube/v3/videos"
        status_payload: dict[str, Any] = {"privacyStatus": privacy}
        if publish_at:
            status_payload["publishAt"] = publish_at.isoformat()

        body = {
            "id": video_id,
            "snippet": full_snippet,
            "status": status_payload,
        }
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.put(
                    url,
                    params={"part": "snippet,status"},
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error updating video status: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code != 200:
            raise _map_http_error(resp, "update_video_status")

        return _parse_video(resp.json() or {})

    def delete_video(self, access_token: str, video_id: str) -> bool:
        self._check_gate()
        url = f"{self.api_base_url}/youtube/v3/videos"
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.delete(
                    url,
                    params={"id": video_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                return resp.status_code in (200, 204)
            except Exception as exc:
                raise ConnectorError(
                    f"Network error deleting video: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

    def refresh(self, refresh_token: str) -> TokenSet:
        self._check_gate()
        if not self.client_id:
            raise ConnectorError(
                "Google Client ID not configured on connector.",
                kind=FailureKind.AUTHORIZATION,
                retryable=False,
            )
        data = {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.post(f"{self.oauth_base_url}/token", data=data)
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"Network error refreshing token: {exc}",
                    kind=FailureKind.TRANSIENT,
                    retryable=True,
                ) from exc

        if resp.status_code != 200:
            raise _map_http_error(resp, "token refresh")

        body = resp.json() or {}
        access_token = body.get("access_token")
        if not access_token:
            raise ConnectorError(
                "Token refresh returned no access_token",
                kind=FailureKind.AUTHORIZATION,
                retryable=False,
            )
        return TokenSet(
            access_token=access_token,
            refresh_token=body.get("refresh_token") or refresh_token,
            expires_in=body.get("expires_in"),
        )

    def revoke(self, token: str) -> bool:
        self._check_gate()
        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.post(f"{self.oauth_base_url}/revoke", params={"token": token})
                return resp.status_code == 200
            except Exception:
                return False
