"""Google Sheets access: the transport contract and the documented API shapes.

This module owns the *documented* surface ``02-technical-prd.md`` §8 cites:

- ``spreadsheets.get``   - verify the spreadsheet exists and is reachable.
- ``spreadsheets.values.get`` with a single-cell range - read the existing Sync Keys.
- ``spreadsheets.values.append`` - append the missing rows, **once**.

``append`` is not idempotent. A retry after a timeout writes a second row. Everything
above this module is built so that a retry is never needed: the reader runs first, the
key column is the authority, and a second append is only ever issued after a read proves
the first one did not land.

This module talks to Google Sheets only. It cannot upload video - it has no reference to
any video connector, no reference to the worker, and no reference to the scheduler. That
is the enforcement of "a report write must never trigger another video upload".

Live-Google behaviour is ``FEAS-09``, which remains BLOCKED until a real project and
spreadsheet exist. Nothing here is evidence about Google; it is the shape the application
expects, with the live calls isolated behind ``_LiveSheetsClient``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

#: The narrowest scope that permits everything this module does, and nothing else.
#: Read-back needs read access; appending needs append access. ``spreadsheets`` covers
#: both and is the scope ``01-technical-prd.md`` §12 FEAT-010 names.
SHEETS_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/spreadsheets",)

#: The key column MUST be column A. Only a single-column read is guaranteed to return
#: just the keys, and a single-column read is the only read this application performs.
#: Column order is frozen for the life of ``SCHEMA_VERSION`` because of this.
KEY_COLUMN = "Sync Key"

#: ``REQUIRED_COLUMNS`` may never be removed or reordered; ``OPTIONAL_COLUMNS`` may be
#: omitted from the destination but are always written when present.
REQUIRED_COLUMNS: tuple[str, ...] = (
    KEY_COLUMN,
    "Schedule ID",
    "Report Date",
    "Report Schema Version",
)
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "YouTube Video ID",
    "Canonical Video URL",
    "Video Title",
    "Target Channel ID",
    "Target Channel Name",
    "Intended Publication Time (UTC)",
    "Actual Upload Completion Time (UTC)",
    "Verified Publication Time (UTC)",
    "Upload Outcome",
    "Current Observed YouTube Status",
    "Creator Mention State",
    "Reporting State",
    "Error / Manual Review",
    "Generated At (UTC)",
)

#: The complete, frozen column order for ``v1``. Index 0 is the key column.
SHEET_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

ErrorKind = Literal[
    "expired",  # the grant can no longer authorize a request
    "forbidden",  # authorized, but this identity may not touch the destination
    "not_found",  # the spreadsheet or worksheet does not exist / is not shared
    "network",  # the request never reached the server, or the server never answered
    "ambiguous",  # the write MAY have applied - the only kind that authorises a re-read
    "invalid",  # the destination reference itself is unusable
]


class SheetError(Exception):
    """A Sheets failure, classified so the caller can decide what is safe.

    ``kind`` is the only thing the sync loop branches on. ``ambiguous`` is the
    consequential one: it means *the request may have reached the server*, so the
    correct next action is a read, never a second append.
    """

    def __init__(self, kind: ErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class SheetDestination:
    """Where reports are written. Never a URL - always an ID plus a tab name."""

    spreadsheet_id: str
    worksheet: str

    @property
    def tab_ref(self) -> str:
        """The ``A1`` tab reference, single-quoted as the API requires."""
        return f"'{self._escape_tab()}'"

    def _escape_tab(self) -> str:
        return self.worksheet.replace("'", "''")

    @property
    def full_range(self) -> str:
        return f"{self.tab_ref}!A1"

    @property
    def key_range(self) -> str:
        """The single-cell read range. Column A only - see ``KEY_COLUMN``."""
        return f"{self.tab_ref}!A:A"


@dataclass(frozen=True)
class SheetWrite:
    """What ``append_rows`` reports back. Keys only - never row payloads."""

    appended_keys: tuple[str, ...]
    skipped_keys: tuple[str, ...]


@runtime_checkable
class SheetConnector(Protocol):
    """The transport. Four operations, each mirroring one documented API call."""

    def verify(self, destination: SheetDestination) -> None:
        """Prove the destination exists and is reachable. Raises ``SheetError``."""

    def read_sync_keys(self, destination: SheetDestination) -> tuple[str, ...]:
        """Return every non-empty value in the key column, in sheet order."""

    def append_rows(
        self, destination: SheetDestination, rows: Sequence[Mapping[str, str]]
    ) -> SheetWrite:
        """Append ``rows`` to the destination. Raises ``SheetError`` on failure."""

    def test_write(self, destination: SheetDestination, key: str) -> SheetWrite:
        """Write exactly one probe row under ``key``, then read it back."""


class SheetsUnconfigured(Exception):
    """The operator has not connected Google Sheets yet."""


__all__ = [
    "KEY_COLUMN",
    "OPTIONAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "SHEETS_SCOPES",
    "SHEET_COLUMNS",
    "ErrorKind",
    "SheetConnector",
    "SheetDestination",
    "SheetError",
    "SheetWrite",
    "SheetsUnconfigured",
]
