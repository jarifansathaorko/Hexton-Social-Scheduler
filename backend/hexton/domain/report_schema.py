"""The report schema: what a row is, what may never be dropped, and what a row may claim.

``01-standard-prd.md`` §9 and ``02-technical-prd.md`` §8 fix the columns. This module is
the single definition of them, and it carries the two constraints that matter:

1. **Identity columns are not optional.** ``REQUIRED_COLUMNS`` exist so that a
   reconciliation or deduplication is possible at all. A column-configuration UI may hide
   them from a *view*; it may never remove them from a *write*. ``columns_to_write``
   enforces that in code, not in a comment.

2. **A row may not claim a state the system has not evidenced.** ``Upload Outcome`` is
   derived from ``JobState``, and the only way a job reaches ``PUBLISHED`` is a persisted
   video ID plus verified remote evidence. ``reporting_state_for`` is the single mapping
   from local state to the reporting vocabulary, so there is exactly one place where a
   report says what happened.

Pure functions. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..connectors.sheets import KEY_COLUMN, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, SHEET_COLUMNS
from .jobstate import JobState, RemoteState, display_for

#: Bumped when the column layout or the meaning of a value changes. It is part of the
#: Sync Key, so a schema change starts a new identity series rather than colliding with
#: rows written under the old layout.
SCHEMA_VERSION = "v1"


@dataclass(frozen=True)
class ReportColumns:
    """The columns a destination will receive, in write order."""

    names: tuple[str, ...]

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(name for name in self.names if name in REQUIRED_COLUMNS)


def columns_to_write(optional: tuple[str, ...] = OPTIONAL_COLUMNS) -> ReportColumns:
    """The columns to write, honouring the operator's optional-column choice.

    ``REQUIRED_COLUMNS`` are always present and always first-with-key-at-A. An optional
    column the operator turned off is simply absent from the row mapping; the destination
    keeps whatever it already had in those cells, because a report write never rewrites a
    row it did not create.
    """
    chosen = tuple(name for name in OPTIONAL_COLUMNS if name in optional)
    return ReportColumns(names=REQUIRED_COLUMNS + chosen)


def validate_columns(columns: ReportColumns) -> tuple[str, ...]:
    """Blocking problems with a column selection. Empty means usable."""
    problems: list[str] = []
    if KEY_COLUMN not in columns.names:
        problems.append(
            f"{KEY_COLUMN!r} is the deduplication key and cannot be removed; "
            "without it a re-run would write duplicate rows."
        )
    if "Schedule ID" not in columns.names:
        problems.append("'Schedule ID' is the reconciliation key and cannot be removed.")
    if columns.names and columns.names[0] != KEY_COLUMN:
        problems.append(
            f"{KEY_COLUMN!r} must be the first column: only a single-column read is "
            "guaranteed to return keys alone, and the read-back depends on that."
        )
    duplicates = {name for name in columns.names if columns.names.count(name) > 1}
    if duplicates:
        problems.append(f"columns repeated: {', '.join(sorted(duplicates))}")
    return tuple(problems)


#: The reporting vocabulary. These are the only values a report may contain for
#: "Upload Outcome". They are deliberately *not* the internal ``JobState`` values: a
#: report reader is an operator, not the state machine.
REPORT_OUTCOMES: tuple[str, ...] = (
    "Pending",
    "Uploading",
    "Uploaded - Not Published",  # the upload succeeded; publication is NOT claimed
    "Published",
    "Cancelled",
    "Manual Review",
    "Not Started",
)


def upload_outcome(
    state: JobState, *, video_id: str | None, remote_state: RemoteState | None
) -> str:
    """The outcome a report row may claim. Never "Published" without evidence.

    ``PUBLISHED`` requires the same evidence the state machine requires: a persisted video
    ID and a remote observation of ``public``. Anything that only *uploaded* reports as
    ``Uploaded - Not Published``, which is the honest description of the state after
    ``videos.insert`` succeeds and before publication is verified.
    """
    if state is JobState.PUBLISHED:
        return "Published" if video_id else "Manual Review"
    if state is JobState.CANCELLED:
        return "Cancelled"
    if state is JobState.FAILED:
        return "Manual Review"
    if state is JobState.UPLOADING:
        return "Uploading"
    if state is JobState.SCHEDULED or state is JobState.UPLOADED:
        return "Uploaded - Not Published"
    if state is JobState.NEEDS_REVIEW:
        # The upload's outcome is uncertain. Reporting it as "Pending" would imply the
        # day is simply not finished with it; reporting it as failed would be a claim
        # the system cannot support either. "Manual Review" is the honest third answer.
        return "Manual Review"
    if state in (JobState.BLOCKED, JobState.MISSED_WINDOW, JobState.FAILED_RETRY):
        return "Pending"
    return "Pending"


def reporting_state_for(state: JobState) -> str:
    """The single internal-state-to-report mapping. Mirrors ``display_for`` exactly."""
    return display_for(state)


def observed_status(remote_state: RemoteState | None) -> str:
    """The most recent remote state, or an explicit marker when there is none."""
    if remote_state is None:
        return "Not observed"
    return remote_state


def generated_at(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "KEY_COLUMN",
    "OPTIONAL_COLUMNS",
    "REPORT_OUTCOMES",
    "REQUIRED_COLUMNS",
    "SCHEMA_VERSION",
    "SHEET_COLUMNS",
    "ReportColumns",
    "columns_to_write",
    "generated_at",
    "observed_status",
    "reporting_state_for",
    "upload_outcome",
    "validate_columns",
]
