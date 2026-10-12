"""The worker process: a loop that claims due jobs and runs the publication sweep.

It is a *separate process* from the API by construction, not by configuration. The
browser never executes a schedule and closing it never stops work: the only thing that
stops a job is the worker process ending, and even then the lease expires and another
worker picks the job up from where it was.

Startup order matters and is deliberate:

1. Reclaim leases held by workers that no longer exist, so an interrupted job is
   available again immediately rather than after a lease timeout.
2. Expire anything whose lease ran out while the machine was asleep.
3. Then claim.

Recovery is therefore ordinary rather than special — an interrupted job re-enters the
same queue as a fresh one, and the runner's reconciliation step decides whether it may
continue the existing session.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from hexton.bootstrap import AppContext
from hexton.compliance.retention import RetentionSweeper
from hexton.worker.heartbeat import beat
from hexton.worker.lease import expire_stale_leases
from hexton.worker.report_sync import ReportSyncResult, ReportSyncSweep
from hexton.worker.runner import JobRunner, RunResult
from hexton.worker.sweep import PublishConfirmationSweep, SweepResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickReport:
    claimed: RunResult | None
    sweep: SweepResult | None
    report_sync: ReportSyncResult | None
    idle: bool


class Worker:
    """Owns the claim loop. One instance per process."""

    def __init__(
        self,
        ctx: AppContext,
        *,
        owner: str | None = None,
        sweep_interval_seconds: float = 5.0,
        report_sync_interval_seconds: float = 30.0,
    ) -> None:
        self.ctx = ctx
        self.runner = JobRunner(ctx, owner=owner)
        self.sweep = PublishConfirmationSweep(ctx)
        self.report_sync = ReportSyncSweep(ctx)
        self.sweep_interval = sweep_interval_seconds
        self.report_sync_interval = report_sync_interval_seconds
        self.stop = threading.Event()
        self._last_sweep = 0.0
        self._last_report_sync = 0.0
        self._jobs_claimed = 0
        self._started_at: datetime | None = None

    def _beat(self, now: datetime) -> None:
        """Publish liveness so ``/api/health`` can answer honestly.

        The API is another process and can see nothing else about this one, so the
        heartbeat row is the whole of its evidence. Failures are logged rather than
        raised: a worker that cannot write its own heartbeat should keep trying to
        publish jobs, and the API will simply report it as not running meanwhile.
        """
        try:
            session = self.ctx.sessions()
            try:
                beat(
                    session,
                    owner=self.runner.owner,
                    jobs_claimed=self._jobs_claimed,
                    pid=os.getpid(),
                    started_at=self._started_at,
                    now=now,
                )
            finally:
                session.close()
        except Exception:  # pragma: no cover - only on a database failure
            log.warning("Could not record a worker heartbeat.", exc_info=True)

    # ------------------------------------------------------------------

    def request_stop(self, *_: object) -> None:
        """Signal handler target. Stops the loop after the current job."""
        log.info("Shutdown requested; finishing the current step before exiting.")
        self.stop.set()

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Not installable from a non-main thread, which is exactly where the
            # tests drive this from; skipping is not an error.
            with suppress(ValueError, OSError):  # pragma: no cover
                signal.signal(sig, self.request_stop)

    # ------------------------------------------------------------------

    def startup(self, *, now: datetime | None = None) -> list[int]:
        """Recover from whatever happened while this process was not running."""
        now = now or datetime.now(UTC)
        orphans = self.runner.reclaim_orphans(now=now)
        expire_stale_leases(self.ctx.engine, now=now)
        # Retention runs at startup as well as on a timer, so a machine that was off
        # for a month still closes its deletion deadlines when it comes back
        # (``DF-20``/``DF-21``).
        RetentionSweeper(self.ctx).run(now=now)
        self._started_at = now
        self._beat(now)
        log.info(
            "Worker %s ready; reclaimed %d interrupted job(s).", self.runner.owner, len(orphans)
        )
        return orphans

    def tick(
        self,
        *,
        now: datetime | None = None,
        force_sweep: bool = False,
        force_report_sync: bool = False,
    ) -> TickReport:
        """One pass: at most one job, plus the sweeps when they are due."""
        now = now or datetime.now(UTC)
        result = self.runner.run_once(now=now)
        if result is not None:
            self._jobs_claimed += 1

        sweep_report: SweepResult | None = None
        due = (time.monotonic() - self._last_sweep) >= self.sweep_interval
        if force_sweep or due:
            sweep_report = self.sweep.run(now=now)
            self._last_sweep = time.monotonic()

        # Reporting is a separate, slower cadence: it is not latency-sensitive and every
        # pass costs a read of the destination. It runs *after* the job and the publish
        # sweep, and it cannot affect either — see :mod:`hexton.worker.report_sync`.
        report_result: ReportSyncResult | None = None
        report_due = (time.monotonic() - self._last_report_sync) >= self.report_sync_interval
        if force_report_sync or report_due:
            try:
                report_result = self.report_sync.run(now=now)
            except Exception:
                log.warning("Report synchronization raised; the worker continues.", exc_info=True)
            self._last_report_sync = time.monotonic()

        # Beat after the work, so the timestamp is evidence that a pass actually
        # completed rather than that a pass was started.
        self._beat(now)

        return TickReport(
            claimed=result, sweep=sweep_report, report_sync=report_result, idle=result is None
        )

    def run_forever(self, *, poll_seconds: float | None = None) -> None:
        """Block until :meth:`request_stop`. Safe to kill at any point."""
        poll = poll_seconds if poll_seconds is not None else self.ctx.settings.worker_poll_seconds
        self.startup()
        log.info("Worker loop started (polling every %.1fs).", poll)

        while not self.stop.is_set():
            report = self.tick()
            if report.idle and report.sweep is None:
                # Nothing to do: sleep, but wake promptly on shutdown so Ctrl+C is
                # immediate rather than "within one poll interval".
                self.stop.wait(poll)

        self.ctx.close()
        log.info("Worker stopped cleanly.")


def run_worker(ctx: AppContext) -> int:
    """Entry point used by ``python -m hexton.worker`` and by the packaged launcher."""
    worker = Worker(ctx)
    worker.install_signal_handlers()
    worker.run_forever()
    return 0
