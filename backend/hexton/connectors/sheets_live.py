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

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .sheets import KEY_COLUMN, ErrorKind, SheetDestination, SheetError, SheetWrite


def _as_google_credentials(value: Any) -> Any:
    """Coerce what the credential store hands us into what the Google client wants.

    ``_connector_for`` reads an *access token string* out of the store — that is the
    only thing the store holds — and the application has never constructed a
    ``google.auth`` credential anywhere. ``build(credentials=...)`` calls
    ``credentials.authorize(...)`` on whatever it is given, so passing the string
    straight through failed with ``'str' object has no attribute 'authorize'`` at the
    first Sheets call: reporting could never have worked against a real spreadsheet,
    and the failure surfaced as an unhandled ``AttributeError`` rather than as a
    report error with a kind.

    A token-only credential deliberately has **no** refresh machinery: there is no
    client secret in this application by design, and the store is refreshed by
    :func:`hexton.services.sheets._connector_for` before each call. When the token
    ages out mid-session the library raises, ``_error_kind`` maps it to ``"expired"``,
    and the next call re-reads a fresh token from the store.
    """
    if value is None:
        raise SheetError(
            "expired",
            "no credential was supplied for the Sheets connection; the stored token "
            "was read as empty.",
        )
    if isinstance(value, str):
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise SheetError(
                "network",
                "google-auth is not installed; run `pip install -e '.[google]'` in backend/.",
            ) from exc
        return Credentials(token=value)
    return value


def _error_kind(exc: Exception) -> ErrorKind:
    """Map a Google ``HttpError`` or client error onto this application's error vocabulary."""
    exc_type = type(exc).__name__.lower()
    exc_msg = str(exc).lower()
    if "refresherror" in exc_type or "invalid_grant" in exc_msg or ("token" in exc_msg and "expired" in exc_msg):
        return "expired"

    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "resp", None), "status", None
    )
    if status is None:
        if "401" in exc_msg or "unauthorized" in exc_msg:
            return "expired"
        if "403" in exc_msg or "forbidden" in exc_msg:
            return "forbidden"
        if "404" in exc_msg or "not found" in exc_msg:
            return "not_found"
        return "network"
    status = int(status)
    if status in (401,):
        return "expired"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status in (400, 409):
        return "invalid"
    return "network"


