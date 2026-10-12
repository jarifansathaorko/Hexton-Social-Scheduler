"""Google Sheets report delivery: the outbox, the read-before-append protocol, health.

``01-standard-prd.md`` §9, ``02-technical-prd.md`` §8.1, ``AC-MVP-008``, ``FEAS-09``.

The design in one paragraph. A report row is *materialised* locally the moment it becomes
knowable, into ``sheet_report_record`` — a durable outbox, not an in-process queue, so a
worker restart cannot lose a row and cannot re-derive one differently. Delivery is a
separate concern: the reader runs first and the sheet is the authority, because
``spreadsheets.values.append`` is not idempotent and a retried append writes a second row.
A key already in the sheet is skipped. A key that arrives at the sheet with a lost
response is *re-read*, not re-appended. A key is marked synced only after the write is
confirmed. And nothing in this module can reach anything that uploads video — the import
surface is deliberately limited to configuration, models, and the Sheets transport.

Limitations, stated where they cannot be missed — see :func:`reconciliation_limits`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import sqlalchemy as sa
from hexton.bootstrap import AppContext
from hexton.compliance.policy import (
    PRIVACY_POLICY_VERSION,
    PolicyAcceptance,
    require_gate,
)
from hexton.connectors.sheets import (
    KEY_COLUMN,
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
    SHEET_COLUMNS,
    SheetDestination,
    SheetError,
    SheetsUnconfigured,
    SheetWrite,
)
from hexton.db.models import (
    ChannelIdentity,
    ContentMetadata,
    PlatformConnection,
    ScheduledJob,
    SheetReportRecord,
    SheetsConfig,
    SheetSummaryRecord,
)
from hexton.domain.jobstate import JobState, RemoteState
from hexton.domain.mention import MentionState, mention_display
from hexton.domain.report_schema import (
    SCHEMA_VERSION,
    columns_to_write,
    generated_at,
    observed_status,
    upload_outcome,
    validate_columns,
)
from hexton.domain.sync_key import (
    make_summary_sync_key,
    make_sync_key,
)
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: ``SheetsConfig`` is a singleton. ``id`` is pinned to 1 so a race between two processes
#: cannot produce two competing destinations.
SINGLETON_ID = 1

#: A destination reference longer than this is certainly not something Google issued.
_MAX_ID_LENGTH = 128


class SheetsConfigError(RuntimeError):
    """The Sheets configuration is unusable. Blocking; never advisory."""

    def __init__(self, message: str, *, code: str = "invalid_config") -> None:
        super().__init__(message)
        self.code = code


class SheetsSyncError(RuntimeError):
    """A sync could not be performed at all. Distinct from a *failed row*."""

    def __init__(self, message: str, *, code: str = "sync_failed", diagnostic: str = "") -> None:
        super().__init__(message)
        self.code = code
        #: ``compliance.policy.diagnose`` output, so the UI can say *why* in operator terms.
        self.diagnostic = diagnostic


# ==========================================================================
# Configuration
# ==========================================================================


def get_config(session: Session) -> SheetsConfig | None:
    return session.get(SheetsConfig, SINGLETON_ID)


def ensure_config(session: Session, *, now: datetime) -> SheetsConfig:
    config = get_config(session)
    if config is not None:
        return config
    config = SheetsConfig(
        id=SINGLETON_ID,
        report_timezone="UTC",
        cutoff_time="00:00",
        include_statuses=[],
        include_optional_columns=list(OPTIONAL_COLUMNS),
        include_daily_summary=True,
        enabled=False,
    )
    session.add(config)
    session.flush()
    log.info("Sheets configuration created (disabled until the operator connects).")
    return config


def _parse_cutoff(text: str) -> time:
    try:
        hours, minutes = (int(part) for part in (text or "00:00").split(":"))
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError
        return time(hours, minutes)
    except (ValueError, AttributeError):
        raise SheetsConfigError(
            f"cutoff_time {text!r} must be HH:MM between 00:00 and 23:59. The report "
            "cut-over is a real boundary: getting it wrong silently moves jobs between "
            "report days.",
            code="invalid_cutoff",
        ) from None


def destination(config: SheetsConfig | None) -> SheetDestination | None:
    """The write target, or ``None`` when the operator has not finished configuring it."""
    if config is None or not config.spreadsheet_id or not config.worksheet:
        return None
    return SheetDestination(spreadsheet_id=config.spreadsheet_id, worksheet=config.worksheet)


def parse_spreadsheet_reference(text: str) -> str:
    """Accept a pasted URL or a bare ID; return an ID or raise.

    ``01-standard-prd.md`` §9: "if a URL is pasted it must be parsed and validated rather
    than stored verbatim". A stored URL would put a whole navigation string into a place
    that expects an identifier, and would make the destination unverifiable.
    """
    raw = (text or "").strip()
    if not raw:
        raise SheetsConfigError("A spreadsheet ID or URL is required.", code="missing_spreadsheet")
    candidate = raw
    if "/" in raw or "docs.google.com" in raw:
        candidate = _extract_id_from_url(raw)
    candidate = candidate.strip().strip("/")
    if not candidate or len(candidate) > _MAX_ID_LENGTH or any(c.isspace() for c in candidate):
        raise SheetsConfigError(
            f"{text!r} does not look like a spreadsheet ID. Paste the ID from the "
            "spreadsheet URL between /d/ and /edit, or the ID alone.",
            code="invalid_spreadsheet",
        )
    return candidate


def _extract_id_from_url(url: str) -> str:
    parts = [p for p in url.split("/") if p]
    try:
        anchor = parts.index("d")
    except ValueError:
        raise SheetsConfigError(
            f"{url!r} has no /d/<id> segment, so the spreadsheet cannot be identified.",
            code="invalid_spreadsheet",
        ) from None
    if anchor + 1 >= len(parts):
        raise SheetsConfigError(
            f"{url!r} ends at /d/ with no identifier after it.", code="invalid_spreadsheet"
        )
    return parts[anchor + 1]


def upsert_config(session: Session, data: Mapping[str, Any], *, now: datetime) -> SheetsConfig:
    """Apply an operator edit. Validates before touching the row."""
    config = ensure_config(session, now=now)

    if "spreadsheet_id" in data and data["spreadsheet_id"] is not None:
        config.spreadsheet_id = parse_spreadsheet_reference(str(data["spreadsheet_id"]))
    if "worksheet" in data and data["worksheet"] is not None:
        tab = str(data["worksheet"]).strip()
        if not tab:
            raise SheetsConfigError(
                "A worksheet name is required. It is the tab at the bottom of the "
                "spreadsheet, not its title.",
                code="missing_worksheet",
            )
        if len(tab) > 128 or any(c in tab for c in "![]"):
            raise SheetsConfigError(
                f"{tab!r} is not a usable worksheet name.", code="invalid_worksheet"
            )
        config.worksheet = tab
    if "report_timezone" in data and data["report_timezone"] is not None:
        config.report_timezone = str(data["report_timezone"]).strip() or "UTC"
    if "cutoff_time" in data and data["cutoff_time"] is not None:
        _parse_cutoff(str(data["cutoff_time"]))
        config.cutoff_time = str(data["cutoff_time"])
    if "include_statuses" in data:
        config.include_statuses = list(data["include_statuses"] or [])
    if "include_optional_columns" in data and data["include_optional_columns"] is not None:
        chosen = [c for c in OPTIONAL_COLUMNS if c in set(data["include_optional_columns"])]
        config.include_optional_columns = chosen
    if "include_daily_summary" in data:
        config.include_daily_summary = bool(data["include_daily_summary"])
    if "enabled" in data:
        wanted = bool(data["enabled"])
        if wanted and destination(config) is None:
            raise SheetsConfigError(
                "Reporting cannot be enabled before a spreadsheet and worksheet are "
                "chosen: a destination Hexton cannot name is a destination it cannot "
                "read back from, and without a read-back there is no deduplication.",
                code="no_destination",
            )
        config.enabled = wanted
    if "connection_id" in data:
        config.connection_id = data["connection_id"]

    config.updated_at = now
    session.flush()
    return config


def active_columns(config: SheetsConfig) -> tuple[str, ...]:
    """The columns that will actually be written, honouring the operator's toggles."""
    chosen = tuple(c for c in OPTIONAL_COLUMNS if c in set(config.include_optional_columns or []))
    return columns_to_write(chosen).names


