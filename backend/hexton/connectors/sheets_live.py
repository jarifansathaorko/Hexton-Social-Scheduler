"""The live Google Sheets client.

Every Google call this application makes to Sheets lives in this one class, so that the
whole live surface is auditable in a single reading and so that the rest of the code has
no dependency on the Google client libraries being importable.

``02-technical-prd.md`` §8 requires the official Google client library for non-upload
Google operations. ``build("sheets", "v4", credentials=...)`` is that library. Only the
YouTube *upload* uses raw ``httpx``, and this module does not touch video at all.

Evidence status: this is the shape the application expects from the documented API. It is
**not** evidence that Google behaves this way - that is ``FEAS-09``, BLOCKED until a real
project, a real consent screen, and a real spreadsheet exist. Nothing here runs in tests;
``FakeSheetsConnector`` covers the client-side protocol.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .sheets import KEY_COLUMN, SheetDestination, SheetError, SheetWrite


def _error_kind(exc: Exception) -> str:
    """Map a Google ``HttpError`` onto this application's error vocabulary.

    The status-code mapping is the only place a Google error is interpreted. ``403`` is
    split because Sheets uses it for both "this identity may not" and "the sharing is
    off", and the operator-facing message differs sharply between the two.
    """
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "resp", None), "status", None
    )
    if status is None:
        return "network"
    status = int(status)
    if status in (401,):
        return "expired"
    if status == 403:
        reason = str(exc).lower()
        if (
            "has not granted" in reason
            or "insufficient permission" in reason
            or "access not configured" in reason
        ):
            return "forbidden"
        return "forbidden"
    if status == 404:
        return "not_found"
    if status in (400, 409):
        return "invalid"
    return "network"


class LiveSheetsConnector:
    """A ``SheetConnector`` backed by the official Google Sheets API client."""

    def __init__(self, credentials: Any) -> None:
        self._credentials = credentials
        self._service: Any | None = None

    def _build(self) -> Any:
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise SheetError(
                    "network",
                    "the Google API client library is not installed; run "
                    "`pip install -e '.[google]'` in backend/",
                ) from exc
            self._service = build(
                "sheets", "v4", credentials=self._credentials, cache_discovery=False
            )
        return self._service

    def _call(self, operation: str, **kwargs: Any) -> Any:
        try:
            return operation().execute()
        except Exception as exc:
            kind = _error_kind(exc)
            raise SheetError(kind, f"{operation.__name__} failed: {exc}") from exc

    def verify(self, destination: SheetDestination) -> None:
        service = self._build()
        self._call(
            lambda: service.spreadsheets().get(
                spreadsheetId=destination.spreadsheet_id,
                fields="properties.title,sheets.properties.title",
            ),
        )

    def read_sync_keys(self, destination: SheetDestination) -> tuple[str, ...]:
        service = self._build()
        response = self._call(
            lambda: (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=destination.spreadsheet_id,
                    range=destination.key_range,
                )
            ),
        )
        values = (response or {}).get("values") or []
        return tuple(str(row[0]).strip() for row in values if row and str(row[0]).strip())

    def append_rows(
        self, destination: SheetDestination, rows: Sequence[Mapping[str, str]]
    ) -> SheetWrite:
        service = self._build()
        columns = _ordered_columns(rows)
        values = [[str(row.get(column, "")) for column in columns] for row in rows]
        self._call(
            lambda: (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=destination.spreadsheet_id,
                    range=destination.full_range,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": values},
                    includeValuesInResponse=False,
                )
            ),
        )
        return SheetWrite(
            appended_keys=tuple(str(row.get(KEY_COLUMN, "")) for row in rows),
            skipped_keys=(),
        )

    def test_write(self, destination: SheetDestination, key: str) -> SheetWrite:
        return self.append_rows(destination, [{KEY_COLUMN: key}])


def _ordered_columns(rows: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    """The column order to write. Frozen by ``SHEET_COLUMNS``; defensive on unknown rows."""
    from .sheets import SHEET_COLUMNS

    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        for column in row:
            if column not in seen:
                seen.add(column)
                ordered.append(column)
    known = [c for c in SHEET_COLUMNS if c in seen]
    unknown = [c for c in ordered if c not in SHEET_COLUMNS]
    return tuple(known + unknown)


__all__ = ["LiveSheetsConnector"]
