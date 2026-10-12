"""SQLAlchemy 2.x typed persistence models.

One canonical schema for the whole application. The rules these tables encode:

* **Tokens never live here.** A ``platform_connection`` holds OAuth *metadata* only —
  scopes, grant and expiry timestamps, revocation state. The access and refresh
  tokens live in the Windows-protected credential store, keyed by the connection's
  ``public_id``. That is what makes "credentials are not stored in plaintext"
  verifiable by inspection of this file.
* **Job state is one column.** ``ScheduledJob.state`` stores a
  :class:`~hexton.domain.jobstate.JobState`; the legal values are enforced by the
  domain, not duplicated here.
* **Remote observations are appended, never overwritten.** ``RemoteObservation`` is a
  log so a publish can be reconstructed from evidence rather than from the last write.
* **Retention is explicit.** ``DataRetention`` carries the timestamps the Developer
  Policies require (``DF-20``/``DF-21``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from hexton.db.types import UTCDateTime
from hexton.domain.jobstate import JobState, MentionState, RemoteState


def utcnow() -> datetime:
    """Every timestamp in this schema is UTC-aware."""
    return datetime.now(UTC)


def new_public_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """Declarative base with JSON annotation support."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------
# Application settings
# --------------------------------------------------------------------------


class AppSetting(Base, TimestampMixin):
    """A single application setting, stored as JSON so it round-trips typed."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# Connections, identities, compliance
# --------------------------------------------------------------------------


class PlatformConnection(Base, TimestampMixin):
    """An authorized OAuth connection to one provider.

    Holds metadata only. Secrets are in the credential store under ``public_id``.
    """

    __tablename__ = "platform_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=new_public_id
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # "youtube" | "sheets"
    label: Mapped[str] = mapped_column(String(128), nullable=False)

    #: "active" | "disabled" | "revoked" | "expired"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    #: ``COMP-07``: disabling is **not** revoking. Disabling stops Hexton using the
    #: connection and erases nothing; revoking deletes credentials and starts the ``D-1``
    #: deletion clock. Keeping the timestamp here makes the distinction visible in the
    #: data rather than only in the UI copy.
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    granted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: When the refresh token stops being usable. Under an OAuth consent screen in
    #: *Testing* status this is 7 days from consent (DF-17).
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_token_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_token_check_ok: Mapped[bool | None] = mapped_column(Boolean)

    #: COMP-04: revocation must delete Authorized Data within 7 calendar days when the
    #: user revoked from inside the app, and 30 when they revoked through Google's
    #: security settings page (DF-20 / DF-21).
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_by: Mapped[str | None] = mapped_column(String(32))  # "client" | "google" | "token"
    authorized_data_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    identities: Mapped[list[ChannelIdentity]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class ChannelIdentity(Base, TimestampMixin):
    """A verified channel identity resolved from a connection.

    ``verification_source`` records *how* it was established, so the UI can be honest
    about the strength of the claim. ``FEAS-01`` remains open: brand-account targeting
    is documented as consent-compatible but the landing behaviour is not (``DF-18`` /
    ``DF-27``).
    """

    __tablename__ = "channel_identity"
    __table_args__ = (UniqueConstraint("connection_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("platform_connection.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    handle: Mapped[str | None] = mapped_column(String(128))

    #: "channels_list_mine" | "operator_confirmed" | "manual"
    verification_source: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Set when the operator explicitly confirmed this is the intended publishing
    #: channel. The confirmation gate in 01 §C-15 cannot be satisfied without it.
    operator_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    #: True when this identity is believed to be a Brand Account-managed channel.
    #: Unverified: see DF-27.
    is_brand_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscriber_count: Mapped[int | None] = mapped_column(Integer)

    connection: Mapped[PlatformConnection] = relationship(back_populates="identities")


# --------------------------------------------------------------------------
# Media and content
# --------------------------------------------------------------------------


class MediaFile(Base, TimestampMixin):
    """A validated media file on disk."""

    __tablename__ = "media_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float | None]
    container: Mapped[str | None] = mapped_column(String(32))
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Last time the file was *re-read* against ``sha256``. A file whose digest no
    #: longer matches was replaced after it was queued, and must never be uploaded
    #: silently as though it were the clip the operator approved.
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Blocking problems found at validation time, kept so the UI can explain itself.
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class ContentMetadata(Base, TimestampMixin):
    """Fully-specified content fields.

    The description is stored here rather than only on YouTube precisely so that a
    later ``videos.update`` can re-assert it in full (``DF-06``).
    """

    __tablename__ = "content_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    hashtags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    category_id: Mapped[str] = mapped_column(String(8), nullable=False, default="22")
    #: Explicit and required. Never defaulted from a server-side value.
    privacy: Mapped[str] = mapped_column(String(16), nullable=False)

    mention_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mention_handles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    mention_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MentionState.NOT_REQUESTED.value
    )
    mention_reason: Mapped[str | None] = mapped_column(Text)
    #: When the operator attested in Studio that the mention was added there. Stored as
    #: a *human attestation*, never as an API verification (``DF-19``).
    mention_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    #: Optional, consent-based normalisation only (``04`` §FEAT-042). Off by default:
    #: the application never rewrites a title or description the operator wrote unless
    #: they explicitly asked for the transformation.
    consent_transforms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transform_log: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


# --------------------------------------------------------------------------
# Schedules and jobs
# --------------------------------------------------------------------------


class Schedule(Base, TimestampMixin):
    """The publication plan for one job."""

    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    publish_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: The operator's IANA zone, kept so a job created under one zone still displays
    #: correctly after the machine's zone changes.
    display_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    #: The minimum lead time in force when the plan was made, recorded so a later
    #: change to the setting cannot retroactively invalidate an accepted schedule.
    min_lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    #: Report-date component of the Sheets sync key.
    report_date: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ScheduledJob(Base, TimestampMixin):
    """The durable job record and the unit of worker leasing.

    Every field the worker needs to decide "may I claim this?" is here, in one table,
    so the claim is a single indexed read-modify-write rather than a join.
    """

    __tablename__ = "scheduled_job"
    __table_args__ = (
        Index("ix_job_claim", "state", "next_attempt_at"),
        Index("ix_job_lease", "lease_expires_at"),
        UniqueConstraint("idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Business key used as the Sheets sync-key component and in operator messages.
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=new_public_id
    )

    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedule.id", ondelete="RESTRICT"), nullable=False
    )
    metadata_id: Mapped[int] = mapped_column(
        ForeignKey("content_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media_file.id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_connection.id", ondelete="RESTRICT")
    )
    channel_identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel_identity.id", ondelete="RESTRICT")
    )

    state: Mapped[str] = mapped_column(String(32), nullable=False, default=JobState.DRAFT.value)
    failure_kind: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(Text)

    #: --- worker lease ------------------------------------------------------
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    #: A unique token for this job. It makes a retried *create* return the original
    #: job instead of making a second one, and it is the handle by which a later
    #: ``videos.insert`` attempt can be tied back to the schedule that authorised it.
    #: It is not what stops a duplicate upload — that is
    #: :attr:`insert_may_have_succeeded` plus the reconciliation ladder (C-05).
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    insert_may_have_succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Remote outcome, once known.
    video_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    #: Canonical watch URL, written at the same instant as ``video_id`` so the audit
    #: trail records where the video was and not merely what the API called it.
    video_url: Mapped[str | None] = mapped_column(Text)
    #: When the *body* finished uploading. Deliberately separate from publication:
    #: a successful ``videos.insert`` means YouTube has the file, not that it is public.
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    rescheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Outcome of any attempt to change or cancel the publication *on YouTube*. Kept
    #: apart from ``cancel_remote_outcome`` semantics because "I cancelled locally" and
    #: "YouTube no longer has a scheduled publication" are different claims.
    cancel_remote_outcome: Mapped[str | None] = mapped_column(String(48))

    published_at_remote: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Poll pacing. The sweep consults this rather than polling on a fixed tick, so a
    #: large backlog cannot walk through the daily quota (``02`` §7.4).
    next_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    poll_result: Mapped[str | None] = mapped_column(String(32))
    #: Live upload progress for the queue UI. Updated in place; the durable record of
    #: what happened lives in ``job_event``.
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    schedule: Mapped[Schedule] = relationship()
    content: Mapped[ContentMetadata] = relationship()
    media: Mapped[MediaFile] = relationship()
    connection: Mapped[PlatformConnection | None] = relationship()
    channel: Mapped[ChannelIdentity | None] = relationship()

    upload_sessions: Mapped[list[UploadSession]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.occurred_at"
    )
    observations: Mapped[list[RemoteObservation]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class UploadSession(Base, TimestampMixin):
    """A persisted resumable-upload session.

    ``DF-15``: the session URI has a finite lifetime and a ``404`` on resume means it
    has expired. Restarting from byte zero can create a duplicate video, so an expired
    session is never restarted automatically.
    """

    __tablename__ = "upload_session"
    __table_args__ = (Index("ix_upload_session_job_phase", "job_id", "phase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_job.id", ondelete="CASCADE"), nullable=False
    )
    session_uri: Mapped[str] = mapped_column(Text, nullable=False, default="")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    bytes_uploaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="video/*")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    insert_may_have_succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    video_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)

    job: Mapped[ScheduledJob] = relationship(back_populates="upload_sessions")


# --------------------------------------------------------------------------
# Evidence: events, observations, reports
# --------------------------------------------------------------------------


class JobEvent(Base):
    """Append-only local audit trail for a job.

    Never updated, never deleted while the job exists. This is the record an operator
    reads when something went wrong, so it must not be rewritten in place.
    """

    __tablename__ = "job_event"
    __table_args__ = (Index("ix_job_event_job_time", "job_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_job.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    job: Mapped[ScheduledJob] = relationship(back_populates="events")


class RemoteObservation(Base):
    """One poll of a video's remote state.

    Appended, never updated. Publication is verified from evidence, not assumed
    (C-04), and this table is where that evidence lives.
    """

    __tablename__ = "remote_observation"
    __table_args__ = (Index("ix_remote_obs_job_time", "job_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_job.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    video_id: Mapped[str | None] = mapped_column(String(64))
    remote_state: Mapped[str] = mapped_column(
        String(48), nullable=False, default=RemoteState.UNKNOWN.value
    )
    privacy_status: Mapped[str | None] = mapped_column(String(16))
    upload_status: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str | None] = mapped_column(String(32))
    publish_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: True when the connector raised something that made this observation unusable.
    inconclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    job: Mapped[ScheduledJob] = relationship(back_populates="observations")


class SheetReportRecord(Base, TimestampMixin):
    """A row the app intends to exist in the Google Sheet.

    ``sync_key`` is unique, so the *local* record cannot duplicate. It is a hint only:
    the sheet is authoritative, and every sync reads before it appends.
    """

    __tablename__ = "sheet_report_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_job.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    sync_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DataRetention(Base, TimestampMixin):
    """Retention bookkeeping required by the Developer Policies.

    ``DF-20``: 7 calendar days after a client-initiated revocation.
    ``DF-21``: 30 calendar days where the user revoked through Google's security
    settings page, or the token can no longer be refreshed.
    """

    __tablename__ = "data_retention"
    __table_args__ = (Index("ix_retention_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: "revocation_client" (7d) | "revocation_google" (30d) | "operator_delete" (immediate)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    purge_note: Mapped[str | None] = mapped_column(Text)


class SheetsConfig(Base, TimestampMixin):
    """Where reports go, and how. ``01-technical-prd.md`` §9, ``02-technical-prd.md`` §8.

    There is at most one row (``id`` is pinned to 1). Disabling reporting flips
    ``enabled`` and erases nothing: the configuration, the outbox, and every historical
    record survive, because ``COMP-07`` forbids coupling "stop writing" to "forget".

    ``spreadsheet_id`` is an ID, never a URL. A pasted URL is parsed and validated at the
    boundary and then discarded (``01`` §9).
    """

    __tablename__ = "sheets_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The Google connection whose grant authorizes Sheets access. Null until connected.
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_connection.id", ondelete="SET NULL")
    )
    spreadsheet_id: Mapped[str | None] = mapped_column(String(128))
    worksheet: Mapped[str | None] = mapped_column(String(128))
    #: IANA name. Reports are cut over in this zone, so a machine in UTC and a machine in
    #: Asia/Dhaka produce the same report date for the same job.
    report_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    #: ``HH:MM`` local to ``report_timezone``. Jobs earlier in the day belong to the
    #: previous report date.
    cutoff_time: Mapped[str] = mapped_column(String(5), nullable=False, default="00:00")
    #: Job-state values to include. Empty means "all".
    include_statuses: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    #: Optional report columns the operator chose to include. ``REQUIRED_COLUMNS`` are
    #: always written regardless of what is listed here — see ``columns_to_write``.
    include_optional_columns: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    include_daily_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_test_write_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_status: Mapped[str | None] = mapped_column(String(32))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    #: Sync Keys the destination held that no local record explains. Captured by the last
    #: pass so the health panel can show them without contacting Google. Usually a sheet
    #: that predates this installation; left alone either way (see
    #: ``reconciliation_limits``).
    last_sync_foreign_keys: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)


class SheetSummaryRecord(Base, TimestampMixin):
    """One daily completion summary, once per report date per channel.

    Kept apart from ``SheetReportRecord`` deliberately: a summary is not attached to a
    job, and forcing it into a job-keyed table would make ``job_id`` nullable and weaken
    the guarantee that one job produces one upload row.

    ``sync_key`` is still unique, so a re-run of the same date cannot produce a second
    summary row locally — and the sheet is still read before anything is appended.
    """

    __tablename__ = "sheet_summary_record"
    __table_args__ = (Index("ix_sheet_summary_record_date_channel", "report_date", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: ``YYYY-MM-DD`` in ``SheetsConfig.report_timezone``.
    report_date: Mapped[str] = mapped_column(String(10), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sync_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    #: The five counts, plus the generation timestamp.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class PolicyAcceptanceRecord(Base):
    """The ``COMP-01`` privacy-policy acceptance gate, on record.

    Recorded against a *version*. A revised policy re-closes every gate until the
    operator accepts the new text; an acceptance at an old version is deliberately not
    inherited, because a policy that changed usually changed because the data handling
    changed.
    """

    __tablename__ = "policy_acceptance_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WorkerHeartbeat(Base):
    """The worker's durable liveness record.

    The API is a different process from the worker, so it cannot observe the worker's
    memory. This row is how both agree on whether the queue is being serviced — which
    matters because closing the browser must not stop the worker, and an operator needs
    to see that distinction rather than infer it.
    """

    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    beat_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer)
    jobs_claimed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class QuotaLedger(Base):
    """Units spent against a provider quota, per quota window.

    The documented window resets at midnight **Pacific** (``08`` §7.4), so the window
    is computed from the Pacific clock rather than from the machine's local zone —
    which on this machine is ``Asia/Dhaka``, sixteen hours away, and would have
    declared the budget spent a whole day early.
    """

    __tablename__ = "quota_ledger"
    __table_args__ = (UniqueConstraint("provider", "window_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Midnight Pacific for the window that contains this unit's spend.
    window_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    units_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
