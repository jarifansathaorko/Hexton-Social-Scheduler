"""Provider quota accounting.

The documented budget is 10,000 units per project per day and the window resets at
**midnight Pacific**, not at local midnight (``08`` §7.4, DF-11/DF-12). This machine
runs ``Asia/Dhaka``, sixteen hours ahead of Pacific in summer, so computing the window
from the machine clock would declare the budget spent a whole day early and would then
report a healthy project as quota-blocked for sixteen hours.

``videos.insert`` costs 1 unit; ``videos.update`` costs **50**. That asymmetry is why
setting ``publishAt`` inside the initial insert is both the safer choice and the only
economical one — a follow-up reschedule spends fifty times as much.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from hexton.db.models import QuotaLedger

#: Documented per-day project budget (``08`` §7.4).
DAILY_UNITS = 10_000

#: Reserve kept back so a sweep or a reschedule is never the thing that exhausts the
#: budget. The upload queue yields to it.
RESERVE_UNITS = 200

#: Documented per-call costs.
COST_INSERT = 1
COST_UPDATE = 50
COST_READ = 1

PACIFIC = ZoneInfo("America/Los_Angeles")


def window_start(now: datetime) -> datetime:
    """The start of the quota window containing ``now``: midnight Pacific."""
    local = now.astimezone(PACIFIC)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def next_reset(now: datetime) -> datetime:
    return window_start(now) + timedelta(days=1)


def _window_where(provider: str, now: datetime):
    start = window_start(now)
    return QuotaLedger.provider == provider, QuotaLedger.window_start == start


def _upsert(provider: str, now: datetime, units: int):
    """The single statement that adds spend to the current window.

    An upsert rather than read-then-write, so two processes racing on the same window
    cannot lose an update — which matters here because the API and the worker are
    separate processes.
    """
    start = window_start(now)
    return (
        sqlite_insert(QuotaLedger)
        .values(provider=provider, window_start=start, units_used=units)
        .on_conflict_do_update(
            index_elements=["provider", "window_start"],
            set_={"units_used": QuotaLedger.units_used + units},
        )
    )


def units_used_on(session: Session, provider: str, now: datetime) -> int:
    """Read spend through a session that is already open."""
    provider_filter, window_filter = _window_where(provider, now)
    row = session.execute(
        select(QuotaLedger.units_used).where(provider_filter, window_filter)
    ).scalar_one_or_none()
    return int(row or 0)


def add_units_on(session: Session, provider: str, units: int, now: datetime) -> int:
    """Charge ``units`` **on the caller's own connection** and return the new total.

    Deliberately not a fresh engine connection. The worker's session almost always
    holds an uncommitted write at the moment it charges for a call — ``_preflight``
    has already stamped the media digest, and the upload row is written before the
    session is opened — and SQLite permits exactly one writer at a time. A second
    connection would block for ``busy_timeout`` and then fail outright with
    ``database is locked``, so every job that reached a provider call would appear to
    hang for fifteen seconds before dying.

    Not committing here is intentional: the spend becomes durable in the same commit
    as the job transition that caused it, so a rolled-back pass does not charge a
    provider call that never happened.
    """
    if units > 0:
        session.execute(_upsert(provider, now, units))
        # The upsert is not an ORM change, so read it back from the same
        # transaction rather than letting the identity map answer.
        session.flush()
    return units_used_on(session, provider, now)


def units_used(engine: Engine, provider: str, now: datetime) -> int:
    with Session(engine) as session:
        return units_used_on(session, provider, now)


def add_units(engine: Engine, provider: str, units: int, now: datetime) -> int:
    """Engine-level convenience wrapper for code that holds no open session.

    Anything with a session already open must use :func:`add_units_on` instead — see
    the note there about SQLite's single-writer rule.
    """
    with Session(engine) as session:
        total = add_units_on(session, provider, units, now)
        session.commit()
        return total


def remaining(engine: Engine, provider: str, now: datetime) -> int:
    return max(0, DAILY_UNITS - units_used(engine, provider, now))


def remaining_on(session: Session, provider: str, now: datetime) -> int:
    return max(0, DAILY_UNITS - units_used_on(session, provider, now))


def can_afford(engine: Engine, provider: str, units: int, now: datetime) -> bool:
    """Whether ``units`` may be spent without eating the reserve."""
    return remaining(engine, provider, now) - units >= RESERVE_UNITS


def can_afford_on(session: Session, provider: str, units: int, now: datetime) -> bool:
    return remaining_on(session, provider, now) - units >= RESERVE_UNITS


def retry_at_reset(now: datetime) -> datetime:
    """When a quota-blocked job may try again.

    Deliberately the *next* reset rather than "an hour later": a quota error is not a
    transient condition, and retrying through the rest of the window only burns calls
    that are already unavailable.
    """
    return next_reset(now) + timedelta(minutes=1)
