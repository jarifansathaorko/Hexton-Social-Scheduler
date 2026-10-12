"""The storage convention: every timestamp is UTC, in and out.

``02`` §2.1 and ``08`` §7.6 make time-zone handling load-bearing, and this is where it
actually breaks if nobody is watching. SQLAlchemy's SQLite dialect renders a
``DateTime`` from the value's *wall clock* and discards the offset, so two failures
follow:

* ``next_attempt_at <= now`` compares ``18:00`` (Dhaka) against ``12:00`` (UTC) as
  though they were different hours — the same instant, treated as six hours apart.
* the column is read back naive, and :func:`hexton.domain.scheduling.ensure_utc`
  refuses a naive value, so any request that formatted a populated timestamp raised
  ``ValueError``.

The fix lives in :class:`hexton.db.types.UTCDateTime`. These tests pin it at three
levels: the schema, a real SQL comparison, and the operator-visible effect of moving
between time zones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import BASE, FUTURE, fresh, reload
from hexton.db.models import Base, Schedule, ScheduledJob
from hexton.db.types import UTCDateTime
from hexton.domain.scheduling import (
    check_publish_time,
    ensure_utc,
    resolve_zone,
    to_display,
)
from sqlalchemy import DateTime, select
from sqlalchemy.types import TypeDecorator

DHAKA = ZoneInfo("Asia/Dhaka")
BERLIN = ZoneInfo("Europe/Berlin")


def _is_a_datetime_column(column) -> bool:
    kind = column.type
    if isinstance(kind, TypeDecorator):
        return isinstance(kind.impl, DateTime)
    return isinstance(kind, DateTime)


def _datetime_columns():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if _is_a_datetime_column(column):
                yield table, column


# ----------------------------------------------------------------------#
# Schema
# ----------------------------------------------------------------------#


def test_every_timestamp_column_uses_the_utc_type() -> None:
    """One silent exception here would put the machine's zone back in charge of a job."""
    strays = [
        f"{table.name}.{column.key}"
        for table, column in _datetime_columns()
        if not isinstance(column.type, UTCDateTime)
    ]
    assert strays == []


def test_the_utc_type_covers_every_table_that_has_a_timestamp() -> None:
    tables = {table.name for table, _ in _datetime_columns()}
    # Sanity: this is not vacuously passing because no columns matched.
    assert len(list(_datetime_columns())) >= 20
    assert "scheduled_job" in tables
    assert "quota_ledger" in tables


def _declarative_models():
    for candidate in Base.registry._class_registry.values():
        if (
            isinstance(candidate, type)
            and candidate is not Base
            and issubclass(candidate, Base)
            and hasattr(candidate, "__tablename__")
        ):
            yield candidate


def test_every_timestamp_read_back_is_timezone_aware(ctx, new_job) -> None:
    """The API formats timestamps through ``ensure_utc``, which raises on naive values."""
    from hexton import quota
    from hexton.db.models import QuotaLedger

    new_job(publish_at=FUTURE)

    checked = 0
    with ctx.sessions() as s:
        for model in _declarative_models():
            for row in s.query(model).all():
                for column in model.__table__.columns:
                    if not _is_a_datetime_column(column):
                        continue
                    value = getattr(row, column.key, None)
                    if value is None:
                        continue
                    checked += 1
                    assert value.tzinfo is not None, (
                        f"{model.__tablename__}.{column.key} came back naive"
                    )
                    assert ensure_utc(value).tzinfo is UTC

    # Quota rows exist only after work has been done, so exercise that path too.
    quota.add_units(ctx.engine, "youtube", 1, BASE)
    with ctx.sessions() as s:
        row = s.query(QuotaLedger).one()
        assert row.window_start.tzinfo is not None
        assert row.window_start == quota.window_start(BASE)
        assert row.updated_at.tzinfo is not None

    assert checked > 0


# ----------------------------------------------------------------------#
# Comparison
# ----------------------------------------------------------------------#


