"""A deterministic in-process connector for tests and first-run development.

Two rules make this safe:

* It is selected only when ``connector_kind == "fake"``, which
  :func:`hexton.connectors.registry.select_connector` refuses to honour in a packaged
  build. There is no runtime flag anywhere else that can reach it.
* It performs **no network I/O of any kind**. Every behaviour is a function of the
  state a test set up, so a failing test is always about the application.

It also models the properties the worker must survive, which a naive mock would not:

* an accepted upload whose response is lost (``ambiguous=True``),
* a transient failure that succeeds on retry,
* a permanent failure that must never be retried,
* an authorization failure that must not be retried at all.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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


def _default_channels() -> list[ChannelSummary]:
    """One channel, so a fresh install using the fake connector is usable.

    Left empty, ``list_owned_channels`` returned nothing, no identity could ever be
    created, and *every* job was blocked with "no verified channel identity is
    selected". The safety rule was firing correctly — against a stand-in that had
    nothing to stand in for. Tests that need a different account pass an explicit
    ``FakeState``; this is only what a first run starts with.
    """
    return [
        ChannelSummary(
            channel_id="UCxxxxxxxxxxxxxxxxxxxxxx",
            title="Hexton test channel",
            handle="@hexton-test",
            subscriber_count=0,
        )
    ]


@dataclass
class FakeState:
    """Everything a test can configure. Mutated directly by the test."""

    channels: list[ChannelSummary] = field(default_factory=_default_channels)
    #: video_id -> status, as the "provider" currently sees it.
    videos: dict[str, VideoStatus] = field(default_factory=dict)
    #: Ordered recent-upload IDs returned by ``list_recent_upload_ids``.
    recent_upload_ids: list[str] = field(default_factory=list)

    #: Fail the next N calls of a named operation with this error.
    fail_next: dict[str, int] = field(default_factory=dict)
    fail_kind: dict[str, FailureKind] = field(default_factory=dict)
    #: Operation names that always raise, e.g. ``{"get_video": FailureKind.AUTHORIZATION}``.
    fail_always: dict[str, FailureKind] = field(default_factory=dict)

    #: Simulate "the body landed but the response never arrived".
    drop_upload_response: bool = False
    #: Auto-create the video once a full upload has been transferred.
    auto_create_video: bool = True
    #: Videos created here, in order, so a test can prove only one exists.
    created_video_ids: list[str] = field(default_factory=list)


class FakeConnector(PlatformConnector):
    """A stand-in with the same contract as the real connector."""

    provider = "fake"
    #: It simulates. Nothing it reports has happened anywhere, and the UI says so.
    reaches_platform = False

    def __init__(self, state: FakeState | None = None) -> None:
        self.state = state if state is not None else FakeState()
        self._counter = itertools.count(1)
        self._sessions: dict[str, dict[str, Any]] = {}
        #: Every method call, recorded so tests can assert on the call sequence.
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # -- plumbing -----------------------------------------------------------

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def _maybe_fail(self, name: str) -> None:
        if name in self.state.fail_always:
            raise ConnectorError(
                f"{name} is configured to fail permanently",
                kind=self.state.fail_always[name],
                retryable=False,
            )
        remaining = self.state.fail_next.get(name, 0)
        if remaining > 0:
            self.state.fail_next[name] = remaining - 1
            kind = self.state.fail_kind.get(name, FailureKind.TRANSIENT)
            raise ConnectorError(
                f"{name} failed (transient, {remaining - 1} more configured)",
                kind=kind,
                retryable=kind is FailureKind.TRANSIENT,
            )

    def call_count(self, name: str) -> int:
        return sum(1 for c in self.calls if c[0] == name)

    # -- identity ------------------------------------------------------------

    def list_owned_channels(self, access_token: str) -> list[ChannelSummary]:
        self._record("list_owned_channels", access_token)
        self._maybe_fail("list_owned_channels")
        return list(self.state.channels)

    # -- resumable session ---------------------------------------------------

    def start_resumable_upload(
        self,
        access_token: str,
        *,
        metadata: dict[str, Any],
        total_bytes: int,
        content_type: str,
        endpoint: str,
    ) -> UploadStart:
        self._record("start_resumable_upload", metadata=metadata, total_bytes=total_bytes)
        self._maybe_fail("start_resumable_upload")

        status = (metadata.get("status") or {}) if isinstance(metadata, dict) else {}
        privacy = status.get("privacyStatus")
        if not privacy:
            # DF-02: omitting privacyStatus is the exact failure mode this product
            # exists to prevent, so the stand-in refuses it too.
            raise ConnectorError(
                "status.privacyStatus must be sent explicitly; it is never defaulted.",
                kind=FailureKind.VALIDATION,
                retryable=False,
            )
        if status.get("publishAt") and privacy != "private":
            # DF-03
            raise ConnectorError(
                "status.publishAt requires status.privacyStatus=private",
                kind=FailureKind.VALIDATION,
                retryable=False,
            )

        sid = uuid.uuid4().hex
        self._sessions[sid] = {
            "total": total_bytes,
            "received": 0,
            "metadata": metadata,
            "content_type": content_type,
        }
        return UploadStart(session_uri=f"fake://upload/{sid}")

    def _session(self, session_uri: str) -> dict[str, Any]:
        sid = session_uri.rsplit("/", 1)[-1]
        if sid not in self._sessions:
            raise ConnectorError(
                "The resumable session has expired (404). Restarting from byte zero "
                "could create a duplicate video.",
                kind=FailureKind.AMBIGUOUS,
                retryable=False,
            )
        return self._sessions[sid]

    def probe_upload(self, access_token: str, session_uri: str, total_bytes: int) -> ProbeResult:
        self._record("probe_upload", session_uri, total_bytes)
        self._maybe_fail("probe_upload")
        sess = self._session(session_uri)
        if sess["received"] >= sess["total"]:
            return ProbeResult(bytes_received=sess["total"], completed=True)
        return ProbeResult(bytes_received=sess["received"])

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
    ) -> UploadOutcome:
        self._record("perform_upload", session_uri, start_offset=start_offset)
        self._maybe_fail("perform_upload")

        sess = self._session(session_uri)
        # A provider that has already received the body reports it; the client must
        # trust the session rather than re-sending (DF-14).
        offset = max(start_offset, sess["received"])
        if offset < sess["total"]:
            sess["received"] = sess["total"]
        if on_progress:
            on_progress(sess["received"], sess["total"])

        if sess["received"] < sess["total"]:
            raise ConnectorError(
                "transfer stopped before the body was complete",
                kind=FailureKind.TRANSIENT,
                retryable=True,
            )

        if self.state.drop_upload_response:
            # The bytes landed; the answer never came back. This is the case the whole
            # duplicate-prevention design exists for.
            return UploadOutcome(video_id="", bytes_sent=sess["total"], ambiguous=True)

        vid = f"fake-{next(self._counter):04d}"
        if self.state.auto_create_video:
            self.state.videos[vid] = VideoStatus(
                video_id=vid,
                privacy_status=(sess["metadata"].get("status") or {}).get(
                    "privacyStatus", "private"
                ),
                upload_status="uploaded",
                processing_status="processing",
                publish_at=_parse_iso((sess["metadata"].get("status") or {}).get("publishAt")),
            )
            self.state.created_video_ids.append(vid)
        return UploadOutcome(video_id=vid, bytes_sent=sess["total"])

    # -- read-back -----------------------------------------------------------

    def get_video(self, access_token: str, video_id: str) -> VideoStatus | None:
        self._record("get_video", video_id)
        self._maybe_fail("get_video")
        return self.state.videos.get(video_id)

    def list_recent_upload_ids(self, access_token: str, channel_id: str) -> list[str]:
        self._record("list_recent_upload_ids", channel_id)
        self._maybe_fail("list_recent_upload_ids")
        return list(self.state.recent_upload_ids)

    # -- publication changes --------------------------------------------------

    def update_video_status(
        self,
        access_token: str,
        video_id: str,
        *,
        privacy: str,
        publish_at: datetime | None,
        full_snippet: dict[str, Any],
    ) -> VideoStatus:
        self._record("update_video_status", video_id, privacy=privacy, publish_at=publish_at)
        self._maybe_fail("update_video_status")
        current = self.state.videos.get(video_id)
        if current is None:
            raise ConnectorError(
                f"video {video_id} does not resolve",
                kind=FailureKind.PERMANENT,
                retryable=False,
            )
        if current.privacy_status == "public":
            # DF-03
            raise ConnectorError(
                "publishAt applies only to private videos that have never been "
                "published; this video is already public.",
                kind=FailureKind.PERMANENT,
                retryable=False,
            )
        if publish_at is not None and privacy != "private":
            raise ConnectorError(
                "status.publishAt requires status.privacyStatus=private",
                kind=FailureKind.VALIDATION,
                retryable=False,
            )
        updated = VideoStatus(
            video_id=current.video_id,
            privacy_status=privacy,
            upload_status="uploaded",
            processing_status=None,
            publish_at=publish_at,
            title=(full_snippet or {}).get("title", current.title),
        )
        self.state.videos[video_id] = updated
        return updated

    def delete_video(self, access_token: str, video_id: str) -> bool:
        self._record("delete_video", video_id)
        self._maybe_fail("delete_video")
        return self.state.videos.pop(video_id, None) is not None

    # -- authorization lifecycle ---------------------------------------------

    def refresh(self, refresh_token: str) -> TokenSet:
        self._record("refresh", refresh_token[:4] + "…")
        self._maybe_fail("refresh")
        if not refresh_token:
            raise ConnectorError(
                "No refresh token is stored for this connection. Under a consent "
                "screen in Testing status, refresh tokens expire after seven days "
                "(DF-17); reconnect the channel.",
                kind=FailureKind.AUTHORIZATION,
                retryable=False,
            )
        return TokenSet(access_token="fake-access", refresh_token=refresh_token, expires_in=3600)

    def revoke(self, token: str) -> bool:
        self._record("revoke", token[:4] + "…")
        self._maybe_fail("revoke")
        return True

    # -- test helpers ---------------------------------------------------------

    def set_video_public(self, video_id: str) -> None:
        """Flip a scheduled video to public, as YouTube would at publish time."""
        current = self.state.videos[video_id]
        self.state.videos[video_id] = VideoStatus(
            video_id=current.video_id,
            privacy_status="public",
            upload_status="uploaded",
            processing_status=None,
            publish_at=current.publish_at,
            title=current.title,
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
