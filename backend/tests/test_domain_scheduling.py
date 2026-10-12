"""Scheduling rules: time zones on Windows, lead times, and the past-time hard fail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hexton.domain.scheduling import (
    DEFAULT_MIN_LEAD_MINUTES,
    ScheduleVerdict,
    ZoneError,
    check_publish_time,
    ensure_utc,
    has_missed_window,
    next_retry_at,
    resolve_zone,
    to_display,
)

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def test_windows_resolves_iana_zones_via_tzdata() -> None:
    """CPython on Windows ships no IANA database; ``tzdata`` is a hard dependency."""
    for key in ("Asia/Dhaka", "America/Los_Angeles", "UTC", "Europe/Berlin"):
        assert resolve_zone(key) is not None


def test_missing_zone_names_tzdata() -> None:
    with pytest.raises(ZoneError) as excinfo:
        resolve_zone("Not/A_Zone")
    assert "tzdata" in str(excinfo.value)


def test_blank_zone_is_rejected() -> None:
    with pytest.raises(ZoneError):
        resolve_zone("   ")


def test_dhaka_and_la_differ_by_the_documented_offset() -> None:
    """A regression guard for the zone database actually being loaded and correct."""
    dhaka = resolve_zone("Asia/Dhaka")
    la = resolve_zone("America/Los_Angeles")
    instant = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert instant.astimezone(dhaka).utcoffset() == timedelta(hours=6)
    assert instant.astimezone(la).utcoffset() == timedelta(hours=-7)


def test_naive_datetime_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="explicit time zone"):
        ensure_utc(datetime(2026, 10, 11, 12, 0))


def test_offset_carrying_datetime_normalises_to_utc() -> None:
    plus_six = datetime(2026, 10, 11, 18, 0, tzinfo=timezone(timedelta(hours=6)))
    assert ensure_utc(plus_six) == NOW


def test_display_formatting_uses_the_operator_zone() -> None:
    text = to_display(NOW, resolve_zone("Asia/Dhaka"))
    assert text is not None
    assert "18:00" in text
    assert "+06" in text or "Dhaka" in text


def test_none_display_is_none() -> None:
    assert to_display(None, resolve_zone("UTC")) is None


# ----------------------------------------------------------------------
# publishAt preflight
# ----------------------------------------------------------------------


def test_missing_publish_time_is_valid() -> None:
    check = check_publish_time(None, now=NOW)
    assert check.ok
    assert check.verdict is ScheduleVerdict.OK


def test_past_publish_time_is_a_hard_block() -> None:
    """DF-04: a past publishAt publishes immediately, so it is never corrected."""
    check = check_publish_time(NOW - timedelta(minutes=1), now=NOW)
    assert not check.ok
    assert check.verdict is ScheduleVerdict.MISSED
    codes = {i.code for i in check.blocking}
    assert "publish_at_in_past" in codes


def test_past_time_message_says_it_would_publish_immediately() -> None:
    check = check_publish_time(NOW - timedelta(hours=3), now=NOW)
    assert any("immediately" in i.message for i in check.blocking)


def test_too_soon_is_blocked_at_the_configured_minimum() -> None:
    check = check_publish_time(
        NOW + timedelta(minutes=DEFAULT_MIN_LEAD_MINUTES - 1), now=NOW, min_lead_minutes=30
    )
    assert check.verdict is ScheduleVerdict.TOO_SOON
    assert {i.code for i in check.blocking} == {"publish_at_too_soon"}


def test_inside_the_warning_band_is_allowed_but_flagged() -> None:
    check = check_publish_time(
        NOW + timedelta(minutes=45), now=NOW, min_lead_minutes=30, warning_lead_minutes=120
    )
    assert check.ok
    assert len(check.issues) == 1
    assert check.issues[0].blocking is False
    assert check.issues[0].code == "publish_at_near_lead"


def test_outside_the_warning_band_is_clean() -> None:
    check = check_publish_time(NOW + timedelta(days=2), now=NOW)
    assert check.ok
    assert check.issues == ()


def test_published_video_cannot_take_a_publish_time() -> None:
    """DF-03: publishAt applies only to a never-published video."""
    check = check_publish_time(NOW + timedelta(days=1), now=NOW, never_published=False)
    assert not check.ok
    assert {i.code for i in check.blocking} == {"already_published"}


def test_naive_publish_time_blocks() -> None:
    check = check_publish_time(datetime(2026, 12, 1, 12, 0), now=NOW)
    assert not check.ok
    assert {i.code for i in check.blocking} == {"naive_datetime"}


def test_naive_now_blocks() -> None:
    check = check_publish_time(NOW + timedelta(days=1), now=datetime(2026, 10, 11, 12, 0))
    assert not check.ok


# ----------------------------------------------------------------------
# missed window and backoff
# ----------------------------------------------------------------------


def test_missed_window_respects_the_grace_period() -> None:
    assert has_missed_window(NOW - timedelta(minutes=3), now=NOW, grace_minutes=5) is False
    assert has_missed_window(NOW - timedelta(minutes=10), now=NOW, grace_minutes=5) is True
    assert has_missed_window(None, now=NOW) is False


def test_backoff_is_exponential_and_capped() -> None:
    assert next_retry_at(1, now=NOW, base_seconds=30) == NOW + timedelta(seconds=30)
    assert next_retry_at(2, now=NOW, base_seconds=30) == NOW + timedelta(seconds=60)
    assert next_retry_at(3, now=NOW, base_seconds=30) == NOW + timedelta(seconds=120)
    assert next_retry_at(20, now=NOW, base_seconds=30, max_seconds=3600) == NOW + timedelta(
        seconds=3600
    )


def test_backoff_jitter_is_deterministic() -> None:
    """Jitter is a fraction of the delay, so the same input gives the same offset."""
    a = next_retry_at(3, now=NOW, base_seconds=30, jitter_ratio=0.2)
    b = next_retry_at(3, now=NOW, base_seconds=30, jitter_ratio=0.2)
    assert a == b
    assert a > next_retry_at(3, now=NOW, base_seconds=30)


def test_backoff_attempt_is_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        next_retry_at(0, now=NOW)


def test_backoff_refuses_a_naive_now() -> None:
    with pytest.raises(ValueError):
        next_retry_at(1, now=datetime(2026, 10, 11, 12, 0))
