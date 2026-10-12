"""Request and response shapes for Phase 3: Sheets reporting, mentions, compliance.

Kept apart from ``schemas.py`` so that the Phase 2 surface stays readable, and so that a
change here cannot silently widen a Phase 2 contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from hexton.compliance.policy import POLICY_LINKS, PRIVACY_COMMITMENTS, PRIVACY_POLICY_VERSION
from hexton.connectors.sheets import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

# --------------------------------------------------------------------------
# COMP-01 / COMP-02 - the privacy gate
# --------------------------------------------------------------------------


class PolicyStatusOut(BaseModel):
    accepted: bool
    version: str = PRIVACY_POLICY_VERSION
    accepted_at: datetime | None = None
    accepted_by: str = ""
    commitments: list[str] = Field(default_factory=lambda: list(PRIVACY_COMMITMENTS))
    links: dict[str, str] = Field(default_factory=lambda: dict(POLICY_LINKS))


class PolicyAcceptIn(BaseModel):
    #: Free-text name of the person accepting, so an audit can attribute the decision.
    accepted_by: str = Field(default="", max_length=128)


# --------------------------------------------------------------------------
# COMP-07 - disable versus revoke
# --------------------------------------------------------------------------


class ConnectionActionOut(BaseModel):
    id: int
    status: str
    #: Exactly what the operation did, in the operator's terms. Never left to inference.
    did: list[str]
    did_not: list[str]
    explanation: str
    #: ``D-1``: how long Authorized Data may now survive, when a clock was started.
    authorized_data_retention_days: int | None = None


# --------------------------------------------------------------------------
# COMP-10 - diagnostics
# --------------------------------------------------------------------------


class DiagnosticOut(BaseModel):
    code: str
    remedy: str
    retry_worthwhile: bool = False
    detail: str = ""


# --------------------------------------------------------------------------
# §1 - Sheets configuration
# --------------------------------------------------------------------------


class SheetsConfigIn(BaseModel):
    spreadsheet_id: str | None = None
    worksheet: str | None = None
    report_timezone: str | None = None
    cutoff_time: str | None = None
    include_statuses: list[str] | None = None
    include_optional_columns: list[str] | None = None
    include_daily_summary: bool | None = None
    enabled: bool | None = None
    connection_id: int | None = None


class SheetsConfigOut(BaseModel):
    configured: bool
    enabled: bool
    spreadsheet_id: str | None
    worksheet: str | None
    connection_id: int | None
    report_timezone: str
    cutoff_time: str
    include_statuses: list[str]
    include_optional_columns: list[str]
    include_daily_summary: bool
    required_columns: list[str] = Field(default_factory=lambda: list(REQUIRED_COLUMNS))
    optional_columns: list[str] = Field(default_factory=lambda: list(OPTIONAL_COLUMNS))
    verified_at: datetime | None
    last_test_write_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    blocking_problems: list[str]


class SheetsHealthOut(BaseModel):
    configured: bool
    enabled: bool
    spreadsheet_id: str | None
    worksheet: str | None
    report_timezone: str | None
    cutoff_time: str | None
    include_daily_summary: bool
    include_optional_columns: list[str]
    required_columns: list[str]
    optional_columns: list[str]
    verified_at: datetime | None
    last_test_write_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    outbox_pending: int
    outbox_synced: int
    outbox_failed: int
    outbox_ambiguous: int
    outbox_total: int
    #: Keys the destination held that no local record explains. From the last pass.
    foreign_keys: list[str]
    blocking_problems: list[str]
    schema_version: str
    reconciliation_limits: list[str]
    policy_version: str


class SheetsOperationOut(BaseModel):
    ok: bool
    message: str


class SheetsSyncOut(BaseModel):
    ok: bool
    attempted: int
    appended: int
    already_present: int
    #: Rows the destination already held under our key, so the ledger caught up. The
    #: normal outcome after a lost response or a worker restart — not an error.
    adopted_from_destination: int
    ambiguous_resolved: int
    ambiguous_unresolved: int
    foreign_keys: list[str]
    failures: list[tuple[str, str]]
    read_after_ambiguous_write: bool
    summary: str


class SheetsOutboxRowOut(BaseModel):
    id: int
    kind: Literal["upload", "daily_summary"]
    sync_key: str
    status: str
    attempts: int
    report_date: str | None
    schedule_id: str | None
    video_id: str | None
    upload_outcome: str | None
    last_synced_at: datetime | None
    last_error: str | None
    payload: dict[str, Any]


class SheetsOutboxOut(BaseModel):
    rows: list[SheetsOutboxRowOut]
    total: int


# --------------------------------------------------------------------------
# §4 - creator mentions
# --------------------------------------------------------------------------


class MentionWorkflowStep(BaseModel):
    index: int
    instruction: str


class MentionOut(BaseModel):
    job_id: int
    state: str
    #: The operator-facing label. This is the only mention text the UI may render.
    label: str
    requested: bool
    handles: list[str]
    intended_creator: str
    #: A human attestation, never an API verification. Stated so the UI cannot blur it.
    attestation_only: bool
    confirmed_at: datetime | None
    reason: str
    steps: list[MentionWorkflowStep]
    prohibitions: list[str]
    #: Set when a metadata rewrite could remove a mention the operator created by hand.
    rewrite_warning: str | None


class MentionConfirmIn(BaseModel):
    #: The operator is asserting, in their own words, that they performed the action in
    #: YouTube Studio. Recorded verbatim so a later "did I actually do that?" is answerable.
    attestation: str = Field(min_length=1, max_length=500)


class MentionStateIn(BaseModel):
    """Move a mention along the workflow without claiming verification."""

    state: Literal["manual_required", "pending_manual", "failed"]


# --------------------------------------------------------------------------
# §2 - report records
# --------------------------------------------------------------------------


class ReportPreviewOut(BaseModel):
    """What one job's row would say, right now, before anything is written."""

    schedule_id: str
    sync_key: str
    report_date: date
    columns: list[str]
    row: dict[str, str]
    #: The exact claim the row makes, so a reviewer can check it in one place.
    claims_published: bool
    video_id_present: bool
    verified_publication_time_present: bool


class DailySummaryOut(BaseModel):
    report_date: date
    rows: list[dict[str, Any]]


__all__ = [
    "ConnectionActionOut",
    "DailySummaryOut",
    "DiagnosticOut",
    "MentionConfirmIn",
    "MentionOut",
    "MentionStateIn",
    "MentionWorkflowStep",
    "PolicyAcceptIn",
    "PolicyStatusOut",
    "ReportPreviewOut",
    "SheetsConfigIn",
    "SheetsConfigOut",
    "SheetsHealthOut",
    "SheetsOperationOut",
    "SheetsOutboxOut",
    "SheetsOutboxRowOut",
    "SheetsSyncOut",
]
