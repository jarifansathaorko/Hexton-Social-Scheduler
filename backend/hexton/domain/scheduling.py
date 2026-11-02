"""Scheduling and time-zone rules.

Pure functions over ``datetime``. No clock reads here: the caller passes ``now`` so
every rule is deterministic and testable, and so the worker and the API can never
disagree about "now" within one decision.

Windows CPython ships no IANA database, so ``tzdata`` is a hard runtime dependency
(Phase 0 evidence, ``08-phase0-feasibility-report.md`` §7.6). :func:`resolve_zone`
is the single chokepoint that turns an IANA key into a zone, so a missing database
produces one clear error rather than scattered failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Default minimum lead time: 0 minutes (no forced delay).
DEFAULT_MIN_LEAD_MINUTES = 0

#: Warning band: 0 minutes by default (disabled).
DEFAULT_WARNING_LEAD_MINUTES = 0

#: A schedule whose time has passed by more than this is treated as missed rather
#: than as "about to happen", so the app never silently publishes late.
DEFAULT_MISSED_GRACE_MINUTES = 5


class ZoneError(ValueError):
    """An IANA zone key could not be resolved."""


def resolve_zone(key: str) -> ZoneInfo:
    """Resolve an IANA key, failing loudly and in one place if ``tzdata`` is absent."""
    if not key or not key.strip():
        raise ZoneError("A time-zone key is required.")
    try:
        return ZoneInfo(key.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ZoneError(
            f"Time zone {key!r} could not be resolved. On Windows this almost always "
            f"means the 'tzdata' package is not installed: CPython ships no system "
            f"IANA database. Install 'tzdata' and restart. (Original error: {exc})"
        ) from exc


def ensure_utc(value: datetime, *, field: str = "datetime") -> datetime:
    """Return ``value`` in UTC, refusing to guess a zone for a naive value."""
    if value.tzinfo is None:
        raise ValueError(
            f"{field} must carry an explicit time zone. A naive datetime is refused "
            f"rather than assumed, because guessing wrong silently shifts the "
            f"publication time."
        )
    return value.astimezone(UTC)


def to_display(value: datetime | None, zone: ZoneInfo) -> str | None:
    """Format a UTC instant for display in the operator's zone, or ``None``."""
    if value is None:
        return None
    return ensure_utc(value).astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


class ScheduleVerdict(str, Enum):
    OK = "ok"
    TOO_SOON = "too_soon"
    MISSED = "missed"
    PUBLISHED_WINDOW_OPEN = "published_window_open"


