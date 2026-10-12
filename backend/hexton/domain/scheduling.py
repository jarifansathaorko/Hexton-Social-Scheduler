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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Placeholder pending FEAS-03. Recorded as an open decision (D-4) in ``02`` §15.
#: It is deliberately generous: a shorter window risks YouTube rejecting the job
#: after the upload has already been paid for in quota.
DEFAULT_MIN_LEAD_MINUTES = 30

#: Inside this band a schedule is legal but worth flagging to the operator.
DEFAULT_WARNING_LEAD_MINUTES = 120

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

    if delta < timedelta(minutes=min_lead_minutes):
        issues.append(
            ScheduleIssue(
                "publish_at_too_soon",
                f"The publication time is less than the configured minimum lead time "
                f"of {min_lead_minutes} minutes away. This minimum is a conservative "
                f"placeholder (open decision D-4) pending FEAS-03.",
                blocking=True,
            )
        )
        return ScheduleCheck(ScheduleVerdict.TOO_SOON, tuple(issues))

    if delta < timedelta(minutes=warning_lead_minutes):
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
