"""Pydantic contracts for the local API.

Two rules run through this module:

* **No secret ever appears in a response schema.** Not an access token, not a refresh
  token, not a client secret, not a credential-store key. A connection is described by
  what it can do and how healthy it is — never by the material that makes it work.
* **Times cross the boundary as UTC with an offset**, alongside the operator's zone,
  so the frontend never has to guess a zone and the machine's zone changing cannot
  silently shift a displayed publication time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Privacy = Literal["private", "unlisted", "public"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------
# Session / health
# ----------------------------------------------------------------------


class SessionOut(BaseModel):
    csrf_token: str
    expires_in: int


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: bool
    worker_alive: bool
    connector: str
    credential_store: str
    profile: str


class VersionOut(BaseModel):
    version: str
    api: int


# ----------------------------------------------------------------------
# Media
# ----------------------------------------------------------------------


class MediaIssueOut(BaseModel):
    code: str
    message: str
    blocking: bool


class MediaCheckOut(BaseModel):
    path: str
    exists: bool
    readable: bool
    size_bytes: int
    sha256: str | None
    container: str | None
    brand: str | None
    duration_seconds: float | None
    content_type: str
    issues: list[MediaIssueOut]
    checked_at: datetime


class MediaAddIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class MediaOut(ORMModel):
    id: int
    path: str
    original_name: str
    sha256: str
    size_bytes: int
    duration_seconds: float | None
    container: str | None
    is_valid: bool
    validated_at: datetime | None
    verified_at: datetime | None


# ----------------------------------------------------------------------
# Connections and identities
# ----------------------------------------------------------------------


class ChannelOut(ORMModel):
    id: int
    channel_id: str
    title: str
    handle: str | None
    is_brand_candidate: bool
    subscriber_count: int | None
    verification_source: str
    verified_at: datetime | None
    operator_confirmed_at: datetime | None


class ConnectionOut(BaseModel):
    """Deliberately carries no token material of any kind."""

    id: int
    public_id: str
    provider: str
    label: str
    status: str
    scopes: list[str]
    granted_at: datetime | None
    token_expires_at: datetime | None
    last_token_check_at: datetime | None
    last_token_check_ok: bool | None
    revoked_at: datetime | None
    authorized_data_deleted_at: datetime | None
    channels: list[ChannelOut] = []


class ConnectionCreateIn(BaseModel):
    provider: Literal["youtube"] = "youtube"
    label: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list)


class ChannelConfirmIn(BaseModel):
    channel_identity_id: int


# ----------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------


class JobFields(BaseModel):
    """Everything an operator can set on a job, shared by create and update."""

    media_id: int
    title: str = Field(min_length=1, max_length=400)
    description: str = Field(default="", max_length=20000)
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    category_id: str = "22"
    privacy: Privacy = "private"
    connection_id: int | None = None
    channel_identity_id: int | None = None
    publish_at_utc: datetime | None = None
    display_timezone: str | None = None
    min_lead_minutes: int | None = None
    mention_requested: bool = False
    consent_transforms: bool = False
    #: Optional. A client that retries a create it never saw a response to can resend
    #: the same key and get the original job back rather than a second copy. Left
    #: blank, one is minted — so the column is never NULL, which is what the unique
    #: constraint in ``models.py`` assumes.
    idempotency_key: str | None = Field(default=None, max_length=64)

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A title is required.")
        return value


class JobCreateIn(JobFields):
    queue: bool = False


class JobUpdateIn(JobFields):
    pass


class IssueOut(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"]


class ValidateOut(BaseModel):
    ok: bool
    issues: list[IssueOut]
    effective_publish_at_utc: datetime | None
    schedule_issues: list[IssueOut] = []


class JobOut(BaseModel):
    id: int
    public_id: str
    state: str
    display_label: str
    display_cue: str
    primary_action: str
    can_retry: bool
    can_cancel: bool

    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    category_id: str
    privacy: str
    mention_requested: bool
    mention_state: str
    mention_reason: str | None
    mention_handles: list[str]
    mention_confirmed: bool

    media_id: int
    media_path: str
    media_name: str
    media_size_bytes: int
    media_duration_seconds: float | None
    media_container: str | None

    connection_id: int | None
    channel_identity_id: int | None
    channel_title: str | None
    channel_id: str | None

    publish_at_utc: datetime | None
    display_timezone: str
    publish_at_display: str | None
    uploaded_at: datetime | None
    uploaded_at_display: str | None

    remote_state: str
    remote_privacy_status: str | None
    remote_processing_status: str | None
    poll_result: str | None
    last_polled_at: datetime | None
    awaiting_verification: bool

    progress_percent: int
    video_id: str | None
    video_url: str | None

    failure_kind: str | None
    failure_message: str | None
    attempt: int
    max_attempts: int
    next_attempt_at: datetime | None
    next_attempt_display: str | None
    insert_may_have_succeeded: bool

    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None


class JobDetailOut(BaseModel):
    job: JobOut
    events: list[EventOut]
    observations: list[ObservationOut]
    upload_sessions: list[UploadSessionOut]
    allowed_transitions: list[str]


class EventOut(BaseModel):
    id: int
    occurred_at: datetime
    kind: str
    message: str
    payload: dict[str, Any] | None


class ObservationOut(BaseModel):
    id: int
    observed_at: datetime
    video_id: str | None
    remote_state: str
    privacy_status: str | None
    upload_status: str | None
    processing_status: str | None
    publish_at: datetime | None


class UploadSessionOut(BaseModel):
    id: int
    phase: str
    bytes_uploaded: int
    total_bytes: int
    attempt: int
    insert_may_have_succeeded: bool
    created_at: datetime


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    states: dict[str, int]


class CancelIn(BaseModel):
    delete_remote: bool = False
    reason: str = ""


class RescheduleIn(BaseModel):
    publish_at_utc: datetime


class ReconcileIn(BaseModel):
    video_id: str | None = None


# ----------------------------------------------------------------------
# Settings and dashboard
# ----------------------------------------------------------------------


class SettingsOut(BaseModel):
    display_timezone: str
    min_lead_minutes: int
    warning_lead_minutes: int
    missed_grace_minutes: int
    lease_seconds: int
    worker_poll_seconds: float
    retention_days: int
    connector_kind: str
    credential_store_kind: str
    data_dir: str
    quota_daily_units: int
    quota_used_today: int
    quota_remaining: int
    quota_resets_at: datetime


class DashboardOut(BaseModel):
    total: int
    by_state: dict[str, int]
    upcoming: list[JobOut]
    needs_attention: list[JobOut]
    uploading: list[JobOut]
    quota_remaining: int
    worker_alive: bool