class LiveSheetsConnector:
    """A ``SheetConnector`` backed by the official Google Sheets API client."""

    def __init__(self, credentials: Any) -> None:
        self._credentials = _as_google_credentials(credentials)
        self._service: Any | None = None

    def _build(self) -> Any:
        if self._service is None:
            try:
                from googleapiclient.discovery import build  # type: ignore[import-untyped]
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

    def _call(self, operation: Callable[[], Any], op_name: str = "operation", **kwargs: Any) -> Any:
        try:
            return operation().execute()
        except Exception as exc:
            kind = _error_kind(exc)
            if op_name == "append_rows" and kind == "network":
                # Network interruption during append_rows means the write may have reached
                # Google Sheets. Classifying this as ambiguous activates the read-before-append
                # verification ladder in the sync engine, preventing duplicate report rows.
                kind = "ambiguous"
            raise SheetError(kind, f"{op_name} failed: {exc}") from exc

    def _ensure_worksheet(self, destination: SheetDestination) -> None:
        """Ensure the destination worksheet tab exists, auto-creating it with headers if missing."""
        service = self._build()
        try:
            meta = self._call(
                lambda: service.spreadsheets().get(
                    spreadsheetId=destination.spreadsheet_id,
                    fields="sheets.properties.title",
                ),
                op_name="get_sheets",
            )
        except SheetError:
            raise

        sheet_titles = [s.get("properties", {}).get("title") for s in meta.get("sheets", [])]
        if destination.worksheet not in sheet_titles:
            # Auto-create the worksheet tab (e.g. today's date tab)
            self._call(
                lambda: service.spreadsheets().batchUpdate(
                    spreadsheetId=destination.spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "addSheet": {
                                    "properties": {
                                        "title": destination.worksheet,
                                    }
                                }
                            }
                        ]
                    },
                ),
                op_name="add_sheet",
            )
            # Initialize with the creator header row
            header_row = ["SL", "Time", "Channel", "Video URL", "Upload Status", "Upload Time", KEY_COLUMN]
            self._call(
                lambda: service.spreadsheets().values().append(
                    spreadsheetId=destination.spreadsheet_id,
                    range=f"{destination.tab_ref}!A1",
                    valueInputOption="RAW",
                    body={"values": [header_row]},
                ),
                op_name="write_headers",
            )

    def verify(self, destination: SheetDestination) -> None:
        service = self._build()
        self._call(
            lambda: service.spreadsheets().get(
                spreadsheetId=destination.spreadsheet_id,
                fields="properties.title,sheets.properties.title",
            ),
            op_name="verify",
        )

    def read_sync_keys(self, destination: SheetDestination) -> tuple[str, ...]:
        service = self._build()
        self._ensure_worksheet(destination)

        # Inspect row 1 to see which column holds KEY_COLUMN ("Sync Key")
        col_letter = "A"
        try:
            h_resp = self._call(
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=destination.spreadsheet_id,
                    range=f"{destination.tab_ref}!1:1",
                ),
                op_name="read_headers",
            )
            headers = (h_resp or {}).get("values", [[]])[0]
            if KEY_COLUMN in headers:
                idx = headers.index(KEY_COLUMN)
                col_letter = chr(ord("A") + idx)
        except Exception:
            pass

        response = self._call(
            lambda: (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=destination.spreadsheet_id,
                    range=f"{destination.tab_ref}!{col_letter}:{col_letter}",
                )
            ),
            op_name="read_sync_keys",
        )
        values = (response or {}).get("values") or []
        return tuple(
            str(row[0]).strip()
            for row in values
            if row and str(row[0]).strip() and str(row[0]).strip() != KEY_COLUMN
        )

    def append_rows(
        self, destination: SheetDestination, rows: Sequence[Mapping[str, str]]
    ) -> SheetWrite:
        service = self._build()
        self._ensure_worksheet(destination)
        columns = _ordered_columns(rows)

        # Check if the worksheet tab already has headers on row 1
        has_headers = False
        try:
            h_resp = self._call(
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=destination.spreadsheet_id,
                    range=f"{destination.tab_ref}!1:1",
                ),
                op_name="check_headers",
            )
            has_headers = bool((h_resp or {}).get("values"))
        except Exception:
            pass

        to_write: list[list[str]] = []
        if not has_headers and rows:
            to_write.append(list(columns))
        for row in rows:
            to_write.append([str(row.get(column, "")) for column in columns])

        self._call(
            lambda: (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=destination.spreadsheet_id,
                    range=destination.full_range,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": to_write},
                    includeValuesInResponse=False,
                )
            ),
            op_name="append_rows",
        )
        return SheetWrite(
            appended_keys=tuple(row.get(KEY_COLUMN, "") for row in rows),
            skipped_keys=(),
        )

    def update_rows(
        self, destination: SheetDestination, rows: Sequence[Mapping[str, str]]
    ) -> None:
        """Update live status, video URL, and upload time for existing rows in the sheet."""
        if not rows:
            return
        service = self._build()
        try:
            resp = self._call(
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=destination.spreadsheet_id,
                    range=f"{destination.tab_ref}!A:Z",
                ),
                op_name="get_all_rows",
            )
        except Exception:
            return

        all_values = (resp or {}).get("values") or []
        if len(all_values) <= 1:
            return

        headers = all_values[0]
        if KEY_COLUMN not in headers:
            return
        key_idx = headers.index(KEY_COLUMN)

        # Build map of key -> row index (1-based for A1 notation)
        key_to_row = {}
        for row_idx, row_vals in enumerate(all_values[1:], start=2):
            if len(row_vals) > key_idx and row_vals[key_idx]:
                key_to_row[str(row_vals[key_idx]).strip()] = row_idx

        # Update matching rows
        for row_data in rows:
            key = row_data.get(KEY_COLUMN, "")
            if not key or key not in key_to_row:
                continue
            sheet_row_num = key_to_row[key]
            # Build updated values matching sheet headers
            updated_vals = [str(row_data.get(col_name, "")) for col_name in headers]
            try:
                self._call(
                    lambda: service.spreadsheets().values().update(
                        spreadsheetId=destination.spreadsheet_id,
                        range=f"{destination.tab_ref}!A{sheet_row_num}",
                        valueInputOption="RAW",
                        body={"values": [updated_vals]},
                    ),
                    op_name="update_row",
                )
            except Exception:
                pass

    def test_write(self, destination: SheetDestination, key: str) -> SheetWrite:
        return self.append_rows(destination, [{KEY_COLUMN: key}])


def _ordered_columns(rows: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    """The column order to write. Prioritizes clean creator columns first, then KEY_COLUMN."""
    from .sheets import CREATOR_COLUMNS, KEY_COLUMN, SHEET_COLUMNS

    seen: set[str] = set()
    for row in rows:
        for column in row:
            seen.add(column)

    # 1. Creator columns first (SL, Time, Channel, Video URL, Upload Status, Upload Time)
    creator = [c for c in CREATOR_COLUMNS if c in seen]
    # 2. Key column next (Sync Key)
    key_col = [KEY_COLUMN] if KEY_COLUMN in seen and KEY_COLUMN not in creator else []
    # 3. Any other standard sheet columns
    others = [c for c in SHEET_COLUMNS if c in seen and c not in creator and c not in key_col]
    # 4. Any unknown columns
    unknown = [c for c in seen if c not in creator and c not in key_col and c not in others]

    return tuple(creator + key_col + others + unknown)


__all__ = ["LiveSheetsConnector"]
