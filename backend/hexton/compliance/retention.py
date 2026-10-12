"""Data retention and deletion.

``COMP-03``/``COMP-04`` require the application to delete data on request and within a
deadline after a revocation, and ``DF-20``/``DF-21`` fix that deadline at **7 calendar
days** for a client-initiated revocation and **30** where the user revoked through
Google's security settings page or the token stopped refreshing.

The important design point is that a deadline is a *record*, not a reminder. Revoking
writes a :class:`~hexton.db.models.DataRetention` row with an absolute deadline, and
this module's job is to make sure no row is ever still open when its deadline passes.
An operator who revoked and then closed the application for a month must still be
compliant when it reopens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hexton.bootstrap import AppContext
from hexton.db.models import (
    ChannelIdentity,
    DataRetention,
    JobEvent,
    PlatformConnection,
    RemoteObservation,
    ScheduledJob,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgeReport:
    connections_purged: int
    retention_rows_closed: int
    events_pruned: int
    observations_pruned: int


class RetentionSweeper:
    """Applies retention rules. Runs at worker startup and then daily."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def run(self, *, now: datetime | None = None) -> PurgeReport:
        now = now or datetime.now(UTC)
        with self.ctx.sessions() as session:
            overdue = list(
                session.execute(
                    select(DataRetention).where(
                        DataRetention.purged_at.is_(None), DataRetention.deadline <= now
                    )
                ).scalars()
            )
            purged = 0
            for row in overdue:
                purged += self._purge_connection(session, row, now)
                row.purged_at = now
                row.purge_note = "Purged automatically after the retention deadline."
            events, observations = self._prune_evidence(session, now)
            session.commit()
        if overdue:
            log.warning(
                "Retention: purged %d connection(s) past deadline and pruned %d "
                "event(s) / %d observation(s).",
                purged,
                events,
                observations,
            )
        return PurgeReport(purged, len(overdue), events, observations)

    def _purge_connection(self, session: Session, row: DataRetention, now: datetime) -> int:
        """Delete everything derived from an authorization whose clock has run out."""
        conn = session.execute(
            select(PlatformConnection).where(PlatformConnection.public_id == row.subject_id)
        ).scalar_one_or_none()
        if conn is None:
            return 0

        for name in ("access", "refresh"):
            self.ctx.credentials.delete(f"conn:{conn.public_id}:{name}")

        for identity in list(conn.identities):
            session.delete(identity)

        conn.status = "deleted"
        conn.scopes = []
        conn.authorized_data_deleted_at = conn.authorized_data_deleted_at or now
        conn.label = f"deleted:{conn.public_id[:8]}"
        return 1

    def _prune_evidence(self, session: Session, now: datetime) -> tuple[int, int]:
        """Drop old evidence for jobs that are finished.

        Finished means terminal: a job that can still change has to keep its history,
        because that history is what an operator reads when deciding what to do. A
        cancelled or published job from a year ago does not.
        """
        horizon = now - timedelta(days=self.ctx.settings.retention_days)
        terminal = ("JOB-PUBLISHED", "JOB-CANCELLED", "JOB-FAILED")

        jobs = (
            session.execute(select(ScheduledJob).where(ScheduledJob.state.in_(terminal)))
            .scalars()
            .all()
        )
        job_ids = [j.id for j in jobs]
        if not job_ids:
            return 0, 0

        events = (
            session.execute(
                select(JobEvent).where(JobEvent.job_id.in_(job_ids), JobEvent.occurred_at < horizon)
            )
            .scalars()
            .all()
        )
        for event in events:
            session.delete(event)

        observations = (
            session.execute(
                select(RemoteObservation).where(
                    RemoteObservation.job_id.in_(job_ids), RemoteObservation.observed_at < horizon
                )
            )
            .scalars()
            .all()
        )
        for observation in observations:
            session.delete(observation)

        return len(events), len(observations)


@dataclass(frozen=True)
class RetentionStatus:
    """What is still open, and when it is due. For the compliance panel."""

    open_deadlines: tuple[tuple[str, str, datetime], ...]  # (reason, subject, deadline)
    #: The longest a piece of Authorized Data may still survive, in calendar days.
    worst_case_days_remaining: int
    #: Whether anything is overdue. True means the sweep has not run since a deadline
    #: passed, which is the condition an operator needs to see rather than a silent
    #: background correction.
    overdue: bool


def status(session: Session, *, now: datetime) -> RetentionStatus:
    """The retention ledger as it stands. Never contacts anything."""
    rows = list(
        session.execute(
            select(DataRetention)
            .where(DataRetention.purged_at.is_(None))
            .order_by(DataRetention.deadline)
        ).scalars()
    )
    open_rows = tuple((r.reason, r.subject_id, r.deadline) for r in rows)
    worst = max(((r.deadline.date() - now.date()).days for r in rows), default=0)
    return RetentionStatus(
        open_deadlines=open_rows,
        worst_case_days_remaining=max(worst, 0),
        overdue=any(r.deadline <= now for r in rows),
    )


def schedule_for_unrefreshable_token(
    session: Session, connection: PlatformConnection, *, now: datetime
) -> DataRetention:
    """Start the 30-calendar-day clock when a grant stops being refreshable.

    ``DF-21`` / ``revoke_outcome(initiated_by="unrefreshable")``. Distinct from a
    client-initiated revocation because the operator did not ask for this and may not
    have noticed; the longer window reflects that, and recording it means the deadline
    exists whether or not anyone remembers the token failed.
    """
    from hexton.compliance.policy import revoke_outcome

    outcome = revoke_outcome(initiated_by="unrefreshable")
    row = DataRetention(
        subject_type="connection",
        subject_id=connection.public_id,
        reason="revocation_google",
        deadline=now + timedelta(days=outcome.authorized_data_retention_days),
    )
    session.add(row)
    session.flush()
    log.warning(
        "Grant for connection %s can no longer be refreshed; Authorized Data is due for "
        "deletion by %s (DF-21).",
        connection.public_id[:8],
        row.deadline.date().isoformat(),
    )
    return row


def delete_everything_for_connection(
    ctx: AppContext,
    session: Session,
    connection: PlatformConnection,
    *,
    now: datetime,
) -> PurgeReport:
    """Operator-requested permanent deletion of one connection's data.

    Immediate rather than scheduled: ``COMP-04`` requires honoring a deletion request,
    and making an operator wait for a deadline they did not choose would be a worse
    answer than the rule itself demands.
    """
    for name in ("access", "refresh"):
        ctx.credentials.delete(f"conn:{connection.public_id}:{name}")
    for identity in list(connection.identities):
        session.delete(identity)
    jobs = (
        session.execute(select(ScheduledJob).where(ScheduledJob.connection_id == connection.id))
        .scalars()
        .all()
    )
    for job in jobs:
        for observation in list(job.observations):
            session.delete(observation)
        for event in list(job.events):
            session.delete(event)
        session.delete(job)
    connection.status = "deleted"
    connection.scopes = []
    connection.label = f"deleted:{connection.public_id[:8]}"
    connection.authorized_data_deleted_at = now
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=connection.public_id,
            reason="operator_delete",
            deadline=now,
            purged_at=now,
            purge_note="Deleted at the operator's request.",
        )
    )
    return PurgeReport(1, 1, 0, 0)


_ = ChannelIdentity
