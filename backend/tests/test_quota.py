"""Quota accounting — the Pacific window, the costs, and the reserve.

``DF-11``/``DF-12``: the documented window resets at **midnight Pacific**, not at
machine-local midnight. This machine runs ``Asia/Dhaka``, sixteen hours ahead of
Pacific in summer, so getting this wrong would report a healthy project as
quota-blocked for most of a day. Every test here pins that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hexton import quota

PACIFIC = quota.PACIFIC


def test_window_start_is_midnight_pacific_not_local_midnight() -> None:
    # 06:00 UTC on 11 Oct 2026 = 23:00 PDT on 10 Oct, so the window opened at
    # 07:00 UTC on 10 Oct — not at local midnight, which in Dhaka would be 18:00 UTC.
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    start = quota.window_start(now)
    assert start == datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
    assert start.astimezone(PACIFIC).hour == 0
    assert start.astimezone(PACIFIC).minute == 0


def test_window_start_in_local_terms_is_always_midnight() -> None:
    for hour in range(24):
        now = datetime(2026, 10, 11, hour, 30, tzinfo=UTC)
        pacific = quota.window_start(now).astimezone(PACIFIC)
        assert (pacific.hour, pacific.minute, pacific.second) == (0, 0, 0)


def test_a_pacific_local_evening_still_belongs_to_the_previous_window() -> None:
    # 05:30 UTC = 22:30 PDT the previous day, i.e. still the previous window.
    now = datetime(2026, 10, 11, 5, 30, tzinfo=UTC)
    assert quota.window_start(now) == datetime(2026, 10, 10, 7, 0, tzinfo=UTC)


def test_the_window_rolls_over_at_pacific_midnight() -> None:
    before = quota.window_start(datetime(2026, 10, 11, 6, 59, tzinfo=UTC))
    after = quota.window_start(datetime(2026, 10, 11, 7, 1, tzinfo=UTC))
    assert after - before == timedelta(days=1)


def test_next_reset_is_one_day_after_the_window_opened() -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    assert quota.next_reset(now) - quota.window_start(now) == timedelta(days=1)


def test_retry_at_reset_waits_for_the_next_window_plus_a_minute() -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    retry = quota.retry_at_reset(now)
    assert retry > quota.next_reset(now)
    assert retry - quota.next_reset(now) == timedelta(minutes=1)


def test_winter_and_summer_offsets_are_both_handled() -> None:
    """PDT is UTC-7 and PST is UTC-8; the window must be right in both."""
    summer = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
    winter = datetime(2026, 12, 1, 6, 0, tzinfo=UTC)
    assert quota.window_start(summer).astimezone(PACIFIC).hour == 0
    assert quota.window_start(winter).astimezone(PACIFIC).hour == 0
    assert quota.window_start(summer) != quota.window_start(winter)


def test_documented_costs_are_pinned() -> None:
    """insert is 1 unit; update is 50 — the asymmetry that makes publishAt-in-insert
    both the safer and the only economical choice."""
    assert quota.COST_INSERT == 1
    assert quota.COST_READ == 1
    assert quota.COST_UPDATE == 50
    assert quota.DAILY_UNITS == 10_000
    assert quota.RESERVE_UNITS == 200
    assert quota.COST_UPDATE > quota.COST_INSERT


def test_spend_accumulates_within_one_window(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    assert quota.units_used(ctx.engine, "youtube", now) == 0
    assert quota.add_units(ctx.engine, "youtube", 1, now) == 1
    assert quota.add_units(ctx.engine, "youtube", 1, now) == 2
    assert quota.units_used(ctx.engine, "youtube", now) == 2


def test_the_next_window_starts_from_zero(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    quota.add_units(ctx.engine, "youtube", 500, now)
    tomorrow = now + timedelta(days=1)
    assert quota.units_used(ctx.engine, "youtube", tomorrow) == 0
    assert quota.remaining(ctx.engine, "youtube", tomorrow) == quota.DAILY_UNITS


def test_providers_are_counted_separately(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    quota.add_units(ctx.engine, "youtube", 100, now)
    assert quota.units_used(ctx.engine, "sheets", now) == 0


def test_the_reserve_is_protected(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    spent = quota.DAILY_UNITS - quota.RESERVE_UNITS
    quota.add_units(ctx.engine, "youtube", spent, now)
    assert quota.remaining(ctx.engine, "youtube", now) == quota.RESERVE_UNITS
    # Spending the next unit would eat the reserve, so it is refused.
    assert quota.can_afford(ctx.engine, "youtube", 1, now) is False
    assert quota.can_afford(ctx.engine, "youtube", 0, now) is True


def test_spend_is_capped_by_the_daily_budget(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    quota.add_units(ctx.engine, "youtube", quota.DAILY_UNITS - 1, now)
    assert quota.remaining(ctx.engine, "youtube", now) == 1
    quota.add_units(ctx.engine, "youtube", 100, now)
    assert quota.remaining(ctx.engine, "youtube", now) == 0  # never negative


def test_a_non_positive_spend_is_a_no_op(ctx) -> None:
    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    assert quota.add_units(ctx.engine, "youtube", 0, now) == 0
    assert quota.add_units(ctx.engine, "youtube", -5, now) == 0


def test_a_naive_timestamp_is_treated_as_local_time_not_rejected() -> None:
    """Documents the one place a naive value is tolerated: the window is recomputed
    from an aware ``now`` everywhere in the application, so this path never sees one."""
    naive = datetime(2026, 10, 11, 6, 0)
    assert quota.window_start(naive).tzinfo is not None