@dataclass(frozen=True)
class ScheduleIssue:
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class ScheduleCheck:
    verdict: ScheduleVerdict
    issues: tuple[ScheduleIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.verdict is ScheduleVerdict.OK and not any(i.blocking for i in self.issues)

    @property
    def blocking(self) -> tuple[ScheduleIssue, ...]:
        return tuple(i for i in self.issues if i.blocking)


def check_publish_time(
    publish_at: datetime | None,
    *,
    now: datetime,
    never_published: bool = True,
    min_lead_minutes: int = DEFAULT_MIN_LEAD_MINUTES,
    warning_lead_minutes: int = DEFAULT_WARNING_LEAD_MINUTES,
    missed_grace_minutes: int = DEFAULT_MISSED_GRACE_MINUTES,
) -> ScheduleCheck:
    """Pre-flight a publication time before any remote call is made.

    The blocking rules come straight from the Phase 0 documentation pins:

    * ``DF-04`` — a ``publishAt`` in the past is documented to publish the video
      **immediately**. Treating that as an ordinary validation error would be a
      silent publication, so it is a hard block.
    * ``DF-03`` — ``publishAt`` requires a never-published video.
    """
    issues: list[ScheduleIssue] = []

    if publish_at is None:
        return ScheduleCheck(ScheduleVerdict.OK)

    try:
        at = ensure_utc(publish_at, field="publish_at")
        reference = ensure_utc(now, field="now")
    except ValueError as exc:
        issues.append(ScheduleIssue("naive_datetime", str(exc), blocking=True))
        return ScheduleCheck(ScheduleVerdict.TOO_SOON, tuple(issues))

    if not never_published:
        # DF-03
        issues.append(
            ScheduleIssue(
                "already_published",
                "A publication time can only be set on a video that has never been "
                "published. For an already-published video this API field is not "
                "honoured, so the request is refused rather than silently ignored.",
                blocking=True,
            )
        )

    delta = at - reference

    if delta < timedelta(0):
        # DF-04
        issues.append(
            ScheduleIssue(
                "publish_at_in_past",
                "The publication time is in the past. YouTube treats a past publishAt "
                'as "publish immediately", so this is blocked rather than corrected.',
                blocking=True,
            )
        )
        return ScheduleCheck(ScheduleVerdict.MISSED, tuple(issues))

    if min_lead_minutes > 0 and delta < timedelta(minutes=min_lead_minutes):
        issues.append(
            ScheduleIssue(
                "publish_at_too_soon",
                f"The publication time is less than the configured minimum lead time "
                f"of {min_lead_minutes} minutes away.",
                blocking=True,
            )
        )
        return ScheduleCheck(ScheduleVerdict.TOO_SOON, tuple(issues))

    if warning_lead_minutes > 0 and delta < timedelta(minutes=warning_lead_minutes):
        issues.append(
            ScheduleIssue(
                "publish_at_near_lead",
                f"The publication time is only {int(delta.total_seconds() // 60)} minutes "
                f"away — inside the {warning_lead_minutes}-minute warning band. Queueing "
                f"is allowed, but if the PC sleeps the window may be missed.",
                blocking=False,
            )
        )

    return ScheduleCheck(ScheduleVerdict.OK, tuple(issues))


def next_retry_at(
    attempt: int,
    *,
    now: datetime,
    base_seconds: int = 30,
    max_seconds: int = 3600,
    jitter_ratio: float = 0.0,
) -> datetime:
    """Exponential backoff with an optional deterministic jitter.

    ``jitter_ratio`` is a fraction of the computed delay; callers pass a value derived
    from the job's own ID so the same job always gets the same offset. Randomness here
    would make tests flaky and would make a restart re-derive a different schedule.
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    delay = min(base_seconds * (2 ** (attempt - 1)), max_seconds)
    if jitter_ratio:
        delay += int(delay * jitter_ratio)
    return ensure_utc(now) + timedelta(seconds=delay)


def has_missed_window(
    publish_at: datetime | None,
    *,
    now: datetime,
    grace_minutes: int = DEFAULT_MISSED_GRACE_MINUTES,
) -> bool:
    """True when a scheduled time has passed by enough that we should not assume
    the job simply has not run yet."""
    if publish_at is None:
        return False
    delta = ensure_utc(now) - ensure_utc(publish_at)
    return delta > timedelta(minutes=grace_minutes)


def get_peak_hour_weight(dt: datetime, target_tz: str = "America/New_York") -> float:
    """Calculate audience attention weight (0.35 to 1.0) based on local viewer peak hours.

    Algorithmic viewer attention windows:
    - Prime Evening leisure: 17:30 - 21:30 local time (score: 1.0)
    - Midday Lunch break: 12:00 - 15:00 local time (score: 0.92)
    - Late afternoon prep: 15:00 - 17:30 local time (score: 0.82)
    - Morning commute: 07:30 - 09:30 local time (score: 0.78)
    - Off-peak / late night: (score: 0.40)
    """
    try:
        zone = ZoneInfo(target_tz)
    except Exception:
        zone = ZoneInfo("UTC")
    local_time = ensure_utc(dt).astimezone(zone)
    h = local_time.hour + local_time.minute / 60.0

    if 17.5 <= h <= 21.5:
        return 1.0
    elif 12.0 <= h <= 15.0:
        return 0.92
    elif 15.0 < h < 17.5:
        return 0.82
    elif 7.5 <= h <= 9.5:
        return 0.78
    elif 9.5 < h < 12.0:
        return 0.72
    elif 21.5 < h <= 23.5:
        return 0.65
    else:
        return 0.40


def find_optimal_slots(
    channel_existing_slots: list[datetime],
    *,
    start_time: datetime,
    days_ahead: int = 3,
    min_gap_hours: float = 2.5,
    target_tz: str = "America/New_York",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Algorithm to compute collision-free, audience-optimized publication slots.

    Prevents channel self-cannibalization by strictly enforcing min_gap_hours between uploads.
    Ranks candidate slots by viewer peak hour scoring and spacing clearance.
    """
    start = ensure_utc(start_time)
    existing = sorted(
        [ensure_utc(dt) for dt in channel_existing_slots if ensure_utc(dt) >= start - timedelta(days=1)]
    )

    candidates: list[dict[str, Any]] = []

    try:
        zone = ZoneInfo(target_tz)
    except Exception:
        zone = ZoneInfo("UTC")

    # Round to the next 30-minute interval at least 30 minutes from start
    current = start + timedelta(minutes=30)
    current = current.replace(
        minute=0 if current.minute < 30 else 30, second=0, microsecond=0
    )
    end_window = start + timedelta(days=days_ahead)

    while current <= end_window:
        too_close = False
        min_distance_hours = float("inf")
        for ex in existing:
            dist = abs((current - ex).total_seconds()) / 3600.0
            if dist < min_distance_hours:
                min_distance_hours = dist
            if dist < min_gap_hours:
                too_close = True
                break

        if not too_close:
            peak_weight = get_peak_hour_weight(current, target_tz)
            spacing_bonus = min(min_distance_hours / 6.0, 1.0) * 0.15
            total_score = min(round((peak_weight * 0.85 + spacing_bonus) * 100, 1), 99.9)

            local_dt = current.astimezone(zone)
            local_str = local_dt.strftime("%b %d, %I:%M %p")
            h = local_dt.hour + local_dt.minute / 60.0

            label = (
                "Prime Evening Peak"
                if 17.5 <= h <= 21.5
                else "Midday Peak"
                if 12.0 <= h <= 15.0
                else "Cadence Recommended"
            )

            tz_display = zone.key.split("/")[-1].replace("_", " ")
            candidates.append(
                {
                    "publish_at_utc": current.isoformat(),
                    "display_local": f"{local_str} ({tz_display})",
                    "score": total_score,
                    "label": label,
                    "clearance_hours": round(min_distance_hours, 1) if min_distance_hours < 100 else 24.0,
                    "peak_weight": round(peak_weight, 2),
                }
            )

        current += timedelta(minutes=30)

    # Sort descending by score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:limit]
