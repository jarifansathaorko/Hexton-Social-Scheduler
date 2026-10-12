"""Deterministic Google Sheets sync keys and read-before-append deduplication.

`spreadsheets.values.append` is not idempotent: a retried append after a timeout writes a
second row. The mechanism that makes reporting safe is a deterministic ``Sync Key`` plus a
read of the existing keys *before* any write, so the sheet - not the local ledger - is the
authority.

Reference: ``02-technical-prd.md`` section 8.1, ``AC-MVP-008``, ``FEAS-09``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: The schema version is part of the key so that a column-layout change starts a new
#: identity series instead of colliding with rows written under the old layout.
SCHEMA_VERSION = "v1"

KEY_COLUMN = "Sync Key"
SCHEDULE_ID_COLUMN = "Schedule ID"

#: Columns that are identity and may never be toggled off by a column-configuration UI.
REQUIRED_COLUMNS = ("Report Date", SCHEDULE_ID_COLUMN, KEY_COLUMN)


def make_sync_key(report_date: date, schedule_id: str, schema_version: str = SCHEMA_VERSION) -> str:
    """`SCH_<report_date>_<schedule_id>_<schema_version>`.

    Deterministic and pure: the same inputs must always produce the same key, in every
    process, on every run. ``schedule_id`` is normalised so that cosmetic differences
    (case, surrounding whitespace) cannot create a second identity for one job.
    """
    if not schedule_id or not schedule_id.strip():
        raise ValueError("schedule_id is required to build a sync key")
    if not isinstance(report_date, date):
        raise TypeError("report_date must be a datetime.date")
    sid = schedule_id.strip().upper()
    for ch in (" ", "\t", "\n", "|"):
        if ch in sid:
            raise ValueError(f"schedule_id must not contain {ch!r}: {schedule_id!r}")
    return f"SCH_{report_date.isoformat()}_{sid}_{schema_version}"


def is_valid_key(key: str) -> bool:
    parts = key.split("_")
    if len(parts) != 4 or parts[0] != "SCH":
        return False
    try:
        date.fromisoformat(parts[1])
    except ValueError:
        return False
    return bool(parts[2]) and bool(parts[3])


@dataclass
class ReportRow:
    sync_key: str
    schedule_id: str
    values: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconcilePlan:
    """What the caller should do. Never 'append and hope'."""

    to_append: list[ReportRow]
    already_present: list[str]
    conflicts: list[tuple[str, dict, dict]]   # (key, existing_values, proposed_values)
    authority: str = "sheet"


def plan_append(
    proposed: list[ReportRow],
    existing_rows: list[dict[str, object]],
    *,
    key_column: str = KEY_COLUMN,
) -> ReconcilePlan:
    """Compute the difference between what we want and what the sheet already holds.

    ``existing_rows`` is the result of reading the ``Sync Key`` column (and enough of each
    row to detect conflicts). The read is the reconciliation: running this twice with no
    intervening change always yields an empty ``to_append``.
    """
    seen: dict[str, dict] = {}
    for row in existing_rows:
        raw = row.get(key_column)
        if raw is None or raw == "":
            continue
        key = str(raw).strip()
        if key in seen:
            # Duplicate already in the sheet: report it, never add another.
            continue
        seen[key] = row

    to_append: list[ReportRow] = []
    present: list[str] = []
    conflicts: list[tuple[str, dict, dict]] = []

    for row in proposed:
        key = row.sync_key.strip()
        if key not in seen:
            to_append.append(row)
            continue
        present.append(key)
        existing = seen[key]
        for col, value in row.values.items():
            if col in existing and existing[col] not in (None, "", value):
                conflicts.append((key, {col: existing[col]}, {col: value}))
                break

    return ReconcilePlan(
        to_append=to_append,
        already_present=present,
        conflicts=conflicts,
        authority="sheet",
    )


def keys_from_ledger(ledger: dict[str, str]) -> set[str]:
    """Local ledger -> set of keys believed synced.

    Used only to *short-circuit* reads. It is never sufficient on its own: FEAS-09
    step 6 corrupts this ledger deliberately and requires the sheet to win.
    """
    return {k for k, v in ledger.items() if v == "synced"}
