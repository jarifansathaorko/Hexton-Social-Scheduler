"""An in-process Sheets stand-in that behaves the way the real API does where it matters.

``phase0-poc/mock_youtube/sheets.py`` established the shape and the one behaviour this
fake must reproduce: ``append`` is **not** idempotent. If a write reaches the sheet and
the response is lost, a blind retry writes a second row. That is the failure the
read-before-append protocol exists to survive, so the fake reproduces it deliberately via
``drop_response_after_append``.

Evidence from this fake is evidence about the *client*, exactly as with ``FakeConnector``.
``FEAS-09`` stays BLOCKED.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import count

from .sheets import KEY_COLUMN, SHEET_COLUMNS, SheetDestination, SheetError, SheetWrite


@dataclass
class SheetState:
    """Shared, mutable sheet contents. Tests drive it directly."""

    rows: list[dict[str, str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=lambda: list(SHEET_COLUMNS))
    #: Existing Sync Keys that the operator insists on keeping - writing one is a conflict.
    protected_keys: set[str] = field(default_factory=set)
    #: When true the next ``append`` is applied to the sheet but its response is lost.
    drop_response_after_append: bool = False
    #: When true the next append is applied to only SOME of the rows before failing.
    fail_after_rows: int | None = None
    #: When set, the next operation of any kind fails before doing anything. This is the
    #: catch-all; the two below exist because read-before-append always issues a read
    #: before the write, so "fail the next call" can only ever mean "fail the read".
    fail_with: tuple[str, str] | None = None
    #: When set, the next *read* fails before doing anything.
    fail_read_with: tuple[str, str] | None = None
    #: When set, the *read* whose 1-based call number equals this fails. Needed because
    #: the interesting failure is the *second* read — the one after an ambiguous write.
    fail_read_at_call: int | None = None
    #: When set, the next *append* fails before writing anything.
    fail_append_with: tuple[str, str] | None = None
    #: Set of spreadsheet ids that exist. Anything else is ``not_found``.
    visible_spreadsheets: set[str] = field(default_factory=lambda: {"sheet-1"})
    #: Set of worksheet tab names that exist within a visible spreadsheet.
    visible_worksheets: set[str] = field(default_factory=lambda: {"Report"})
    append_calls: int = 0
    read_calls: int = 0
    verify_calls: int = 0
    last_appended: list[dict[str, str]] = field(default_factory=list)
    sequence: count = field(default_factory=lambda: count(1))


class FakeSheetsConnector:
    """A ``SheetConnector`` whose sheet lives in ``state``."""

    def __init__(self, state: SheetState | None = None) -> None:
        self.state = state if state is not None else SheetState()

    def _check_destination(self, destination: SheetDestination) -> None:
        state = self.state
        if state.fail_with is not None:
            kind, detail = state.fail_with
            state.fail_with = None
            raise SheetError(kind, detail)  # type: ignore[arg-type]
        if destination.spreadsheet_id not in state.visible_spreadsheets:
            raise SheetError(
                "not_found", f"spreadsheet {destination.spreadsheet_id!r} is not accessible"
            )
        if destination.worksheet not in state.visible_worksheets:
            raise SheetError("not_found", f"worksheet {destination.worksheet!r} is not accessible")

    def verify(self, destination: SheetDestination) -> None:
        self.state.verify_calls += 1
        self._check_destination(destination)

    def read_sync_keys(self, destination: SheetDestination) -> tuple[str, ...]:
        self.state.read_calls += 1
        if self.state.fail_read_at_call == self.state.read_calls:
            self.state.fail_read_at_call = None
            raise SheetError("network", "the follow-up read also failed")
        if self.state.fail_read_with is not None:
            kind, detail = self.state.fail_read_with
            self.state.fail_read_with = None
            raise SheetError(kind, detail)  # type: ignore[arg-type]
        self._check_destination(destination)
        return tuple(row[KEY_COLUMN] for row in self.state.rows if row.get(KEY_COLUMN))

    def append_rows(
        self, destination: SheetDestination, rows: Sequence[Mapping[str, str]]
    ) -> SheetWrite:
        self._check_destination(destination)
        state = self.state
        state.append_calls += 1
        if state.fail_append_with is not None:
            kind, detail = state.fail_append_with
            state.fail_append_with = None
            raise SheetError(kind, detail)  # type: ignore[arg-type]

        appended: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            key = str(row.get(KEY_COLUMN, ""))
            if not key:
                raise SheetError("invalid", "a report row was built without a Sync Key")
            if state.fail_after_rows is not None and index >= state.fail_after_rows:
                # One-shot: consumed by firing. A flag that survived the failure would
                # make every subsequent pass fail identically, which is not how a real
                # partial write behaves.
                state.fail_after_rows = None
                raise SheetError("network", f"lost the response after {index} rows")
            state.rows.append(dict(row))
            appended.append(dict(row))

        state.last_appended = appended

        if state.fail_after_rows is not None:
            state.fail_after_rows = None
            raise SheetError("network", "lost the response mid-append")
        if state.drop_response_after_append:
            state.drop_response_after_append = False
            # The rows ARE on the sheet. Only the response was lost.
            raise SheetError("ambiguous", "the append may have reached the server")

        return SheetWrite(
            appended_keys=tuple(str(r[KEY_COLUMN]) for r in appended), skipped_keys=()
        )

    def test_write(self, destination: SheetDestination, key: str) -> SheetWrite:
        return self.append_rows(destination, [{KEY_COLUMN: key}])


def seed(rows: Sequence[Mapping[str, str]]) -> SheetState:
    """A state pre-populated with ``rows`` - used to simulate an existing sheet."""
    return SheetState(rows=[dict(row) for row in rows])


__all__ = ["FakeSheetsConnector", "SheetState", "seed"]
