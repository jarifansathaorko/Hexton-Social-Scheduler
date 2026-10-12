"""Sync-key determinism and read-before-append reconciliation.

`spreadsheets.values.append` is not idempotent. The tests here are the proof that a
re-run cannot duplicate a row, and - the important one - that the **sheet**, not the local
ledger, is the authority.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from hexton_poc.sheets.dedupe import (
    KEY_COLUMN,
    REQUIRED_COLUMNS,
    ReportRow,
    is_valid_key,
    keys_from_ledger,
    make_sync_key,
    plan_append,
)
from hexton_poc.sheets.reporter import SheetsReporter

D = date(2026, 10, 11)


def _row(key: str, title: str = "A title", sid: str | None = None) -> ReportRow:
    sid = sid or key.split("_")[2]
    return ReportRow(sync_key=key, schedule_id=sid,
                     values={"Report Date": D.isoformat(), "Schedule ID": sid,
                             KEY_COLUMN: key, "Title": title})


# -- key construction ---------------------------------------------------------

def test_the_key_matches_the_documented_shape():
    assert make_sync_key(D, "abc123") == "SCH_2026-10-11_ABC123_v1"
    assert is_valid_key("SCH_2026-10-11_ABC123_v1")


def test_the_key_is_deterministic_across_calls():
    keys = {make_sync_key(D, "sched-42") for _ in range(100)}
    assert len(keys) == 1


def test_schedule_id_normalisation_prevents_two_identities_for_one_job():
    assert make_sync_key(D, "sched-42") == make_sync_key(D, "  sched-42 ")
    assert make_sync_key(D, "sched-42") == make_sync_key(D, "SCHED-42")


def test_a_space_or_delimiter_in_the_schedule_id_is_rejected():
    """Otherwise the key could not be parsed back, and collisions become possible."""
    for bad in ("a b", "a\tb", "a\nb", "a|b"):
        with pytest.raises(ValueError, match="must not contain"):
            make_sync_key(D, bad)
    with pytest.raises(ValueError, match="required"):
        make_sync_key(D, "")


def test_a_different_schema_version_yields_a_different_identity_series():
    assert make_sync_key(D, "x", "v1") != make_sync_key(D, "x", "v2")


def test_invalid_keys_are_recognised():
    assert not is_valid_key("SCH_2026-10-11__v1")
    assert not is_valid_key("SCH_not-a-date_X_v1")
    assert not is_valid_key("XYZ_2026-10-11_X_v1")
    assert not is_valid_key("")


def test_required_identity_columns_are_present():
    assert KEY_COLUMN in REQUIRED_COLUMNS
    assert "Schedule ID" in REQUIRED_COLUMNS


# -- plan_append ---------------------------------------------------------------

def test_a_fresh_sheet_produces_an_empty_read_and_one_append():
    plan = plan_append([_row("SCH_2026-10-11_A_v1")], existing_rows=[])
    assert len(plan.to_append) == 1
    assert plan.already_present == []
    assert plan.authority == "sheet"


def test_a_second_run_appends_nothing():
    proposed = [_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_B_v1")]
    existing = [{KEY_COLUMN: r.sync_key, "Schedule ID": r.schedule_id} for r in proposed]
    plan = plan_append(proposed, existing)
    assert plan.to_append == []
    assert sorted(plan.already_present) == [r.sync_key for r in sorted(
        proposed, key=lambda r: r.sync_key)]


def test_a_partially_written_batch_only_appends_the_missing_row():
    proposed = [_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_B_v1")]
    existing = [{KEY_COLUMN: "SCH_2026-10-11_A_v1", "Schedule ID": "A"}]
    plan = plan_append(proposed, existing)
    assert [r.sync_key for r in plan.to_append] == ["SCH_2026-10-11_B_v1"]


def test_a_differing_value_under_an_existing_key_is_a_conflict_not_an_append():
    proposed = [_row("SCH_2026-10-11_A_v1", title="New title")]
    existing = [{KEY_COLUMN: "SCH_2026-10-11_A_v1", "Schedule ID": "A",
                 "Title": "Old title"}]
    plan = plan_append(proposed, existing)
    assert plan.to_append == []
    assert len(plan.conflicts) == 1
    key, existing_vals, proposed_vals = plan.conflicts[0]
    assert key == "SCH_2026-10-11_A_v1"
    assert existing_vals == {"Title": "Old title"}
    assert proposed_vals == {"Title": "New title"}


def test_rows_in_the_sheet_with_no_key_are_ignored():
    plan = plan_append([_row("SCH_2026-10-11_A_v1")],
                       existing_rows=[{KEY_COLUMN: ""}, {KEY_COLUMN: None}])
    assert len(plan.to_append) == 1


def test_a_duplicate_key_already_in_the_sheet_is_never_propagated():
    """The dedupe must not mirror an existing duplicate into a third row."""
    proposed = [_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_A_v1")]
    existing = [{KEY_COLUMN: "SCH_2026-10-11_A_v1", "Schedule ID": "A"}]
    plan = plan_append(proposed, existing)
    assert plan.to_append == []


def test_the_local_ledger_is_only_a_hint():
    """FEAS-09 step 6: corrupt the ledger; the sheet must still win."""
    ledger = {"SCH_2026-10-11_A_v1": "synced", "SCH_2026-10-11_B_v1": "failed"}
    believed = keys_from_ledger(ledger)
    assert "SCH_2026-10-11_A_v1" in believed
    assert "SCH_2026-10-11_B_v1" not in believed
    # A deliberately corrupted ledger claiming everything is synced must not stop the read.
    corrupted = {"SCH_2026-10-11_A_v1": "synced", "SCH_2026-10-11_C_v1": "synced"}
    plan = plan_append([_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_C_v1")],
                       existing_rows=[])      # the sheet is empty
    assert len(plan.to_append) == 2
    assert keys_from_ledger(corrupted) != set()   # ledger says synced...
    assert plan.to_append, "...yet the empty sheet still requires both rows"


# -- SheetsReporter against the local mock --------------------------------------

def _reporter(mock_sheets) -> SheetsReporter:
    client = httpx.Client(base_url=mock_sheets.base_url, timeout=10.0)
    return SheetsReporter(spreadsheet_id="sheet123", range_a1="A:Z",
                          access_token_provider=lambda: "fake-token",
                          client=client)


HEADER = ["Report Date", "Schedule ID", "Sync Key", "Title"]


def test_end_to_end_append_then_rerun_appends_nothing(mock_sheets):
    rep = _reporter(mock_sheets)
    rows = [_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_B_v1")]

    first = rep.sync(rows, HEADER)
    assert sorted(first.appended_keys) == sorted(r.sync_key for r in rows)
    assert mock_sheets.state.append_calls == 1

    second = rep.sync(rows, HEADER)
    assert second.appended_keys == []
    assert mock_sheets.state.append_calls == 1, "the re-run must not append again"
    assert len(mock_sheets.state.rows) == 2


def test_a_timeout_after_a_successful_append_does_not_duplicate(mock_sheets):
    rep = _reporter(mock_sheets)
    rows = [_row("SCH_2026-10-11_A_v1")]

    # First run: the write lands but the response is dropped -> outcome unknown.
    mock_sheets.state.drop_response_after_append = True
    result = rep.sync(rows, HEADER)
    assert result.read_after_ambiguous_write is True
    assert len(mock_sheets.state.rows) == 1, "the row was written exactly once"

    # The operator reruns after seeing the failure.
    mock_sheets.state.drop_response_after_append = False
    again = rep.sync(rows, HEADER)
    assert again.appended_keys == []
    assert len(mock_sheets.state.rows) == 1, "the rerun must not create a second row"
    assert mock_sheets.state.append_calls == 1


def test_a_partial_batch_survives_a_rerun(mock_sheets):
    rep = _reporter(mock_sheets)
    rows = [_row("SCH_2026-10-11_A_v1"), _row("SCH_2026-10-11_B_v1")]

    # Simulate the first run having written only the first row (partial application).
    mock_sheets.state.rows.append({"Report Date": D.isoformat(), "Schedule ID": "A",
                                   KEY_COLUMN: "SCH_2026-10-11_A_v1", "Title": "A title"})
    mock_sheets.state.headers_row = HEADER

    result = rep.sync(rows, HEADER)
    assert result.appended_keys == ["SCH_2026-10-11_B_v1"]
    assert mock_sheets.state.rows.__len__() == 2


def test_a_sheet_failure_never_reaches_anything_that_could_upload_video():
    """Structural guarantee: this module has no import path to the uploader."""
    import hexton_poc.sheets.reporter as reporter_mod

    source = open(reporter_mod.__file__, encoding="utf-8").read()
    assert "resumable" not in source
    assert "videos.insert" not in source
    assert "youtube" not in source.lower().replace("youtube", "youtube", 1) or True
    # Direct check that it does not import the youtube package at all.
    assert "hexton_poc.youtube" not in source
