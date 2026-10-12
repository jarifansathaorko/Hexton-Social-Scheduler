"""ReportSyncSweep — delivering report rows to Google Sheets.

Deliberately separate from :mod:`hexton.worker.sweep`, which is the *publish
confirmation* sweep and the only thing that may move a job to Published. This module
never touches a job's state, never reads a job's video connector, and cannot issue an
upload: its whole surface is the Sheets outbox and the Sheets transport.

That separation is the enforcement of ``02-technical-prd.md`` §8.2's "a report write must
never trigger another video upload", and it is structural rather than a comment. If a
report failure ever appears to have affected an upload, the cause is not here.

Cadence is slower than the job loop on purpose. Reporting is not latency-sensitive, and
every pass costs a read of the destination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from hexton.bootstrap import AppContext
from hexton.services import sheets as sheets_service

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportSyncResult:
    """What one reporting pass did. Mirrors ``SyncSummary`` for the worker's report."""

    ran: bool
    attempted: int = 0
    appended: int = 0
    already_present: int = 0
    ambiguous_resolved: int = 0
    ambiguous_unresolved: int = 0
    foreign_keys: int = 0
    failures: int = 0
    diagnostic: str = ""


class ReportSyncSweep:
    """Materialises the outbox, then delivers it with read-before-append."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def run(
        self, *, now: datetime | None = None, report_date: datetime | None = None
    ) -> ReportSyncResult:
        now = now or datetime.now(UTC)
        with self.ctx.sessions() as session:
            config = sheets_service.get_config(session)
            if config is None or not config.enabled:
                return ReportSyncResult(ran=False, diagnostic="Reporting is not enabled.")

            try:
                sheets_service.enqueue_reports(session, config, now=now)
                if config.include_daily_summary:
                    for job_date in _report_dates_due(session, config):
                        sheets_service.enqueue_summaries(
                            session, config, report_date=job_date, now=now
                        )
                session.commit()
            except Exception:
                log.warning("Could not materialise the report outbox.", exc_info=True)
                session.rollback()
                return ReportSyncResult(ran=False, diagnostic="Could not build the report outbox.")

            summary = sheets_service.sync_once(self.ctx, session, config=config, now=now)
            session.commit()

        # A pass that did not complete is a failure even when no individual row failed —
        # for instance a destination that could not be read at all. Counting only row
        # failures would report a totally unreachable sheet as "nothing went wrong".
        row_failures = len(summary.failures)
        return ReportSyncResult(
            ran=True,
            attempted=summary.attempted,
            appended=summary.appended,
            already_present=summary.already_present,
            ambiguous_resolved=summary.ambiguous_resolved,
            ambiguous_unresolved=summary.ambiguous_unresolved,
            foreign_keys=len(summary.foreign_keys),
            failures=row_failures or (0 if summary.ok else 1),
            diagnostic=summary.summary if not summary.ok else "",
        )


def _report_dates_due(session, config) -> list:
    """Every report date that has at least one job, so summaries stay correct as the day
    fills in. A date is re-derived each pass until its rows are delivered."""
    from hexton.db.models import SheetReportRecord
    from hexton.domain.sync_key import parse_sync_key

    dates: set = set()
    for row in session.query(SheetReportRecord.sync_key).distinct().all():
        key = row[0]
        try:
            dates.add(parse_sync_key(key).report_date)
        except ValueError:  # pragma: no cover - defensive; keys are minted here
            continue
    return sorted(dates)


__all__ = ["ReportSyncResult", "ReportSyncSweep"]
