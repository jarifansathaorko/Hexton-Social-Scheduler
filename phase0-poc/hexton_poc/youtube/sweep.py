"""PublishConfirmationSweep: make publication *verified* rather than assumed.

``02-technical-prd.md`` §6.3. Delegating publication to YouTube means the app cannot know
whether publication happened unless it asks. The sweep is what separates four conditions
that must never be conflated:

1. upload completed       - media accepted, video ID known, processing not confirmed
2. scheduled publication  - YouTube accepted a future publish time
3. published (verified)   - remote status observed as ``public``
4. publication awaiting verification - the publish time passed with no confirming poll

Condition 4 is a normal, non-error state. It appears whenever the PC was off at the
publication instant, which for a local Windows app is the common case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..jobstate import JobState, PollResult, RemoteState
from .reconcile import VIDEOS_LIST

MIN_POLL_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class SweepObservation:
    video_id: str
    remote_state: RemoteState
    poll_result: PollResult
    privacy_status: str
    publish_at_utc: str
    upload_status: str
    processing_status: str
    published_at_utc: str
    job_state: JobState
    display_suffix: str = ""
    reason: str = ""


def classify(
    resource: dict | None,
    *,
    now: datetime,
    expected_publish_at_utc: str | None = None,
    last_polled_at_utc: str | None = None,
    token_valid: bool = True,
) -> SweepObservation:
    """Pure classification. No I/O, so the whole decision table is unit-testable."""
    video_id = (resource or {}).get("id", "")

    if not token_valid:
        return SweepObservation(
            video_id, RemoteState.UNKNOWN, PollResult.UNKNOWN, "", "", "", "", "",
            JobState.BLOCKED,
            reason="The connection's token is no longer valid, so publication could not be "
                   "verified. The job stays unverified rather than being assumed successful.")

    if resource is None:
        return SweepObservation(
            video_id, RemoteState.UNKNOWN, PollResult.NEVER_POLLED, "", "", "", "", "",
            JobState.QUEUED,
            reason="No remote observation has been made for this video yet.")

    status = resource.get("status", {}) or {}
    privacy = (status.get("privacyStatus") or "").lower()
    publish_at = status.get("publishAt", "") or ""
    upload_status = (status.get("uploadStatus") or "").lower()

    remote_state = {
        "public": RemoteState.PUBLIC,
        "private": RemoteState.PRIVATE_UNLISTED_OR_OTHER,
        "unlisted": RemoteState.PRIVATE_UNLISTED_OR_OTHER,
    }.get(privacy, RemoteState.UNKNOWN)

    if upload_status == "failed":
        return SweepObservation(
            video_id, RemoteState.FAILED, PollResult.CONFIRMED, privacy, publish_at,
            upload_status, "", "", JobState.FAILED,
            reason=f"YouTube reports uploadStatus=failed"
                   f"{(' with failureReason=' + str(status.get('failureReason'))) if status.get('failureReason') else ''}.")

    if privacy == "public":
        return SweepObservation(
            video_id, RemoteState.PUBLIC, PollResult.CONFIRMED, privacy, publish_at,
            upload_status, "", (resource.get("snippet") or {}).get("publishedAt", ""),
            JobState.PUBLISHED,
            reason="Remote privacyStatus observed as public.")

    # Not public yet. Is it scheduled, and has the time passed?
    publish_passed = False
    if publish_at:
        try:
            publish_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            publish_passed = publish_dt <= now.astimezone(timezone.utc)
        except ValueError:
            publish_passed = False

    if publish_at and not publish_passed:
        return SweepObservation(
            video_id, RemoteState.SCHEDULED, PollResult.NOT_YET, privacy, publish_at,
            upload_status, "", "", JobState.SCHEDULED,
            reason="A future publishAt is set and the remote resource has accepted it.")

    if publish_at and publish_passed:
        # The publish time has passed but the video is not public. Either YouTube has not
        # acted yet, or we cannot tell. Either way this is *awaiting verification*.
        return SweepObservation(
            video_id, RemoteState.PRIVATE_UNLISTED_OR_OTHER, PollResult.NOT_YET, privacy,
            publish_at, upload_status, "", "", JobState.SCHEDULED,
            display_suffix="— publication awaiting verification",
            reason="The publish time has passed but the remote status is not yet public. "
                   "Reported as awaiting verification rather than as success or failure.")

    return SweepObservation(
        video_id, remote_state, PollResult.CONFIRMED, privacy, publish_at, upload_status,
        "", "", JobState.UPLOADED,
        reason="Uploaded with no publishAt set; the video is not public.")


class PublishConfirmationSweep:
    """Polls remote status with the documented, 1-unit ``videos.list`` call."""

    def __init__(self, *, access_token_provider, client: httpx.Client,
                 min_interval_seconds: int = MIN_POLL_INTERVAL_SECONDS,
                 base_url: str = "https://www.googleapis.com/youtube/v3") -> None:
        self._token = access_token_provider
        self._client = client
        self._min_interval = min_interval_seconds
        self._base = base_url.rstrip("/")

    def should_poll(self, last_polled_at_utc: str | None, now: datetime) -> bool:
        if not last_polled_at_utc:
            return True
        try:
            last = datetime.fromisoformat(last_polled_at_utc.replace("Z", "+00:00"))
        except ValueError:
            return True
        return (now.astimezone(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() \
            >= self._min_interval

    def poll(self, video_id: str, *, expected_publish_at_utc: str | None = None,
             now: datetime | None = None, token_valid: bool = True) -> SweepObservation:
        now = now or datetime.now(timezone.utc)
        if not token_valid:
            return classify(None, now=now, token_valid=False)

        resp = self._client.get(
            f"{self._base}/videos",
            params={"part": "id,snippet,status", "id": video_id},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if resp.status_code == 401:
            return classify(None, now=now, token_valid=False)
        if resp.status_code != 200:
            return SweepObservation(
                video_id, RemoteState.UNKNOWN, PollResult.UNKNOWN, "", "", "", "", "",
                JobState.SCHEDULED,
                display_suffix="— publication awaiting verification",
                reason=f"videos.list returned HTTP {resp.status_code}; publication could not "
                       "be verified on this pass.")
        items = (resp.json() or {}).get("items", [])
        if not items:
            return SweepObservation(
                video_id, RemoteState.UNKNOWN, PollResult.UNKNOWN, "", "", "", "", "",
                JobState.NEEDS_REVIEW,
                reason="The recorded video ID no longer resolves to a video. Either it was "
                       "deleted, or the ID is wrong. Routed to manual review.")
        return classify(items[0], now=now, expected_publish_at_utc=expected_publish_at_utc)
