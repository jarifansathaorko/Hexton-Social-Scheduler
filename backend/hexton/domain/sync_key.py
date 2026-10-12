"""The Google Sheets report sync key.

Shape: ``SCH_<report_date>_<schedule_id>_<schema_version>``

The key is the only idempotency mechanism available for ``spreadsheets.values.append``,
which is **not** an idempotent operation: a retry after a dropped response writes a
second row. So the key must be deterministic across runs and across restarts, and it
must be parseable back into its parts so a conflict can be named precisely.

Pure functions. No I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

PREFIX = "SCH"
#: Daily completion summaries share the key machinery but must never collide with an
#: upload row's key, so they carry their own prefix. Both are read back from column A
#: and deduplicated by the same protocol.
SUMMARY_PREFIX = "SUM"
DEFAULT_SCHEMA_VERSION = "v1"

#: A schedule ID may not contain a character that would make the key unparseable.
_FORBIDDEN_IN_ID = re.compile(r"[\s|,\t\r\n/]")

_KEY_RE = re.compile(
    r"^(?P<prefix>[A-Z]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<id>[A-Z0-9._-]+)_(?P<version>v\d+)$"
)

KEY_COLUMN = "Sync Key"
REQUIRED_COLUMNS = ("Report Date", "Schedule ID", KEY_COLUMN)


@dataclass(frozen=True)
class SyncKey:
    report_date: date
    schedule_id: str
    schema_version: str = DEFAULT_SCHEMA_VERSION

    def __str__(self) -> str:
        return f"{PREFIX}_{self.report_date.isoformat()}_{self.schedule_id}_{self.schema_version}"


def normalise_schedule_id(schedule_id: str) -> str:
    """Fold a schedule ID into its canonical form.

    Case and surrounding whitespace are folded so one job can never acquire two
    identities just because two runs disagreed about capitalisation.
    """
    return (schedule_id or "").strip().upper()


def make_sync_key(
    report_date: date,
    schedule_id: str,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> str:
    """Build the key. Deterministic, and strict about what it will accept."""
    if report_date is None:
        raise ValueError("report_date is required")
    raw = schedule_id or ""
    if not raw.strip():
        raise ValueError("schedule_id is required to build a sync key")
    if _FORBIDDEN_IN_ID.search(raw):
        raise ValueError(
            f"schedule_id {raw!r} must not contain whitespace or any of | , tab "
            f"newline /: the resulting key could not be parsed back, and collisions "
            f"would become possible."
        )
    normalised = normalise_schedule_id(raw)
    if not re.fullmatch(r"[A-Z0-9._-]+", normalised):
        raise ValueError(f"schedule_id {raw!r} normalises to an unusable key part.")
    return str(SyncKey(report_date, normalised, schema_version))


def is_valid_key(key: str) -> bool:
    if not key:
        return False
    match = _KEY_RE.match(key.strip())
    if not match or match.group("prefix") != PREFIX:
        return False
    try:
        date.fromisoformat(match.group("date"))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ParsedKey:
    report_date: date
    schedule_id: str
    schema_version: str


def parse_sync_key(key: str) -> ParsedKey:
    """Inverse of :func:`make_sync_key`. Raises when the key is malformed."""
    if not is_valid_key(key):
        raise ValueError(f"{key!r} is not a valid sync key.")
    match = _KEY_RE.match(key.strip())
    assert match is not None  # guarded by is_valid_key
    return ParsedKey(
        report_date=date.fromisoformat(match.group("date")),
        schedule_id=match.group("id"),
        schema_version=match.group("version"),
    )


# --------------------------------------------------------------------------
# Daily completion summaries
# --------------------------------------------------------------------------
#
# A summary is per report date per channel, not per job, so it has no schedule ID. It
# still needs a deterministic key, because ``spreadsheets.values.append`` is not
# idempotent for a summary row either, and "the same day synced twice" is exactly the
# case a daily report invites.


@dataclass(frozen=True)
class SummarySyncKey:
    report_date: date
    channel_id: str
    schema_version: str = DEFAULT_SCHEMA_VERSION

    def __str__(self) -> str:
        return (
            f"{SUMMARY_PREFIX}_{self.report_date.isoformat()}_"
            f"{self.channel_id}_{self.schema_version}"
        )


def make_summary_sync_key(
    report_date: date,
    channel_id: str,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> str:
    """The key for one daily completion summary. Deterministic, like the upload key."""
    if report_date is None:
        raise ValueError("report_date is required")
    raw = (channel_id or "").strip()
    if not raw:
        raise ValueError("channel_id is required to build a summary sync key")
    if _FORBIDDEN_IN_ID.search(raw):
        raise ValueError(
            f"channel_id {channel_id!r} must not contain whitespace or any of "
            f"| , tab newline /: the resulting key could not be parsed back."
        )
    return str(SummarySyncKey(report_date, raw.upper(), schema_version))


def is_summary_key(key: str) -> bool:
    """Whether ``key`` names a daily summary rather than an upload row."""
    return (key or "").strip().startswith(f"{SUMMARY_PREFIX}_")


def parse_summary_sync_key(key: str) -> tuple[date, str, str]:
    """Inverse of :func:`make_summary_sync_key`. Raises when the key is malformed."""
    text = (key or "").strip()
    if not is_summary_key(text):
        raise ValueError(f"{key!r} is not a summary sync key.")
    match = _KEY_RE.match(text)
    if not match:
        raise ValueError(f"{key!r} is not a valid summary sync key.")
    return date.fromisoformat(match.group("date")), match.group("id"), match.group("version")
