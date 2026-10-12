"""The real YouTube connector.

**Not wired in Phase 1.** Every method raises :class:`ConnectorNotAvailable` with the
specific gate that has to pass first. That is deliberate and is the honest state of
the project after Phase 0: ``FEAS-01`` … ``FEAS-05`` are all ``blocked`` pending a
Cloud project, a consent configuration and a test channel
(``08-phase0-feasibility-report.md`` §6).

What Phase 2 adds here, without changing the interface:

* ``httpx`` for the resumable protocol, because it must persist and re-read a session
  URI across a process restart (FEAS-04).
* ``google-api-python-client`` for the supported non-upload reads (identity,
  ``videos.list``), where the official client is the documented path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hexton.connectors.base import (
    ChannelSummary,
    ConnectorNotAvailable,
    PlatformConnector,
    ProbeResult,
    TokenSet,
    UploadOutcome,
    UploadStart,
    VideoStatus,
)

_GATE = (
    "The real YouTube connector is not enabled in this build. Phase 0 recorded "
    "FEAS-01..FEAS-05 as blocked pending a Google Cloud project, an OAuth consent "
    "configuration, and a test channel; wiring the live connector before those pass "
    "would put an unverified integration on the critical path. See "
    "08-phase0-feasibility-report.md §9 for the prerequisites."
)


class YouTubeConnector(PlatformConnector):
    """Placeholder that fails loudly rather than silently doing nothing."""

    provider = "youtube"

    def list_owned_channels(self, access_token: str) -> list[ChannelSummary]:
        raise ConnectorNotAvailable(_GATE)

    def start_resumable_upload(
        self,
        access_token: str,
        *,
        metadata: dict[str, Any],
        total_bytes: int,
        content_type: str,
        endpoint: str,
    ) -> UploadStart:
        raise ConnectorNotAvailable(_GATE)

    def probe_upload(self, access_token: str, session_uri: str, total_bytes: int) -> ProbeResult:
        raise ConnectorNotAvailable(_GATE)

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
        raise ConnectorNotAvailable(_GATE)

    def get_video(self, access_token: str, video_id: str) -> VideoStatus | None:
        raise ConnectorNotAvailable(_GATE)

    def list_recent_upload_ids(self, access_token: str, channel_id: str) -> list[str]:
        raise ConnectorNotAvailable(_GATE)

    def update_video_status(
        self,
        access_token: str,
        video_id: str,
        *,
        privacy: str,
        publish_at: datetime | None,
        full_snippet: dict[str, Any],
    ) -> VideoStatus:
        raise ConnectorNotAvailable(_GATE)

    def delete_video(self, access_token: str, video_id: str) -> bool:
        raise ConnectorNotAvailable(_GATE)

    def refresh(self, refresh_token: str) -> TokenSet:
        raise ConnectorNotAvailable(_GATE)

    def revoke(self, token: str) -> bool:
        raise ConnectorNotAvailable(_GATE)