def test_a_sql_comparison_uses_the_instant_not_the_wall_clock(ctx, new_job) -> None:
    """The bug, pinned: 18:00 Dhaka and 12:00Z are the same moment."""
    job = new_job(publish_at=None)
    dhaka_local = datetime(2026, 10, 11, 18, 0, tzinfo=DHAKA)
    utc_same_instant = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).schedule.publish_at_utc = dhaka_local
        s.commit()

    with ctx.sessions() as s:
        # Equal: the same instant expressed in another zone matches.
        match = s.execute(
            select(Schedule.id).where(Schedule.publish_at_utc == utc_same_instant)
        ).scalar()
        assert match is not None

        # Not before: a wall-clock comparison would have read 18:00 > 12:00 and
        # agreed, but for the wrong reason. A UTC+8 zone exposes the difference.
        assert (
            s.execute(
                select(Schedule.id).where(Schedule.publish_at_utc < utc_same_instant)
            ).scalar()
            is None
        )
        five_minutes_later = utc_same_instant + timedelta(minutes=5)
        assert (
            s.execute(
                select(Schedule.id).where(Schedule.publish_at_utc < five_minutes_later)
            ).scalar()
            is not None
        )


def test_the_value_round_trips_as_the_same_instant(ctx, new_job) -> None:
    job = new_job(publish_at=None)
    dhaka_local = datetime(2026, 10, 11, 18, 0, tzinfo=DHAKA)

    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).schedule.publish_at_utc = dhaka_local
        s.commit()

    row = reload(ctx, job.id)
    assert row.schedule.publish_at_utc == datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    assert row.schedule.publish_at_utc.utcoffset() == timedelta(0)


def test_a_naive_write_is_read_back_as_utc(ctx, new_job) -> None:
    """The schema's convention: an unlabelled value is already UTC, never local."""
    job = new_job(publish_at=None)
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).schedule.publish_at_utc = datetime(2026, 10, 11, 12, 0)
        s.commit()

    assert reload(ctx, job.id).schedule.publish_at_utc == datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------#
# Operator-visible effect
# ----------------------------------------------------------------------#


def test_changing_the_display_zone_does_not_move_the_stored_instant(
    ctx, new_job, monkeypatch
) -> None:
    """§8 item 6: a machine that changes zone must not silently move a job."""
    job = new_job(publish_at=FUTURE)
    before = reload(ctx, job.id).schedule.publish_at_utc

    monkeypatch.setattr(ctx.settings, "display_timezone", "Europe/Berlin")

    after = reload(ctx, job.id).schedule.publish_at_utc
    assert after == before
    assert after == FUTURE


def test_the_same_instant_renders_differently_in_each_zone(ctx, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    instant = reload(ctx, job.id).schedule.publish_at_utc

    dhaka_text = to_display(instant, resolve_zone("Asia/Dhaka"))
    berlin_text = to_display(instant, resolve_zone("Europe/Berlin"))

    assert "18:00" in dhaka_text  # 12:00Z is 18:00 in Dhaka
    assert "14:00" in berlin_text  # and 14:00 CEST in Berlin
    assert dhaka_text != berlin_text


def test_a_job_keeps_its_own_display_zone() -> None:
    """A job scheduled in Dhaka stays displayed in Dhaka after the operator moves."""
    instant = FUTURE
    assert "18:00" in to_display(instant, resolve_zone("Asia/Dhaka"))
    assert to_display(instant, resolve_zone("Asia/Dhaka")) != to_display(
        instant, resolve_zone("America/Los_Angeles")
    )


def test_the_verdict_depends_on_the_instant_not_the_zone(ctx, new_job) -> None:
    """The same UTC moment judged from two different machine zones agrees."""
    past_instant = BASE - timedelta(minutes=1)
    from_dhaka = check_publish_time(past_instant, now=BASE)
    from_la = check_publish_time(past_instant, now=BASE)
    assert from_dhaka.verdict == from_la.verdict
    assert not from_dhaka.ok


def test_daylight_saving_in_the_target_zone_does_not_shift_the_instant(ctx, new_job) -> None:
    """Berlin leaves DST on 25 Oct 2026; a job either side of it must not move."""
    before_transition = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    after_transition = datetime(2026, 10, 25, 2, 30, tzinfo=UTC)

    assert to_display(before_transition, resolve_zone("Europe/Berlin")).startswith(
        "2026-10-25 02:30"
    )
    assert to_display(after_transition, resolve_zone("Europe/Berlin")).startswith(
        "2026-10-25 03:30"
    )
    # One real hour apart on the clock, two in Berlin's wall clock.
    assert (after_transition - before_transition) == timedelta(hours=2)