def validate_config(config: SheetsConfig | None) -> tuple[str, ...]:
    """Blocking problems with the current configuration. Empty means usable."""
    if config is None:
        return ("Reporting has not been configured yet.",)
    problems: list[str] = []
    if destination(config) is None:
        problems.append("No spreadsheet and worksheet have been chosen.")
    _parse_cutoff(config.cutoff_time)
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(config.report_timezone)
    except Exception:
        problems.append(f"report_timezone {config.report_timezone!r} is not a known IANA zone.")
    columns = columns_to_write(
        tuple(c for c in OPTIONAL_COLUMNS if c in set(config.include_optional_columns or []))
    )
    problems.extend(validate_columns(columns))
    return tuple(problems)


def reconciliation_limits() -> tuple[str, ...]:
    """What spreadsheet-level reconciliation cannot do. Shown to the operator.

    These are real limits of the mechanism, not bugs to be fixed later. They are stated
    so that a future contributor does not "improve" the sync into an overwrite, and so
    that an operator can tell which surprises are impossible to detect rather than
    assuming Hexton is watching for them.
    """
    return (
        "Hexton reads only the Sync Key column. It cannot tell whether the *other* cells "
        "of an existing row were edited by hand, so it will never detect a manual change "
        "to a row it wrote.",
        "Hexton never edits or deletes a row it did not create. If a row needs correcting, "
        "correct it in the sheet and let Hexton keep writing forward.",
        "The sheet is append-only from Hexton's side. There is no code path that "
        "rewrites an existing row, so a retry can never lose an operator's edit.",
        "A Sync Key already present in the sheet is treated as delivered and the local "
        "record catches up to it. The key is deterministic (report date, schedule ID, "
        "schema version), so presence means a report was written under that identity — "
        "usually by an earlier Hexton pass whose response was lost, or before a restart.",
        "Because only the key is read, a row someone hand-wrote with Hexton's exact key "
        "would be adopted the same way and Hexton could not tell the difference. This is "
        "the one disagreement a key read cannot detect; check the sheet by hand if the "
        "row counts look wrong.",
        "If a Sync Key exists in the sheet but Hexton has no matching record at all, it "
        "is reported as a foreign key and left alone. That usually means the sheet was "
        "used by a previous installation.",
        "A failed delivery is recorded against its own record with the reason, and is "
        "retried on the next pass. It is never escalated into a job-state change and "
        "never retried by appending blind.",
    )


