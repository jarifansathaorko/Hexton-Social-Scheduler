"""The connector interface.

This is the only boundary between the application and Google. Nothing above this
module imports ``httpx``, ``googleapiclient`` or builds a Google URL. That makes the
whole worker testable with :class:`~hexton.connectors.fake.FakeConnector`, and it
makes "is the real connector wired yet?" a one-line answer.

Errors are classified at the boundary. Every :class:`ConnectorError` carries a
:class:`~hexton.domain.jobstate.FailureKind`, so the worker's retry policy is applied
uniformly instead of being scattered through network code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hexton.domain.jobstate import FailureKind


class ConnectorError(RuntimeError):
    """A provider call failed. Always carries a retry classification."""

    def __init__(self, message: str, *, kind: FailureKind, retryable: bool) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class ConnectorNotAvailable(ConnectorError):
    """The requested connector is not wired in this build."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind=FailureKind.PERMANENT, retryable=False)


@dataclass(frozen=True)
class ChannelSummary:
    """A channel the authorizing credential can publish to."""

    channel_id: str
    title: str
    handle: str | None = None
    subscriber_count: int | None = None
    #: True when this identity is believed to manage a Brand Account. Unverified:
    #: see DF-18 / DF-27, and FEAS-01 remains open.
    is_brand_candidate: bool = False


@dataclass(frozen=True)
class VideoStatus:
    """The subset of a video resource this application actually reasons about."""

    video_id: str
    privacy_status: str
    upload_status: str
    processing_status: str | None = None
    publish_at: datetime | None = None
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadStart:
    """Result of opening a resumable session."""

    session_uri: str


@dataclass(frozen=True)
class ProbeResult:
    """Result of a ``Content-Range: bytes */TOTAL`` status probe."""

    #: Highest byte index confirmed received, or ``-1`` when nothing has landed.
    bytes_received: int
    completed: bool = False
    video: VideoStatus | None = None


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class UploadOutcome:
    """What a completed transfer produced."""

    video_id: str
    bytes_sent: int
    #: True when the provider's response was never received even though the body may
    #: have landed. The worker must reconcile, and must never issue a second insert.
    ambiguous: bool = False


class PlatformConnector(ABC):
    """What this application needs from YouTube.

    Deliberately narrow. ``01`` §12 forbids the app from becoming a general upload
    client, and the resumable transport keeps its session state in SQLite across a
    process restart (FEAS-04), so a session survives the process that created it.
    """

    provider: str

    # -- identity ------------------------------------------------------------

    @abstractmethod
    def list_owned_channels(self, access_token: str) -> list[ChannelSummary]:
        """``channels.list(mine=true)``. The documented identity check (FEAS-01)."""

    # -- resumable session ---------------------------------------------------

    @abstractmethod
    def start_resumable_upload(
        self,
        access_token: str,
        *,
        metadata: dict[str, Any],
        total_bytes: int,
        content_type: str,
        endpoint: str,
    ) -> UploadStart:
        """Open a session. Must send ``privacyStatus`` explicitly and ``publishAt``
        inside this body, never as a follow-up update (``DF-02``/``DF-03``/``C-02``)."""

    @abstractmethod
    def probe_upload(self, access_token: str, session_uri: str, total_bytes: int) -> ProbeResult:
        """Ask how much of the body the provider already has."""

    @abstractmethod
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
        """Transfer the remaining bytes and return the created video.

        Phase 1 ships a deterministic in-memory implementation. Phase 2 replaces it
        with the first-party ``httpx`` resumable client without changing this
        signature, because the client already persists its session and offset in
        ``UploadSession``.
        """

    # -- read-back -----------------------------------------------------------

    @abstractmethod
    def get_video(self, access_token: str, video_id: str) -> VideoStatus | None:
        """``videos.list(id=...)``. Returns ``None`` when the ID does not resolve."""

    @abstractmethod
    def list_recent_upload_ids(self, access_token: str, channel_id: str) -> list[str]:
        """Candidate video IDs from the documented reconciliation ladder (R2/R3).

        Evidence only. Never sufficient on its own to adopt a video ID: an unrelated
        manual upload at the same moment is indistinguishable.
        """

    # -- publication changes --------------------------------------------------

    @abstractmethod
    def update_video_status(
        self,
        access_token: str,
        video_id: str,
        *,
        privacy: str,
        publish_at: datetime | None,
        full_snippet: dict[str, Any],
    ) -> VideoStatus:
        """Reschedule or change visibility on an existing video.

        Two constraints are load-bearing and are enforced by the caller before this
        is reached:

        * ``DF-06`` — ``videos.update`` **deletes** any already-valued property that
          is omitted from the request, so ``full_snippet`` must carry every snippet
          field. There is deliberately no way to send a partial one.
        * ``DF-03`` — ``publishAt`` applies only to a video that has never been
          published. The caller refuses rather than sending a request that will be
          silently ignored.
        """

    @abstractmethod
    def delete_video(self, access_token: str, video_id: str) -> bool:
        """Remove a video from YouTube. Used only by an explicit operator cancellation."""

    # -- authorization lifecycle ---------------------------------------------

    @abstractmethod
    def refresh(self, refresh_token: str) -> TokenSet:
        """Exchange a refresh token for a new access token."""

    @abstractmethod
    def revoke(self, token: str) -> bool:
        """Programmatically revoke. COMP-04 requires this the moment the user asks,
        and requires deleting Authorized Data within 7 calendar days (``DF-20``)."""
