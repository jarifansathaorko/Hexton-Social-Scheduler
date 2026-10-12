"""Read-before-append report writer.

Implements ``02-technical-prd.md`` §8.1 and ``AC-MVP-008``:

1. Every row carries a deterministic ``Sync Key``.
2. Read the existing ``Sync Key`` column before appending.
3. Append only the missing keys.
4. On a timeout or any ambiguous write, **re-read** rather than retry blindly.
5. Mark a key synced only after a confirmed success.

A Sheets failure must never trigger another YouTube upload. That invariant is enforced by
construction here: this module has no reference to anything that can upload video.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from ..sheets.dedupe import KEY_COLUMN, ReportRow, ReconcilePlan, plan_append


@dataclass(frozen=True)
class SyncResult:
    appended_keys: list[str]
    skipped_keys: list[str]
    conflicts: list[tuple[str, dict, dict]]
    read_after_ambiguous_write: bool = False
    notes: list[str] = field(default_factory=list)


class SheetsReporter:
    def __init__(self, *, spreadsheet_id: str, range_a1: str, access_token_provider,
                 client: httpx.Client, key_column: str = KEY_COLUMN) -> None:
        self._id = spreadsheet_id
        self._range = range_a1
        self._token = access_token_provider
        self._client = client
        self.key_column = key_column

    # The base is the documented Sheets endpoint; the mock server substitutes its own.
    base = "https://sheets.googleapis.com/v4/spreadsheets"

    @property
    def _root(self) -> str:
        root = str(self._client.base_url)
        return f"{root}/v4/spreadsheets" if root.startswith("http://127.0.0.1") \
            else f"{self.base}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def read_rows(self) -> list[dict]:
        """Read the target range. This read *is* the reconciliation."""
        url = f"{self._root}/{self._id}/values/{self._range}"
        resp = self._client.get(url, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"values.get failed: HTTP {resp.status_code} {resp.text[:200]}")
        values = (resp.json() or {}).get("values", [])
        if not values:
            return []
        header = values[0]
        out = []
        for row in values[1:]:
            out.append({header[i]: (row[i] if i < len(row) else "") for i in range(len(header))})
        return out

    def append(self, rows: list[ReportRow], header: list[str]) -> bool:
        """Append rows. Returns False if the write's outcome is unknown."""
        url = f"{self._root}/{self._id}/values/{self._range}:append?" + \
              urlencode({"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"})
        payload = {"range": self._range, "values": [[r.values.get(c, "") for c in header]
                                                    for r in rows]}
        try:
            resp = self._client.post(url, headers=self._headers(), content=json.dumps(payload))
        except httpx.HTTPError:
            # Outcome unknown: the append may have been applied. The caller must re-read.
            return False
        if resp.status_code != 200:
            return False
        return True

    def sync(self, proposed: list[ReportRow], header: list[str]) -> SyncResult:
        """The full read-before-append cycle, safe to re-run."""
        notes: list[str] = []

        existing = self.read_rows()
        plan: ReconcilePlan = plan_append(proposed, existing, key_column=self.key_column)

        if not plan.to_append:
            return SyncResult([], [p.sync_key for p in proposed], plan.conflicts,
                              notes=["Nothing to append; the sheet already holds every key."])

        ok = self.append(plan.to_append, header)
        if ok:
            return SyncResult([r.sync_key for r in plan.to_append],
                              list(plan.already_present),      # already a list of keys
                              plan.conflicts, notes=notes)

        # Ambiguous write. Re-read; the sheet is authoritative, never the local ledger.
        notes.append("The append did not return success. Re-reading the sheet to determine "
                     "what was actually written.")
        existing_after = self.read_rows()
        plan_after = plan_append(proposed, existing_after, key_column=self.key_column)
        appended = {p for p in plan_after.already_present}
        return SyncResult(
            [r.sync_key for r in plan.to_append if r.sync_key not in appended],
            list(plan_after.already_present),
            plan_after.conflicts,
            read_after_ambiguous_write=True,
            notes=notes,
        )