# ==========================================================================
# Credentials and the connector
# ==========================================================================


def _sheets_connection(session: Session, config: SheetsConfig) -> PlatformConnection:
    if config.connection_id is None:
        raise SheetsUnconfigured("No Google connection is selected for reporting.")
    connection = session.get(PlatformConnection, config.connection_id)
    if connection is None:
        raise SheetsUnconfigured("The Google connection selected for reporting no longer exists.")
    if connection.revoked_at is not None:
        raise SheetsUnconfigured("The Google connection used for reporting has been revoked.")
    return connection


def _connector_for(ctx: AppContext, session: Session, config: SheetsConfig):
    connection = _sheets_connection(session, config)
    key = f"conn:{connection.public_id}"
    access = ctx.credentials.read(f"{key}:access")
    if not access:
        refresh = ctx.credentials.read(f"{key}:refresh")
        if not refresh:
            raise SheetsUnconfigured(
                "No credential is stored for the reporting connection. This is a "
                "missing credential, not a remote failure — nothing was sent to Google."
            )
        tokens = ctx.connector.refresh(refresh)
        ctx.credentials.write(f"{key}:access", tokens.access_token)
        access = tokens.access_token
    return ctx.sheets(access)


# ==========================================================================
# Report dates
# ==========================================================================


def _zone(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise SheetsConfigError(
            f"report_timezone {name!r} is not a known IANA zone.", code="invalid_timezone"
        ) from exc


def report_date_for(instant: datetime, *, report_timezone: str, cutoff_time: str = "00:00") -> date:
    """The report date an instant belongs to.

    Cutover is a real boundary: a job scheduled at 00:10 under a 03:00 cut-over belongs
    to the *previous* report day, because the operator's working day has not rolled over
    yet. Getting this wrong moves rows between days silently, which is exactly the class
    of error a sync key is supposed to make impossible to repeat.
    """
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(_zone(report_timezone))
    cutoff = _parse_cutoff(cutoff_time)
    # Compare wall clocks. The cut-over is a local time of day the operator chose, so
    # both sides must be naive: an aware ``timetz()`` against a naive cutoff raises the
    # moment the two are equal — which, for a 00:00 cut-over, is exactly midnight.
    if local.time() < cutoff:
        return (local - timedelta(days=1)).date()
    return local.date()


def report_date_of_job(job: ScheduledJob, config: SheetsConfig) -> date | None:
    """The report date for ``job``, or ``None`` when it cannot be determined."""
    if job.schedule is not None and job.schedule.report_date is not None:
        instant = job.schedule.report_date
    elif job.schedule is not None and job.schedule.publish_at_utc is not None:
        instant = job.schedule.publish_at_utc
    else:
        return None
    return report_date_for(
        instant, report_timezone=config.report_timezone, cutoff_time=config.cutoff_time
    )


# ==========================================================================
# Row building
# ==========================================================================


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest_remote_state(session: Session, job: ScheduledJob) -> RemoteState | None:
    """The most recent observed remote state, or ``None`` when nothing was ever polled.

    Read from the appended ``remote_observation`` log rather than from a cached column,
    so the report shows what was last *observed* rather than what was last *recorded* by
    whichever code path happened to run.
    """
    from hexton.db.models import RemoteObservation

    row = session.scalars(
        sa.select(RemoteObservation)
        .where(RemoteObservation.job_id == job.id)
        .order_by(RemoteObservation.observed_at.desc())
        .limit(1)
    ).one_or_none()
    if row is None or not row.remote_state:
        return None
    try:
        return RemoteState(row.remote_state)
    except ValueError:
        return None


def build_row(
    session: Session,
    job: ScheduledJob,
    *,
    config: SheetsConfig,
    report_date: date,
    now: datetime,
) -> dict[str, str]:
    """One upload-outcome row. Never claims a state the system has not evidenced."""
    content: ContentMetadata | None = job.content
    channel: ChannelIdentity | None = job.channel

    state = JobState(job.state)
    remote_state = _latest_remote_state(session, job)
    mention = (
        MentionState(content.mention_state)
        if content is not None and content.mention_state
        else MentionState.NOT_REQUESTED
    )
    schedule_id = job.public_id

    row: dict[str, str] = {
        KEY_COLUMN: make_sync_key(report_date, schedule_id, SCHEMA_VERSION),
        "Schedule ID": schedule_id,
        "Report Date": report_date.isoformat(),
        "Report Schema Version": SCHEMA_VERSION,
        "YouTube Video ID": job.video_id or "",
        "Canonical Video URL": job.video_url or "",
        "Video Title": content.title if content is not None else "",
        "Target Channel ID": channel.channel_id if channel is not None else "",
        "Target Channel Name": channel.title if channel is not None else "",
        "Intended Publication Time (UTC)": _iso(
            job.schedule.publish_at_utc if job.schedule is not None else None
        ),
        "Actual Upload Completion Time (UTC)": _iso(job.uploaded_at),
        "Verified Publication Time (UTC)": _iso(job.published_at_remote),
        "Upload Outcome": upload_outcome(state, video_id=job.video_id, remote_state=remote_state),
        "Current Observed YouTube Status": observed_status(remote_state),
        "Creator Mention State": mention_display(mention),
        "Reporting State": state.value,
        "Error / Manual Review": _explanation(job),
        "Generated At (UTC)": generated_at(now),
    }
    columns = set(active_columns(config))
    return {name: row.get(name, "") for name in SHEET_COLUMNS if name in columns}


def _explanation(job: ScheduledJob) -> str:
    """Why a job is not simply "done", in the operator's terms."""
    if job.failure_message:
        return job.failure_message
    state = JobState(job.state)
    if state is JobState.NEEDS_REVIEW:
        return (
            "The outcome of the upload is uncertain, so Hexton will not retry it and "
            "will not report it as published. Resolve it in the app."
        )
    if state is JobState.MISSED_WINDOW:
        return "The publication time passed before YouTube confirmed a schedule."
    if state is JobState.BLOCKED:
        return job.failure_message or "This job is blocked and needs operator action."
    return ""


# ==========================================================================
# The outbox
# ==========================================================================


def eligible_jobs(session: Session, config: SheetsConfig) -> Sequence[ScheduledJob]:
    """Jobs reporting is allowed to describe.

    A job with no schedule is not reportable: there is no publication time to place it in
    a report day, and a sync key needs a report date. Those jobs are skipped, not guessed
    at — a guessed key would be a second identity for the same schedule.
    """
    rows = session.scalars(sa.select(ScheduledJob)).all()
    included = {str(s) for s in (config.include_statuses or [])}
    return tuple(
        job
        for job in rows
        if report_date_of_job(job, config) is not None and (not included or job.state in included)
    )


def enqueue_reports(session: Session, config: SheetsConfig, *, now: datetime) -> int:
    """Materialise one outbox row per reportable job. Returns how many were created.

    Idempotent: a job that already has a record keeps it. The record's payload is
    *refreshed* on each pass, so a job that moved from ``UPLOADED`` to ``PUBLISHED``
    between two passes produces the same Sync Key with the newer truth — which is what
    makes the daily summary correct without inventing a second row.
    """
    created = 0
    for job in eligible_jobs(session, config):
        report_date = report_date_of_job(job, config)
        assert report_date is not None  # guarded by eligible_jobs
        key = make_sync_key(report_date, job.public_id, SCHEMA_VERSION)
        payload = build_row(session, job, config=config, report_date=report_date, now=now)

        record = session.scalars(
            sa.select(SheetReportRecord).where(SheetReportRecord.sync_key == key)
        ).one_or_none()
        if record is None:
            record = SheetReportRecord(
                job_id=job.id, sync_key=key, status="pending", payload=payload
            )
            session.add(record)
            created += 1
        elif record.status != "synced":
            # Not yet delivered: refresh what it will say. A delivered row is frozen,
            # because changing the payload under a key that is already in the sheet is
            # how a report silently disagrees with itself.
            record.payload = payload
        session.flush()
    return created


#: The five counts a daily completion summary distinguishes, in report order.
SUMMARY_BUCKETS: tuple[tuple[str, frozenset[JobState]], ...] = (
    ("Succeeded", frozenset({JobState.PUBLISHED})),
    ("Failed", frozenset({JobState.FAILED})),
    ("Cancelled", frozenset({JobState.CANCELLED})),
    ("Manual Review", frozenset({JobState.NEEDS_REVIEW})),
)

#: Everything not named above is *pending*: the day has not finished with it yet.
_PENDING_EXCLUDED = frozenset(
    {JobState.PUBLISHED, JobState.FAILED, JobState.CANCELLED, JobState.NEEDS_REVIEW}
)


def summarise_day(
    session: Session, config: SheetsConfig, *, report_date: date
) -> list[tuple[str, dict[str, int]]]:
    """``(channel_id, counts)`` for one report date, one entry per channel touched."""
    by_channel: dict[str, dict[str, int]] = {}
    for job in eligible_jobs(session, config):
        if report_date_of_job(job, config) != report_date:
            continue
        channel_id = job.channel.channel_id if job.channel is not None else "(unassigned)"
        counts = by_channel.setdefault(
            channel_id, {name: 0 for name, _ in SUMMARY_BUCKETS} | {"Pending": 0}
        )
        state = JobState(job.state)
        placed = False
        for name, states in SUMMARY_BUCKETS:
            if state in states:
                counts[name] += 1
                placed = True
                break
        if not placed and state not in _PENDING_EXCLUDED:
            counts["Pending"] += 1
    return sorted(by_channel.items())


def build_summary_row(
    *, report_date: date, channel_id: str, counts: Mapping[str, int], now: datetime
) -> dict[str, str]:
    """One daily summary row. Uses the same key column so it deduplicates identically."""
    total = sum(counts.values())
    return {
        KEY_COLUMN: make_summary_sync_key(report_date, channel_id, SCHEMA_VERSION),
        "Report Date": report_date.isoformat(),
        "Schedule ID": f"(daily summary · {channel_id})",
        "Report Schema Version": SCHEMA_VERSION,
        "YouTube Video ID": "",
        "Canonical Video URL": "",
        "Video Title": f"Daily summary — {total} job(s)",
        "Target Channel ID": channel_id,
        "Target Channel Name": "",
        "Intended Publication Time (UTC)": "",
        "Actual Upload Completion Time (UTC)": "",
        "Verified Publication Time (UTC)": "",
        "Upload Outcome": ", ".join(f"{name}: {counts.get(name, 0)}" for name, _ in SUMMARY_BUCKETS)
        + f", Pending: {counts.get('Pending', 0)}",
        "Current Observed YouTube Status": "",
        "Creator Mention State": "",
        "Reporting State": "daily_summary",
        "Error / Manual Review": "",
        "Generated At (UTC)": generated_at(now),
    }


def enqueue_summaries(
    session: Session, config: SheetsConfig, *, report_date: date, now: datetime
) -> int:
    """Materialise the daily completion summary rows for ``report_date``."""
    if not config.include_daily_summary:
        return 0
    created = 0
    for channel_id, counts in summarise_day(session, config, report_date=report_date):
        key = make_summary_sync_key(report_date, channel_id, SCHEMA_VERSION)
        payload = build_summary_row(
            report_date=report_date, channel_id=channel_id, counts=counts, now=now
        )
        record = session.scalars(
            sa.select(SheetSummaryRecord).where(SheetSummaryRecord.sync_key == key)
        ).one_or_none()
        if record is None:
            session.add(
                SheetSummaryRecord(
                    report_date=report_date.isoformat(),
                    channel_id=channel_id,
                    sync_key=key,
                    status="pending",
                    payload=payload,
                )
            )
            created += 1
        elif record.status != "synced":
            record.payload = payload
        session.flush()
    return created


# ==========================================================================
# Delivery — the read-before-append protocol
# ==========================================================================


@dataclass
class SyncSummary:
    """What one delivery pass did. Everything the UI and the tests need."""

    attempted: int = 0
    appended: int = 0
    already_present: int = 0
    #: Rows the destination already held under our key, so the local ledger caught up.
    #: This is the *normal* outcome after a lost response or a worker restart, which is
    #: why it is counted separately from a first-time append rather than being an error.
    adopted_from_destination: int = 0
    ambiguous_resolved: int = 0
    ambiguous_unresolved: int = 0
    foreign_keys: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    read_after_ambiguous_write: bool = False
    diagnostic: str = ""
    ok: bool = True

    @property
    def summary(self) -> str:
        if not self.ok:
            return self.diagnostic or "The report could not be synchronized."
        parts = [f"{self.appended} row(s) written"]
        if self.already_present:
            parts.append(f"{self.already_present} already present")
        if self.adopted_from_destination:
            parts.append(f"{self.adopted_from_destination} found already written")
        if self.ambiguous_resolved:
            parts.append(f"{self.ambiguous_resolved} recovered after a lost response")
        if self.ambiguous_unresolved:
            parts.append(f"{self.ambiguous_unresolved} unconfirmed")
        if self.foreign_keys:
            parts.append(f"{len(self.foreign_keys)} foreign key(s)")
        if self.failures:
            parts.append(f"{len(self.failures)} failed")
        return "; ".join(parts) + "."


def _pending_records(session: Session) -> tuple[SheetReportRecord, ...]:
    return tuple(
        session.scalars(
            sa.select(SheetReportRecord)
            .where(SheetReportRecord.status.in_(("pending", "failed", "ambiguous")))
            .order_by(SheetReportRecord.id)
        ).all()
    )


def _pending_summaries(session: Session) -> tuple[SheetSummaryRecord, ...]:
    return tuple(
        session.scalars(
            sa.select(SheetSummaryRecord)
            .where(SheetSummaryRecord.status.in_(("pending", "failed", "ambiguous")))
            .order_by(SheetSummaryRecord.id)
        ).all()
    )


def sync_once(
    ctx: AppContext,
    session: Session,
    *,
    config: SheetsConfig | None = None,
    now: datetime | None = None,
) -> SyncSummary:
    """Run one delivery pass. The whole read-before-append protocol lives here.

    Order is the point, and it is not negotiable:

    1. **Read** the destination's existing Sync Keys.
    2. **Compare** them against the pending outbox.
    3. **Append** only the keys that are missing.
    4. **Record** success locally, only after the write is confirmed.
    5. On an ambiguous outcome, **re-read** and reconcile — never append again.

    A failure here is recorded against the outbox row and retried later. It never
    escalates, never touches a job's state, and cannot reach anything that uploads video.
    """
    now = now or datetime.now(UTC)
    config = config or get_config(session)
    if config is None or not config.enabled:
        return SyncSummary(ok=False, diagnostic="Reporting is not enabled.")

    problems = validate_config(config)
    if problems:
        return SyncSummary(ok=False, diagnostic="; ".join(problems))

    try:
        connector = _connector_for(ctx, session, config)
    except SheetsUnconfigured as exc:
        return SyncSummary(ok=False, diagnostic=str(exc))
    except Exception as exc:
        log.warning("Could not obtain a Sheets credential: %s", exc)
        return SyncSummary(ok=False, diagnostic=f"Could not obtain a credential: {exc}")

    target = destination(config)
    assert target is not None  # guarded by validate_config

    # --- 1. read ---------------------------------------------------------
    try:
        remote_keys = set(connector.read_sync_keys(target))
    except SheetError as exc:
        return SyncSummary(ok=False, diagnostic=f"Could not read the destination: {exc}")

    # --- 2. compare ------------------------------------------------------
    records = list(_pending_records(session)) + list(_pending_summaries(session))
    to_append = [r for r in records if r.sync_key not in remote_keys]
    present = [r for r in records if r.sync_key in remote_keys]
    known = {r.sync_key for r in records}

    # ``already_present`` counts every pending row the destination already held;
    # ``adopted_from_destination`` counts how many of those the local ledger then
    # caught up with. They differ only in that a skipped-by-the-writer key still
    # needs the local status advanced, which ``_resolve_present`` does.
    result = SyncSummary(already_present=len(present))
    result.foreign_keys = sorted(k for k in remote_keys if k and k not in known)
    _resolve_present(session, present, now=now, result=result)

    if not to_append:
        config.last_sync_at = now
        config.last_sync_status = "ok"
        config.last_sync_error = None
        config.last_sync_foreign_keys = list(result.foreign_keys)
        session.flush()
        return result

    # --- 3. append, once -------------------------------------------------
    result.attempted = len(to_append)
    ordered = sorted(to_append, key=lambda r: r.sync_key)
    payloads = [_row_for(r) for r in ordered]

    try:
        write: SheetWrite = connector.append_rows(target, payloads)
    except SheetError as exc:
        _handle_append_failure(
            session,
            connector,
            target,
            ordered,
            exc,
            now=now,
            result=result,
        )
    else:
        _mark_synced(
            session, [r for r in ordered if r.sync_key in set(write.appended_keys)], now=now
        )
        result.appended = len(write.appended_keys)
        result.already_present += len(write.skipped_keys)

    # --- 4. record the outcome -------------------------------------------
    result.ok = result.ambiguous_unresolved == 0 and not result.failures
    config.last_sync_at = now
    config.last_sync_status = "ok" if result.ok else "partial" if result.appended else "failed"
    config.last_sync_error = None if result.ok else result.summary
    config.last_sync_foreign_keys = list(result.foreign_keys)
    session.flush()
    return result


def _row_for(record: Any) -> dict[str, str]:
    payload = dict(record.payload or {})
    payload.setdefault(KEY_COLUMN, record.sync_key)
    return payload


def _mark_synced(session: Session, records: Iterable[Any], *, now: datetime) -> None:
    for record in records:
        record.status = "synced"
        record.last_synced_at = now
        record.last_error = None
    session.flush()


def _resolve_present(
    session: Session, present: Sequence[Any], *, now: datetime, result: SyncSummary
) -> None:
    """Keys already in the sheet.

    The sheet is the authority (``02`` §8.1), so a key that is already there is treated
    as delivered. Two things make that safe rather than careless:

    * The key is **deterministic** — report date, schedule ID and schema version — so a
      key can only be present because this application's own report was written under
      that identity, or because someone hand-wrote that exact string.
    * Nothing is overwritten either way. Adopting a key changes only the local ledger.

    The residual risk — a hand-written row that happens to carry our key — is stated in
    :func:`reconciliation_limits` rather than being turned into a blocking workflow it
    is not possible to detect reliably. See that function for why detection is not
    possible from a key read alone.
    """
    for record in present:
        if record.status == "synced":
            continue
        was_ambiguous = record.status == "ambiguous"
        record.status = "synced"
        record.last_synced_at = now
        record.last_error = None
        if was_ambiguous:
            # The reconciliation step: an earlier write reached the sheet and its
            # response was lost. This read is the proof that it landed.
            result.ambiguous_resolved += 1
        else:
            result.adopted_from_destination += 1
        result.read_after_ambiguous_write = True
    session.flush()


def _handle_append_failure(
    session: Session,
    connector: Any,
    target: SheetDestination,
    ordered: Sequence[Any],
    exc: SheetError,
    *,
    now: datetime,
    result: SyncSummary,
) -> None:
    """Classify an append failure. ``ambiguous`` gets a re-read; everything else does not."""
    if exc.kind != "ambiguous":
        for record in ordered:
            record.status = "failed"
            record.attempts = (record.attempts or 0) + 1
            record.last_error = f"{exc.kind}: {exc.detail}"
        result.failures = [(r.sync_key, str(exc)) for r in ordered]
        result.ok = False
        session.flush()
        return

    # The request may have reached the server. Re-read — do NOT append again. This is
    # the single case in which a second request is authorised, and it is a *read*.
    result.read_after_ambiguous_write = True
    try:
        remote_after = set(connector.read_sync_keys(target))
    except SheetError as read_exc:
        for record in ordered:
            record.status = "ambiguous"
            record.attempts = (record.attempts or 0) + 1
            record.last_error = (
                f"The write may have reached the destination and the follow-up read "
                f"failed ({read_exc}). Hexton will read again on the next pass before "
                f"deciding anything; it will not append a second row."
            )
        result.ambiguous_unresolved = len(ordered)
        result.ok = False
        session.flush()
        return

    landed = [r for r in ordered if r.sync_key in remote_after]
    missing = [r for r in ordered if r.sync_key not in remote_after]
    _mark_synced(session, landed, now=now)
    result.ambiguous_resolved += len(landed)
    for record in missing:
        record.status = "ambiguous"
        record.attempts = (record.attempts or 0) + 1
        record.last_error = (
            "The write may have reached the destination, but the follow-up read does "
            "not show it. Hexton will read again on the next pass before deciding "
            "anything; it will not append a second row."
        )
    result.ambiguous_unresolved = len(missing)
    result.ok = not missing
    session.flush()


# ==========================================================================
# Operator-facing operations
# ==========================================================================


def verify_access(ctx: AppContext, session: Session, *, now: datetime) -> tuple[bool, str]:
    """Prove the destination exists and the stored credential can reach it."""
    config = get_config(session)
    if config is None:
        return False, "Reporting has not been configured yet."
    target = destination(config)
    if target is None:
        return False, "Choose a spreadsheet and worksheet first."
    try:
        connector = _connector_for(ctx, session, config)
    except SheetsUnconfigured as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Could not obtain a credential: {exc}"
    try:
        connector.verify(target)
    except SheetError as exc:
        return False, _explain_sheet_error(exc)
    config.verified_at = now
    session.flush()
    return True, f"Reached {target.spreadsheet_id!r}, worksheet {target.worksheet!r}."


def test_write(ctx: AppContext, session: Session, *, now: datetime) -> tuple[bool, str]:
    """Write exactly one probe row and read it back. Never a report row."""
    config = get_config(session)
    if config is None:
        return False, "Reporting has not been configured yet."
    target = destination(config)
    if target is None:
        return False, "Choose a spreadsheet and worksheet first."
    try:
        connector = _connector_for(ctx, session, config)
    except SheetsUnconfigured as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Could not obtain a credential: {exc}"

    probe_key = f"TEST_{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    try:
        connector.verify(target)
        write = connector.test_write(target, probe_key)
    except SheetError as exc:
        return False, _explain_sheet_error(exc)

    config.last_test_write_at = now
    session.flush()
    return True, (
        f"Wrote the test row {probe_key!r} ({len(write.appended_keys)} row). Delete it "
        "from the sheet whenever you like — Hexton never edits or removes rows."
    )


def _explain_sheet_error(exc: SheetError) -> str:
    """Translate a transport error into the sentence the operator needs."""
    return {
        "expired": (
            "The stored authorization can no longer authorize a request. Reauthorize "
            "Google in Settings; nothing was sent to the spreadsheet."
        ),
        "forbidden": (
            "Google refused this destination for the connected account. Share the "
            "spreadsheet with that account, or connect an account that owns it. This is "
            "a permission problem, not a network problem."
        ),
        "not_found": (
            "The spreadsheet or worksheet could not be found. Check that the ID is right "
            "and that the worksheet name matches the tab exactly, including spaces and "
            "capitalisation."
        ),
        "network": (
            "The request did not complete. Hexton has not written anything and will not "
            "guess; try again when the connection is back."
        ),
        "ambiguous": (
            "The write may have reached the spreadsheet, but the outcome is not "
            "confirmed. Hexton will read the sheet again before doing anything else, and "
            "will not append a duplicate row."
        ),
        "invalid": f"The destination reference is not usable: {exc.detail}",
    }.get(exc.kind, str(exc))


def health(ctx: AppContext, session: Session, *, now: datetime) -> dict[str, Any]:
    """The reporting-health panel, in one payload.

    Deliberately local-only: it never contacts Google, so opening Settings cannot cost a
    request or fail because the network is down. Everything that needs the destination is
    either a timestamp recorded by an earlier pass or a list captured by the last sync.
    """
    config = get_config(session)
    problems = validate_config(config)
    undelivered: int = 0
    for model in (SheetReportRecord, SheetSummaryRecord):
        undelivered += (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.status.in_(("pending", "failed", "ambiguous")))
            )
            or 0
        )
    failed = 0
    for model in (SheetReportRecord, SheetSummaryRecord):
        failed += (
            session.scalar(
                sa.select(sa.func.count()).select_from(model).where(model.status == "failed")
            )
            or 0
        )
    ambiguous = 0
    for model in (SheetReportRecord, SheetSummaryRecord):
        ambiguous += (
            session.scalar(
                sa.select(sa.func.count()).select_from(model).where(model.status == "ambiguous")
            )
            or 0
        )
    synced = (
        session.scalar(
            sa.select(sa.func.count())
            .select_from(SheetReportRecord)
            .where(SheetReportRecord.status == "synced")
        )
        or 0
    )
    return {
        "configured": destination(config) is not None,
        "enabled": bool(config and config.enabled),
        "spreadsheet_id": config.spreadsheet_id if config else None,
        "worksheet": config.worksheet if config else None,
        "report_timezone": config.report_timezone if config else None,
        "cutoff_time": config.cutoff_time if config else None,
        "include_daily_summary": bool(config and config.include_daily_summary),
        "include_optional_columns": list(config.include_optional_columns or []) if config else [],
        "required_columns": list(REQUIRED_COLUMNS),
        "optional_columns": list(OPTIONAL_COLUMNS),
        "verified_at": config.verified_at if config else None,
        "last_test_write_at": config.last_test_write_at if config else None,
        "last_sync_at": config.last_sync_at if config else None,
        "last_sync_status": config.last_sync_status if config else None,
        "last_sync_error": config.last_sync_error if config else None,
        "outbox_pending": int(undelivered),
        "outbox_synced": int(synced),
        "outbox_failed": int(failed),
        "outbox_ambiguous": int(ambiguous),
        "outbox_total": int(undelivered) + int(synced),
        # Captured by the last pass rather than looked up now — see this function's
        # docstring. Empty means "none seen", not "none exist".
        "foreign_keys": list(config.last_sync_foreign_keys or []) if config else [],
        "blocking_problems": list(problems),
        "schema_version": SCHEMA_VERSION,
        "reconciliation_limits": list(reconciliation_limits()),
        "policy_version": PRIVACY_POLICY_VERSION,
    }


__all__ = [
    "KEY_COLUMN",
    "OPTIONAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "SCHEMA_VERSION",
    "SHEET_COLUMNS",
    "SINGLETON_ID",
    "SUMMARY_BUCKETS",
    "PolicyAcceptance",
    "SheetsConfigError",
    "SheetsSyncError",
    "SyncSummary",
    "active_columns",
    "build_row",
    "build_summary_row",
    "columns_to_write",
    "destination",
    "eligible_jobs",
    "enqueue_reports",
    "enqueue_summaries",
    "ensure_config",
    "get_config",
    "health",
    "parse_spreadsheet_reference",
    "reconciliation_limits",
    "report_date_for",
    "report_date_of_job",
    "require_gate",
    "summarise_day",
    "sync_once",
    "test_write",
    "upsert_config",
    "validate_config",
    "verify_access",
]
