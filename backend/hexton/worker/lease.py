"""Job claiming, heartbeating, and lease recovery.

The lease is what makes two workers safe: a claim is a lease with an expiry, so a
worker that is killed mid-job does not hold the job forever. Recovery after a crash is
therefore not a special case — it is simply "expired leases become claimable again",
which is the same code path as a healthy hand-back.

All time comparisons use an injected ``now`` so tests can move the clock without
sleeping, and so the API and the worker cannot disagree about what "due" means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, and_, or_, select, update

from hexton.db.models import ScheduledJob
from hexton.db.session import immediate_transaction

#: States a worker is allowed to take. ``DRAFT`` and ``READY`` are excluded because a
#: job only becomes claimable once the operator has asked for it to run, and terminal
#: states are excluded because nothing may touch those at all.
CLAIMABLE_STATES = ("JOB-QUEUED", "JOB-FAILED_RETRY", "JOB-UPLOADING")


@dataclass(frozen=True)
class Claim:
    job_id: int
    public_id: str
    attempt: int


@dataclass(frozen=True)
class Lease:
    job_id: int
    owner: str
    expires_at: datetime


def _due_filter(now: datetime):
    return or_(ScheduledJob.next_attempt_at.is_(None), ScheduledJob.next_attempt_at <= now)


def _unleased(now: datetime):
    return or_(
        ScheduledJob.lease_owner.is_(None),
        ScheduledJob.lease_expires_at.is_(None),
        ScheduledJob.lease_expires_at < now,
    )


def claim_next(
    engine: Engine,
    *,
    owner: str,
    now: datetime,
    lease_seconds: int,
) -> Claim | None:
    """Atomically claim the next due job, or return ``None``.

    The whole read-modify-write happens inside ``BEGIN IMMEDIATE``, so the write lock
    is held before either worker reads. Without that, two workers can both read the
    same row and only find out when the first one writes — at which point the second
    worker has already spent an attempt, and in the worst case two uploads start for
    one schedule.

    The ``UPDATE`` repeats the lease condition and checks the row count, so even if a
    future caller wraps this in a transaction that already holds the lock, a stale
    claim cannot be applied.
    """
    now = _as_utc(now)
    expires = now + timedelta(seconds=lease_seconds)

    with immediate_transaction(engine) as conn:
        stmt = (
            select(ScheduledJob.id, ScheduledJob.public_id, ScheduledJob.attempt)
            .where(
                ScheduledJob.state.in_(CLAIMABLE_STATES),
                _due_filter(now),
                _unleased(now),
            )
            .order_by(ScheduledJob.next_attempt_at.asc().nullsfirst(), ScheduledJob.id.asc())
            .limit(1)
        )
        row = conn.execute(stmt).first()
        if row is None:
            return None

        updated = conn.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.id == row.id,
                ScheduledJob.state.in_(CLAIMABLE_STATES),
                _unleased(now),
            )
            .values(
                lease_owner=owner,
                lease_expires_at=expires,
                attempt=ScheduledJob.attempt + 1,
                updated_at=now,
            )
        )
        if updated.rowcount != 1:
            # Another writer won between the read and the write. Under IMMEDIATE this
            # should be unreachable; treat it as "nothing to do" rather than as an
            # error, because it is a legitimate outcome of a race.
            return None

        return Claim(job_id=row.id, public_id=row.public_id, attempt=row.attempt + 1)


def heartbeat(
    engine: Engine,
    *,
    job_id: int,
    owner: str,
    now: datetime,
    lease_seconds: int,
) -> bool:
    """Extend a held lease. Returns ``False`` when the lease was already lost.

    Losing a lease mid-job is a real possibility: the step can take longer than the
    lease, or the machine can stall. ``False`` tells the worker to stop touching this
    job immediately, because another worker may now own it.
    """
    now = _as_utc(now)
    with engine.connect() as conn:
        result = conn.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.lease_owner == owner)
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
        )
        conn.commit()
        return result.rowcount == 1


def release(
    engine: Engine,
    *,
    job_id: int,
    owner: str,
) -> bool:
    """Hand a job back by clearing its lease. Does not change state."""
    with engine.connect() as conn:
        result = conn.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.lease_owner == owner)
            .values(lease_owner=None, lease_expires_at=None, updated_at=datetime.now(UTC))
        )
        conn.commit()
        return result.rowcount == 1


def expire_stale_leases(engine: Engine, *, now: datetime) -> list[int]:
    """Clear leases whose owner died. Returns the affected job IDs.

    Called at worker startup and periodically afterwards. A job found here was
    interrupted — the worker was killed, the machine slept, or the process was closed.
    Its state is *not* changed: whatever step it was in stays recorded, and the runner
    decides what to do with it, which is where reconciliation belongs.
    """
    now = _as_utc(now)
    with engine.connect() as conn:
        result = conn.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.lease_owner.is_not(None),
                ScheduledJob.lease_expires_at.is_not(None),
                ScheduledJob.lease_expires_at < now,
            )
            .values(lease_owner=None, lease_expires_at=None, updated_at=now)
        )
        conn.commit()
        if result.rowcount == 0:
            return []
        # Re-read the ids that are now free but were leased a moment ago is not
        # possible after the fact, so select the currently-unleased set that is still
        # claimable. This is used only for logging and event emission.
        rows = conn.execute(
            select(ScheduledJob.id).where(
                ScheduledJob.lease_owner.is_(None),
                ScheduledJob.state.in_(CLAIMABLE_STATES),
            )
        )
        return [r.id for r in rows]


def held_leases(conn: Connection, *, now: datetime) -> list[tuple[int, str, datetime]]:
    """Every lease currently held. Used by the API's status endpoint."""
    rows = conn.execute(
        select(ScheduledJob.id, ScheduledJob.lease_owner, ScheduledJob.lease_expires_at).where(
            ScheduledJob.lease_owner.is_not(None)
        )
    )
    return [(r.id, r.lease_owner, r.lease_expires_at) for r in rows]


def leases_pending_expiry(
    conn: Connection, *, now: datetime, within_seconds: int
) -> list[tuple[int, str, datetime]]:
    """Leases expiring soon — surfaced so an operator can see a job is at risk."""
    horizon = now + timedelta(seconds=within_seconds)
    rows = conn.execute(
        select(ScheduledJob.id, ScheduledJob.lease_owner, ScheduledJob.lease_expires_at).where(
            and_(
                ScheduledJob.lease_owner.is_not(None),
                ScheduledJob.lease_expires_at.is_not(None),
                ScheduledJob.lease_expires_at <= horizon,
                ScheduledJob.lease_expires_at > now,
            )
        )
    )
    return [(r.id, r.lease_owner, r.lease_expires_at) for r in rows]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(
            "lease timestamps must be timezone-aware; a naive datetime makes lease "
            "expiry depend on the machine's local clock, which changes when the user "
            "travels or the OS adjusts for daylight saving."
        )
    return value.astimezone(UTC)
