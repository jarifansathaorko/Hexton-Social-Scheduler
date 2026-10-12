"""Google Sheets reporting: configuration, verification, delivery, outbox inspection.

``01-standard-prd.md`` §9, ``02-technical-prd.md`` §8, ``AC-MVP-008``, ``FEAS-09``.

Every mutating route here is gated by the ``COMP-01`` privacy acceptance, because
reporting is an API-connected feature: it reads a Google Sheet under an OAuth grant. The
gate is applied in one place — :func:`_require_policy` — so a new route cannot forget it.

Not one route here can touch a job's state or trigger an upload. The router's import
surface is configuration, outbox inspection, and the Sheets service. That is the
structural half of "a report write must never trigger another video upload"; the other
half is :mod:`hexton.worker.report_sync`, which owns delivery.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hexton.api.deps import CtxDep, SessionDep
from hexton.api.schemas_phase3 import (
    DailySummaryOut,
    ReportPreviewOut,
    SheetsConfigIn,
    SheetsConfigOut,
    SheetsHealthOut,
    SheetsOperationOut,
    SheetsOutboxOut,
    SheetsOutboxRowOut,
    SheetsSyncOut,
)
from hexton.bootstrap import AppContext
from hexton.compliance.policy import Feature, PolicyAcceptance, gate
from hexton.db.models import (
    PolicyAcceptanceRecord,
    ScheduledJob,
    SheetReportRecord,
    SheetsConfig,
    SheetSummaryRecord,
)
from hexton.domain.jobstate import JobState
from hexton.domain.report_schema import SCHEMA_VERSION
from hexton.domain.sync_key import make_summary_sync_key, parse_summary_sync_key, parse_sync_key
from hexton.services import sheets as sheets_service

router = APIRouter(prefix="/api/reporting", tags=["reporting"])


def _require_policy(session: Session) -> None:
    """``COMP-01``. One gate, applied to every route in this router."""
    record = session.scalars(
        select(PolicyAcceptanceRecord)
        .where(PolicyAcceptanceRecord.superseded_at.is_(None))
        .order_by(PolicyAcceptanceRecord.accepted_at.desc())
        .limit(1)
    ).one_or_none()
    acceptance = (
        PolicyAcceptance(version=record.version, accepted_at=record.accepted_at) if record else None
    )
    result = gate(acceptance, Feature.SHEETS_REPORTING)
    if not result.allowed:
        raise HTTPException(status_code=403, detail=result.reason)


def _load_config(session: Session) -> SheetsConfig:
    config = sheets_service.get_config(session)
    if config is None:
        raise HTTPException(status_code=404, detail="Reporting has not been configured yet.")
    return config


# --------------------------------------------------------------------------
# §1 - configuration
# --------------------------------------------------------------------------


@router.get("/config", response_model=SheetsConfigOut)
def get_config(session: Session = SessionDep) -> SheetsConfigOut:
    _require_policy(session)
    config = sheets_service.ensure_config(session, now=datetime.now(UTC))
    return SheetsConfigOut(
        configured=sheets_service.destination(config) is not None,
        enabled=config.enabled,
        spreadsheet_id=config.spreadsheet_id,
        worksheet=config.worksheet,
        connection_id=config.connection_id,
        report_timezone=config.report_timezone,
        cutoff_time=config.cutoff_time,
        include_statuses=list(config.include_statuses or []),
        include_optional_columns=list(config.include_optional_columns or []),
        include_daily_summary=config.include_daily_summary,
        verified_at=config.verified_at,
        last_test_write_at=config.last_test_write_at,
        last_sync_at=config.last_sync_at,
        last_sync_status=config.last_sync_status,
        last_sync_error=config.last_sync_error,
        blocking_problems=list(sheets_service.validate_config(config)),
    )


@router.put("/config", response_model=SheetsConfigOut)
def put_config(payload: SheetsConfigIn, session: Session = SessionDep) -> SheetsConfigOut:
    _require_policy(session)
    data = payload.model_dump(exclude_unset=True)
    try:
        sheets_service.upsert_config(session, data, now=datetime.now(UTC))
    except sheets_service.SheetsConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return get_config(session)


@router.get("/health", response_model=SheetsHealthOut)
def health(ctx: AppContext = CtxDep, session: Session = SessionDep) -> SheetsHealthOut:
    _require_policy(session)
    return SheetsHealthOut(**sheets_service.health(ctx, session, now=datetime.now(UTC)))


@router.post("/verify", response_model=SheetsOperationOut)
def verify(ctx: AppContext = CtxDep, session: Session = SessionDep) -> SheetsOperationOut:
    """``spreadsheets.get`` against the chosen destination. Reads nothing else."""
    _require_policy(session)
    ok, message = sheets_service.verify_access(ctx, session, now=datetime.now(UTC))
    session.commit()
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return SheetsOperationOut(ok=True, message=message)


@router.post("/test-write", response_model=SheetsOperationOut)
def test_write(ctx: AppContext = CtxDep, session: Session = SessionDep) -> SheetsOperationOut:
    """Write exactly one probe row and read it back. Never a report row."""
    _require_policy(session)
    ok, message = sheets_service.test_write(ctx, session, now=datetime.now(UTC))
    session.commit()
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return SheetsOperationOut(ok=True, message=message)


@router.post("/sync", response_model=SheetsSyncOut)
def sync_now(ctx: AppContext = CtxDep, session: Session = SessionDep) -> SheetsSyncOut:
    """Run one delivery pass on demand, exactly as the worker would.

    Materialises the outbox first, then delivers. Keeping those as two steps rather than
    folding the enqueue into ``sync_once`` is deliberate: delivery is the part with the
    subtle protocol, and a function that both invents rows and writes them is harder to
    reason about than two that each do one thing.
    """
    _require_policy(session)
    config = _load_config(session)
    if not config.enabled:
        raise HTTPException(
            status_code=409,
            detail="Reporting is disabled. Enabling it is a separate, deliberate step.",
        )
    now = datetime.now(UTC)
    sheets_service.enqueue_reports(session, config, now=now)
    if config.include_daily_summary:
        for job_date in _report_dates_due(session, config):
            sheets_service.enqueue_summaries(session, config, report_date=job_date, now=now)
    session.commit()

    summary = sheets_service.sync_once(ctx, session, config=config, now=now)
    session.commit()
    return SheetsSyncOut(
        ok=summary.ok,
        attempted=summary.attempted,
        appended=summary.appended,
        already_present=summary.already_present,
        adopted_from_destination=summary.adopted_from_destination,
        ambiguous_resolved=summary.ambiguous_resolved,
        ambiguous_unresolved=summary.ambiguous_unresolved,
        foreign_keys=summary.foreign_keys,
        failures=summary.failures,
        read_after_ambiguous_write=summary.read_after_ambiguous_write,
        summary=summary.summary,
    )


def _report_dates_due(session: Session, config: SheetsConfig) -> list[date]:
    """Every report date that has at least one reportable job.

    Re-derived each pass rather than remembered, so a summary for a day that is still
    filling in stays correct — a job that reaches Published at 23:50 must still land in
    that day's summary.
    """
    dates: set[date] = set()
    for job in session.scalars(select(ScheduledJob)).all():
        report_date = sheets_service.report_date_of_job(job, config)
        if report_date is not None:
            dates.add(report_date)
    return sorted(dates)


@router.post("/enqueue", response_model=SheetsOperationOut)
def enqueue(report_date: date | None = None, session: Session = SessionDep) -> SheetsOperationOut:
    """Materialise the outbox without delivering it. Delivery still reads first."""
    _require_policy(session)
    config = _load_config(session)
    now = datetime.now(UTC)
    created = sheets_service.enqueue_reports(session, config, now=now)
    if report_date is not None:
        created += sheets_service.enqueue_summaries(
            session, config, report_date=report_date, now=now
        )
    session.commit()
    return SheetsOperationOut(
        ok=True,
        message=f"{created} report row(s) queued. Delivery reads the destination first, "
        f"so nothing already present will be written again.",
    )


# --------------------------------------------------------------------------
# §2 - what a row says
# --------------------------------------------------------------------------


@router.get("/preview/{job_id}", response_model=ReportPreviewOut)
def preview(job_id: int, session: Session = SessionDep) -> ReportPreviewOut:
    """What this job's row would say, before anything is written.

    Exposed so the honesty rule is inspectable: ``claims_published`` is derived from the
    same function the writer uses, so a row can never claim Published in the preview and
    say something weaker in the sheet.
    """
    _require_policy(session)
    config = _load_config(session)
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} does not exist.")
    report_date = sheets_service.report_date_of_job(job, config)
    if report_date is None:
        raise HTTPException(
            status_code=409,
            detail="This job has no publication time, so it has no report date and no "
            "row. Hexton will not invent one.",
        )
    row = sheets_service.build_row(
        session, job, config=config, report_date=report_date, now=datetime.now(UTC)
    )
    return ReportPreviewOut(
        schedule_id=job.public_id,
        sync_key=row["Sync Key"],
        report_date=report_date,
        columns=list(row),
        row=row,
        claims_published=row.get("Upload Outcome") == "Published",
        video_id_present=bool(row.get("YouTube Video ID")),
        verified_publication_time_present=bool(row.get("Verified Publication Time (UTC)")),
    )


@router.get("/daily-summary/{report_date}", response_model=DailySummaryOut)
def daily_summary(report_date: date, session: Session = SessionDep) -> DailySummaryOut:
    """The five counts for one report date, per channel, as they would be written."""
    _require_policy(session)
    config = _load_config(session)
    rows = [
        {
            "channel_id": channel_id,
            "sync_key": make_summary_sync_key(report_date, channel_id, SCHEMA_VERSION),
            "counts": counts,
        }
        for channel_id, counts in sheets_service.summarise_day(
            session, config, report_date=report_date
        )
    ]
    return DailySummaryOut(report_date=report_date, rows=rows)


# --------------------------------------------------------------------------
# The outbox
# --------------------------------------------------------------------------


@router.get("/outbox", response_model=SheetsOutboxOut)
def outbox(status: str | None = None, session: Session = SessionDep) -> SheetsOutboxOut:
    _require_policy(session)
    upload_query = select(SheetReportRecord).order_by(SheetReportRecord.id)
    summary_query = select(SheetSummaryRecord).order_by(SheetSummaryRecord.id)
    if status:
        upload_query = upload_query.where(SheetReportRecord.status == status)
        summary_query = summary_query.where(SheetSummaryRecord.status == status)

    rows: list[SheetsOutboxRowOut] = []
    for record in session.scalars(upload_query).all():
        payload = dict(record.payload or {})
        try:
            parsed_date = parse_sync_key(record.sync_key).report_date.isoformat()
        except ValueError:  # pragma: no cover - keys are minted here
            parsed_date = None
        rows.append(
            SheetsOutboxRowOut(
                id=record.id,
                kind="upload",
                sync_key=record.sync_key,
                status=record.status,
                attempts=record.attempts,
                report_date=parsed_date,
                schedule_id=payload.get("Schedule ID"),
                video_id=payload.get("YouTube Video ID") or None,
                upload_outcome=payload.get("Upload Outcome"),
                last_synced_at=record.last_synced_at,
                last_error=record.last_error,
                payload=payload,
            )
        )
    for record in session.scalars(summary_query).all():
        payload = dict(record.payload or {})
        try:
            parsed_date = parse_summary_sync_key(record.sync_key)[0].isoformat()
        except ValueError:  # pragma: no cover - keys are minted here
            parsed_date = record.report_date
        rows.append(
            SheetsOutboxRowOut(
                id=record.id,
                kind="daily_summary",
                sync_key=record.sync_key,
                status=record.status,
                attempts=record.attempts,
                report_date=parsed_date,
                schedule_id=None,
                video_id=None,
                upload_outcome=payload.get("Upload Outcome"),
                last_synced_at=record.last_synced_at,
                last_error=record.last_error,
                payload=payload,
            )
        )
    return SheetsOutboxOut(rows=rows, total=len(rows))


@router.post("/outbox/{record_id}/retry", response_model=SheetsOperationOut)
def retry_record(record_id: int, session: Session = SessionDep) -> SheetsOperationOut:
    """Put a failed or unconfirmed row back in the delivery queue.

    This is the error workflow, and it is deliberately small. The operator is not asked
    to decide *what* the destination says — the next pass reads it first and reconciles
    by key, so re-queuing cannot duplicate a row that already landed. The operator is
    only deciding *when* to try again.

    Nothing here writes to the sheet, and nothing here can overwrite a row: delivery is
    append-only and always reads first.
    """
    _require_policy(session)
    record = session.get(SheetReportRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Report record {record_id} does not exist.")
    if record.status == "synced":
        raise HTTPException(
            status_code=409,
            detail=f"Record {record_id} is already delivered; there is nothing to retry.",
        )
    record.status = "pending"
    record.last_error = (
        "Re-queued by the operator. The destination is read again before anything is "
        "appended, so a row that already landed will be recognised and skipped."
    )
    session.commit()
    return SheetsOperationOut(
        ok=True,
        message="Re-queued. The next pass reads the destination first, so this cannot "
        "produce a duplicate row even if the earlier write actually landed.",
    )


@router.get("/limits", response_model=dict[str, Any])
def limits() -> dict[str, Any]:
    """What spreadsheet-level reconciliation cannot do. Public: no policy gate needed."""
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": list(sheets_service.reconciliation_limits()),
        "note": "Hexton appends to a sheet and never edits or deletes a row it did not "
        "create. Correcting a delivered report is an operator action in the sheet.",
    }


def _job_states(session: Session) -> list[str]:  # pragma: no cover - helper for the UI
    return sorted({row[0] for row in session.execute(select(ScheduledJob.state)).all()})


__all__ = ["JobState", "router", "selectinload"]
