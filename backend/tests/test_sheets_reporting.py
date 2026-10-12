"""Google Sheets reporting: the outbox, read-before-append, and the failure ladder.

``AC-MVP-008``, ``02-technical-prd.md`` §8.1, ``FEAS-09``.

The properties under test, and why each one is worth a test of its own:

* **Duplicate prevention** — ``spreadsheets.values.append`` is not idempotent, so the
  read-back is the only thing standing between a retry and a second row.
* **A lost response after a successful write** — the case that makes naive retry logic
  wrong. The server applied the write; the client saw nothing. The correct next action
  is a *read*, never a second append.
* **Worker restart** — the outbox is durable, so a restart resumes rather than
  re-derives. A row built before the restart must be the same row after it.
* **A write never triggers an upload** — asserted structurally and behaviourally.
* **Published is not claimed by a successful upload** — the honest-reporting rule.

``FEAS-09`` remains BLOCKED: everything here is evidence about the client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import BASE, FAKE_SHEETS_TOKEN, make_job, make_media
from fastapi.testclient import TestClient
from hexton.bootstrap import build_context
from hexton.connectors.fake import FakeState
from hexton.connectors.sheets import KEY_COLUMN
from hexton.credentials.store import InMemoryCredentialStore
from hexton.db.models import (
    RemoteObservation,
    ScheduledJob,
    SheetReportRecord,
    SheetSummaryRecord,
)
from hexton.domain.jobstate import JobState, RemoteState
from hexton.domain.report_schema import SCHEMA_VERSION
from hexton.domain.sync_key import make_summary_sync_key, make_sync_key, parse_sync_key
from hexton.services import sheets as sheets_service
from hexton.worker.report_sync import ReportSyncSweep

PUBLISHED_KEY_DATE = BASE.date()


def enqueue(ctx, session, config, *, now=BASE) -> int:
    """Materialise the outbox, as the worker and the API both do before delivering.

    ``sync_once`` deliberately does *not* enqueue: delivery and materialisation are
    separate concerns, and a function that both invents rows and writes them is harder
    to reason about than two that each do one thing.
    """
    created = sheets_service.enqueue_reports(session, config, now=now)
    if config.include_daily_summary:
        for day in {
            sheets_service.report_date_of_job(job, config)
            for job in sheets_service.eligible_jobs(session, config)
        } - {None}:
            created += sheets_service.enqueue_summaries(session, config, report_date=day, now=now)
    session.commit()
    return created


def deliver(ctx, session, config, *, now=BASE):
    """Materialise, then deliver. What the worker and the on-demand route both do."""
    enqueue(ctx, session, config, now=now)
    return sheets_service.sync_once(ctx, session, config=config, now=now)


# ==========================================================================
# Sync keys
# ==========================================================================


def test_the_sync_key_is_deterministic_across_every_input_that_matters() -> None:
    """The key is the only idempotency mechanism, so it must not vary by run."""
    a = make_sync_key(PUBLISHED_KEY_DATE, "abc123", SCHEMA_VERSION)
    b = make_sync_key(PUBLISHED_KEY_DATE, "abc123", SCHEMA_VERSION)
    assert a == b == f"SCH_{PUBLISHED_KEY_DATE.isoformat()}_ABC123_{SCHEMA_VERSION}"


def test_case_is_folded_so_one_schedule_cannot_acquire_two_identities() -> None:
    """Case is folded; surrounding whitespace is *refused* rather than silently
    trimmed. Both behaviours matter: folding stops two runs disagreeing about
    capitalisation, and refusing whitespace stops a key that could not be parsed back."""
    assert make_sync_key(PUBLISHED_KEY_DATE, "abc123") == make_sync_key(
        PUBLISHED_KEY_DATE, "AbC123"
    )
    with pytest.raises(ValueError, match="must not contain whitespace"):
        make_sync_key(PUBLISHED_KEY_DATE, "  AbC123  ")


def test_the_schema_version_is_part_of_the_key() -> None:
    """A column-layout change starts a new series instead of colliding with the old."""
    assert make_sync_key(PUBLISHED_KEY_DATE, "abc", "v1") != make_sync_key(
        PUBLISHED_KEY_DATE, "abc", "v2"
    )


def test_a_malformed_schedule_id_is_refused_rather_than_silently_mangled() -> None:
    with pytest.raises(ValueError, match="must not contain whitespace"):
        make_sync_key(PUBLISHED_KEY_DATE, "a b")


def test_a_summary_key_cannot_collide_with_an_upload_key() -> None:
    """Both live in the same key column, so the prefixes must not overlap."""
    upload = make_sync_key(PUBLISHED_KEY_DATE, "UC-main")
    summary = make_summary_sync_key(PUBLISHED_KEY_DATE, "UC-main")
    assert upload != summary
    assert parse_sync_key(upload).schedule_id == "UC-MAIN"


def test_a_summary_key_round_trips() -> None:
    from hexton.domain.sync_key import parse_summary_sync_key

    key = make_summary_sync_key(PUBLISHED_KEY_DATE, "UC-main")
    day, channel, version = parse_summary_sync_key(key)
    assert (day, channel, version) == (PUBLISHED_KEY_DATE, "UC-MAIN", SCHEMA_VERSION)


# ==========================================================================
# Report dates and the cut-over
# ==========================================================================


def test_a_time_before_the_cut_over_belongs_to_the_previous_report_day() -> None:
    """The cutover is a real boundary; getting it wrong moves rows between days."""
    just_after_midnight_dhaka = datetime(2026, 10, 10, 18, 15, tzinfo=UTC)  # 00:15 +06
    assert sheets_service.report_date_for(
        just_after_midnight_dhaka, report_timezone="Asia/Dhaka", cutoff_time="03:00"
    ) == PUBLISHED_KEY_DATE - timedelta(days=1)


def test_a_time_after_the_cut_over_belongs_to_today() -> None:
    morning_dhaka = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)  # 10:00 +06
    assert (
        sheets_service.report_date_for(
            morning_dhaka, report_timezone="Asia/Dhaka", cutoff_time="03:00"
        )
        == PUBLISHED_KEY_DATE
    )


def test_an_unparseable_cutoff_is_refused_before_any_row_is_built() -> None:
    with pytest.raises(sheets_service.SheetsConfigError, match="HH:MM"):
        sheets_service.report_date_for(BASE, report_timezone="UTC", cutoff_time="25:99")


def test_an_unknown_report_timezone_is_refused() -> None:
    with pytest.raises(sheets_service.SheetsConfigError, match="not a known IANA zone"):
        sheets_service.report_date_for(BASE, report_timezone="Mars/Olympus")


# ==========================================================================
# Configuration
# ==========================================================================


def test_a_pasted_url_is_parsed_to_an_id_and_never_stored_verbatim() -> None:
    """``01`` §9: a pasted URL must be parsed and validated, not stored."""
    url = "https://docs.google.com/spreadsheets/d/1AbC-_xYz123/edit?usp=sharing"
    assert sheets_service.parse_spreadsheet_reference(url) == "1AbC-_xYz123"


def test_a_url_with_no_id_segment_is_refused() -> None:
    with pytest.raises(sheets_service.SheetsConfigError, match="no /d/<id> segment"):
        sheets_service.parse_spreadsheet_reference("https://docs.google.com/spreadsheets/")


def test_a_bare_id_passes_through_untouched() -> None:
    assert sheets_service.parse_spreadsheet_reference("  1AbC  ") == "1AbC"


def test_reporting_cannot_be_enabled_without_a_destination(ctx, session, sheets_connection) -> None:
    """A destination Hexton cannot name is one it cannot read back from."""
    with pytest.raises(sheets_service.SheetsConfigError, match="before a spreadsheet"):
        sheets_service.upsert_config(session, {"enabled": True}, now=BASE)


def test_the_key_column_may_not_be_toggled_off() -> None:
    """Column toggles are a view concern; identity is not negotiable."""
    from hexton.connectors.sheets import OPTIONAL_COLUMNS

    chosen = tuple(c for c in OPTIONAL_COLUMNS if c != KEY_COLUMN)
    columns = sheets_service.columns_to_write(chosen)
    problems = sheets_service.validate_columns(columns)
    assert KEY_COLUMN in columns.names, "required columns are always written"
    assert problems == (), f"removing an optional column should not break the schema: {problems}"


def test_a_column_choice_that_drops_a_required_field_is_reported() -> None:
    from hexton.domain.report_schema import ReportColumns

    broken = ReportColumns(names=(KEY_COLUMN, "Video Title"))
    problems = sheets_service.validate_columns(broken)
    assert any("Schedule ID" in p for p in problems)


def test_the_key_column_must_be_first() -> None:
    from hexton.domain.report_schema import ReportColumns

    problems = sheets_service.validate_columns(ReportColumns(names=("Video Title", KEY_COLUMN)))
    assert any("first column" in p for p in problems)


def test_only_known_optional_columns_are_ever_written() -> None:
    """A column name from a stale config cannot smuggle itself into the sheet."""
    from hexton.connectors.sheets import OPTIONAL_COLUMNS, REQUIRED_COLUMNS
    from hexton.db.models import SheetsConfig
    from hexton.domain.report_schema import columns_to_write

    chosen = (*OPTIONAL_COLUMNS[:2], "Something Invented")
    columns = columns_to_write(chosen)
    assert "Something Invented" not in columns.names
    # Required columns come first (key at index 0), then the chosen optional ones.
    assert columns.names[: len(REQUIRED_COLUMNS)] == REQUIRED_COLUMNS
    assert columns.names[len(REQUIRED_COLUMNS) :][:2] == chosen[:2]
    assert SheetsConfig.__tablename__ == "sheets_config"


# ==========================================================================
# Report rows: what a row may claim
# ==========================================================================


def test_a_successful_upload_is_never_reported_as_published(
    ctx, session, reporting, connection, clip
) -> None:
    """The honest-reporting rule, at the row level."""
    job = make_job(
        session,
        make_media(session, clip),
        connection,
        state="JOB-SCHEDULED",
        publish_at=BASE,
    )
    job.video_id = "fake-0001"
    job.uploaded_at = BASE
    session.commit()

    row = sheets_service.build_row(
        session, job, config=reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
    )
    assert row["Upload Outcome"] == "Uploaded - Not Published"
    assert row["Verified Publication Time (UTC)"] == ""
    assert row["YouTube Video ID"] == "fake-0001"


def test_published_requires_a_persisted_video_id_and_an_observed_public_status(
    ctx, session, reporting, connection, clip
) -> None:
    job = make_job(
        session, make_media(session, clip), connection, state="JOB-PUBLISHED", publish_at=BASE
    )
    job.video_id = "fake-0002"
    job.published_at_remote = BASE + timedelta(minutes=30)
    session.add(
        RemoteObservation(
            job_id=job.id,
            video_id="fake-0002",
            privacy_status="public",
            remote_state=RemoteState.PUBLIC.value,
        )
    )
    session.commit()

    row = sheets_service.build_row(
        session, job, config=reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
    )
    assert row["Upload Outcome"] == "Published"
    assert row["Verified Publication Time (UTC)"] != ""


def test_a_job_in_needs_review_is_reported_as_manual_review_not_as_a_failure(
    ctx, session, reporting, connection, clip
) -> None:
    """An uncertain outcome is not a failure, and reporting must not collapse the two."""
    job = make_job(
        session, make_media(session, clip), connection, state="JOB-NEEDS_REVIEW", publish_at=BASE
    )
    job.insert_may_have_succeeded = True
    session.commit()
    row = sheets_service.build_row(
        session, job, config=reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
    )
    assert row["Upload Outcome"] == "Manual Review"
    assert "uncertain" in row["Error / Manual Review"]


def test_required_columns_survive_an_operator_turning_every_optional_one_off(
    ctx, session, reporting, connection, clip
) -> None:
    """``01`` §9: identity columns exist so reconciliation is possible at all."""
    reporting.include_optional_columns = []
    session.commit()
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    row = sheets_service.build_row(
        session, job, config=reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
    )
    assert KEY_COLUMN in row
    assert "Schedule ID" in row
    assert "Report Date" in row
    assert "Video Title" not in row, "an opted-out optional column is genuinely absent"
    assert row[KEY_COLUMN] == make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)


def test_a_job_with_no_publication_time_is_skipped_not_guessed_at(
    ctx, session, reporting, connection, clip
) -> None:
    """A guessed report date would be a second identity for the same schedule."""
    job = make_job(session, make_media(session, clip), connection, publish_at=None)
    assert sheets_service.report_date_of_job(job, reporting) is None
    assert sheets_service.enqueue_reports(session, reporting, now=BASE) == 0
    assert session.query(SheetReportRecord).count() == 0


# ==========================================================================
# The outbox
# ==========================================================================


def test_enqueueing_twice_does_not_create_a_second_row(
    ctx, session, reporting, connection, clip
) -> None:
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    assert sheets_service.enqueue_reports(session, reporting, now=BASE) == 1
    session.commit()
    assert sheets_service.enqueue_reports(session, reporting, now=BASE) == 0
    assert session.query(SheetReportRecord).count() == 1


def test_an_undelivered_row_is_refreshed_but_a_delivered_one_is_frozen(
    ctx, session, reporting, connection, clip
) -> None:
    """A payload changing under a key already in the sheet is a report lying about itself."""
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()
    key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)

    record = session.query(SheetReportRecord).filter_by(sync_key=key).one()
    record.status = "synced"
    record.payload = {"Sync Key": key, "Video Title": "the delivered truth"}
    session.commit()

    job.state = JobState.PUBLISHED.value
    session.commit()
    sheets_service.enqueue_reports(session, reporting, now=BASE + timedelta(hours=1))
    session.commit()
    refreshed = session.query(SheetReportRecord).filter_by(sync_key=key).one()
    assert refreshed.payload["Video Title"] == "the delivered truth"


def test_an_undelivered_row_picks_up_a_state_change(
    ctx, session, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()
    key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)

    job.state = JobState.PUBLISHED.value
    job.video_id = "fake-0003"
    job.published_at_remote = BASE
    session.commit()
    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()
    record = session.query(SheetReportRecord).filter_by(sync_key=key).one()
    assert record.payload["Upload Outcome"] == "Published"


# ==========================================================================
# Delivery: the read-before-append protocol
# ==========================================================================


def test_a_first_sync_appends_every_pending_row(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()

    result = deliver(ctx, session, reporting, now=BASE)
    session.commit()

    assert result.ok
    assert result.appended == 2, "the upload row and the daily summary"
    assert result.already_present == 0
    assert len(sheets_state.rows) == 2
    assert sheets_state.rows[0][KEY_COLUMN].startswith("SCH_")


def test_a_second_sync_appends_nothing(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """The read runs before the write, so an existing key is skipped, not repeated."""
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    deliver(ctx, session, reporting, now=BASE)
    session.commit()
    calls_before = sheets_state.append_calls
    rows_before = list(sheets_state.rows)

    result = deliver(ctx, session, reporting, now=BASE + timedelta(hours=1))
    session.commit()

    # A delivered record is no longer pending, so there is nothing to compare and
    # nothing to write: the second pass is a pure no-op.
    assert result.appended == 0
    assert result.attempted == 0
    assert sheets_state.append_calls == calls_before, "no write was issued at all"
    assert sheets_state.rows == rows_before, "the sheet is byte-for-byte unchanged"


def test_a_row_already_in_the_sheet_is_adopted_rather_than_written_again(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """A key already at the destination is treated as delivered.

    This is the reconciliation step doing its job: the key is deterministic, so presence
    means a report was written under that identity — usually by an earlier pass whose
    response was lost, or before a restart. The local ledger catches up, and crucially
    **no second row is written**.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id)

    # An earlier pass wrote this row and we never heard back.
    sheets_state.rows.append({KEY_COLUMN: key, "Video Title": "written by an earlier pass"})

    result = deliver(ctx, session, reporting, now=BASE)
    session.commit()

    assert result.adopted_from_destination == 1
    assert result.appended == 1, "only the summary row was still missing"
    record = session.query(SheetReportRecord).filter_by(sync_key=key).one()
    assert record.status == "synced"
    assert record.last_error is None
    # The hand-placed row is untouched, and its key was never appended over.
    assert sheets_state.rows[0]["Video Title"] == "written by an earlier pass"
    keys = [r[KEY_COLUMN] for r in sheets_state.rows]
    assert keys.count(key) == 1, "the adopted key appears exactly once in the sheet"


def test_adopting_a_row_never_edits_or_removes_it(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """Adoption changes the local ledger only — the sheet is append-only from here."""
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id)
    sheets_state.rows.append({KEY_COLUMN: key, "Video Title": "the operator's own cell"})

    deliver(ctx, session, reporting, now=BASE)
    session.commit()

    assert sheets_state.rows[0] == {KEY_COLUMN: key, "Video Title": "the operator's own cell"}, (
        "adopting a key must not rewrite a single cell of the row it found"
    )
    appended_keys = [r[KEY_COLUMN] for r in sheets_state.rows[1:]]
    assert key not in appended_keys, "the adopted key was never appended again"


def test_a_foreign_key_is_reported_and_left_alone(ctx, session, sheets_state, reporting) -> None:
    """A key with no local record usually means the sheet predates this install."""
    sheets_state.rows.append({KEY_COLUMN: "SCH_2020-01-01_OLDINSTALL_v1"})
    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()
    assert result.foreign_keys == ["SCH_2020-01-01_OLDINSTALL_v1"]
    assert len(sheets_state.rows) == 1, "the foreign row is neither edited nor removed"


# ==========================================================================
# The case that makes naive retry logic wrong
# ==========================================================================


def test_a_lost_response_after_a_successful_write_is_re_read_not_re_appended(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """The server applied the write and the response was lost.

    A naive client retries the append and writes a second row. The protocol here is
    read-then-compare, so the second pass issues a *read*, finds the key, and appends
    nothing. This is the single most important behaviour in the module.
    """
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    sheets_state.drop_response_after_append = True

    first = deliver(ctx, session, reporting, now=BASE)
    session.commit()

    assert first.read_after_ambiguous_write is True
    assert first.ambiguous_resolved == 2, "the re-read proved both writes landed"
    assert len(sheets_state.rows) == 2, "one upload row and one summary, no duplicates"
    assert len({row[KEY_COLUMN] for row in sheets_state.rows}) == 2
    assert session.query(SheetReportRecord).one().status == "synced"


def test_a_retry_after_an_ambiguous_write_that_never_landed_appends_once(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """The other half: the write genuinely did not reach the sheet."""
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    # Fail the append specifically, so nothing lands. ``fail_with`` would be consumed by
    # the read that read-before-append performs first.
    sheets_state.fail_append_with = ("network", "the connection dropped")

    first = deliver(ctx, session, reporting, now=BASE)
    session.commit()
    assert first.ok is False
    assert len(sheets_state.rows) == 0
    record = session.query(SheetReportRecord).one()
    assert record.status == "failed"
    assert record.attempts == 1

    # The network is back. One append, once.
    result = deliver(ctx, session, reporting, now=BASE + timedelta(hours=1))
    session.commit()
    assert result.appended == 2
    assert len({row[KEY_COLUMN] for row in sheets_state.rows}) == 2
    assert len(sheets_state.rows) == 2, "no duplicate row from the retry"


def test_an_ambiguous_write_that_cannot_be_read_back_is_left_unconfirmed(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """If the follow-up read also fails, the honest answer is "unknown", not "done"."""
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    enqueue(ctx, session, reporting)
    sheets_state.drop_response_after_append = True
    # The re-read after the ambiguous write is the *second* read; target it precisely.
    sheets_state.fail_read_at_call = sheets_state.read_calls + 2

    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    assert result.ambiguous_unresolved == 2, "both the upload row and the summary"
    assert result.ok is False
    assert {r.status for r in session.query(SheetReportRecord).all()} == {"ambiguous"}
    assert all(
        "will not append a second row" in r.last_error
        for r in session.query(SheetReportRecord).all()
    )


def test_an_ambiguous_record_is_converged_by_the_next_successful_read(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """The write landed but nothing proved it. The next read settles it."""
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    enqueue(ctx, session, reporting)
    key = make_sync_key(PUBLISHED_KEY_DATE, session.query(ScheduledJob).one().public_id)

    record = session.query(SheetReportRecord).one()
    record.status = "ambiguous"
    session.commit()
    sheets_state.rows.append({KEY_COLUMN: key})

    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()
    assert result.ambiguous_resolved == 1
    assert session.query(SheetReportRecord).one().status == "synced"


# ==========================================================================
# Partial completion
# ==========================================================================


def test_a_partial_append_leaves_the_missing_rows_pending_for_the_next_pass(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """Some rows reached the sheet and some did not; the batch is retried as a whole.

    A partial append is not partial success: the connector cannot say *which* rows
    landed, because the response was lost mid-request. So every row in the batch is
    failed, and the next pass re-reads and skips the ones that did land by key. That is
    what stops a retry from writing a duplicate.
    """
    for n in range(3):
        make_job(session, make_media(session, clip), connection, title=f"Job {n}", publish_at=BASE)
    session.commit()
    assert enqueue(ctx, session, reporting) == 4, "three upload rows and one summary"
    session.commit()

    sheets_state.fail_after_rows = 2
    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    assert result.ok is False
    statuses = sorted(r.status for r in session.query(SheetReportRecord).all())
    assert statuses.count("failed") == 3, "a partial append is not partial success"

    # The next pass writes the rest; the two already on the sheet are skipped by key.
    second = deliver(ctx, session, reporting, now=BASE + timedelta(hours=1))
    session.commit()
    assert second.ok
    keys = [row[KEY_COLUMN] for row in sheets_state.rows]
    assert len(keys) == len(set(keys)), "no duplicate key reached the sheet"
    assert len({k for k in keys if k.startswith("SCH_")}) == 3


# ==========================================================================
# Worker restart
# ==========================================================================


def test_the_outbox_survives_a_worker_restart_and_resumes(
    ctx, settings, sheets_state, connection, clip
) -> None:
    """Durability, demonstrated by throwing the whole process away."""
    with ctx.sessions() as session:
        job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
        sheets_service.upsert_config(
            session,
            {
                "spreadsheet_id": "sheet-1",
                "worksheet": "Report",
                "connection_id": connection.id,
                "enabled": True,
            },
            now=BASE,
        )
        session.commit()
        key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)

    # "Restart": a completely new context over the same database file and the same sheet.
    # The credential store is shared deliberately: on a real machine the credential
    # outlives the process, and a restart that could not find it would be a different
    # bug from the one this test is about.
    restarted = build_context(
        settings,
        fake_state=FakeState(),
        sheets_state=sheets_state,
        credential_store=ctx.credentials,
        log_to_stderr=False,
    )
    try:
        with restarted.sessions() as session:
            config = sheets_service.get_config(session)
            sheets_service.enqueue_reports(session, config, now=BASE)
            session.commit()

            # The destination is unreachable for this whole first life.
            sheets_state.fail_read_with = ("network", "down during the first process's life")
            first = sheets_service.sync_once(restarted, session, config=config, now=BASE)
            session.commit()
            assert first.ok is False
            assert "Could not read" in first.diagnostic
            assert session.query(SheetReportRecord).filter_by(sync_key=key).one().status == (
                "pending"
            ), "an unread destination is not a delivery"

        # New process, network back. The outbox is exactly where it was left.
        sheets_state.fail_read_with = None
        with restarted.sessions() as session:
            config = sheets_service.get_config(session)
            result = sheets_service.sync_once(
                restarted, session, config=config, now=BASE + timedelta(hours=1)
            )
            session.commit()
            assert result.appended == 1
            record = session.query(SheetReportRecord).filter_by(sync_key=key).one()
            assert record.status == "synced"
    finally:
        restarted.close()


def test_a_row_built_before_a_restart_is_the_same_row_after_it(
    ctx, settings, sheets_state, connection, clip
) -> None:
    """Determinism across restarts, which is what makes the key useful at all."""
    with ctx.sessions() as session:
        job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
        sheets_service.upsert_config(
            session,
            {
                "spreadsheet_id": "sheet-1",
                "worksheet": "Report",
                "connection_id": connection.id,
                "enabled": True,
            },
            now=BASE,
        )
        session.commit()
        first_key = make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)

    restarted = build_context(
        settings,
        fake_state=FakeState(),
        sheets_state=sheets_state,
        credential_store=InMemoryCredentialStore(),
        log_to_stderr=False,
    )
    try:
        with restarted.sessions() as session:
            config = sheets_service.get_config(session)
            sheets_service.enqueue_reports(session, config, now=BASE + timedelta(days=1))
            session.commit()
            record = session.query(SheetReportRecord).one()
            assert record.sync_key == first_key
    finally:
        restarted.close()


# ==========================================================================
# Credentials, expiry, and destinations that cannot be reached
# ==========================================================================


def test_a_missing_credential_says_so_and_sends_nothing(
    ctx, session, sheets_state, reporting, sheets_connection, connection, clip
) -> None:
    """``COMP-10``: "no credential" and "the request failed" are different answers."""
    # The *reporting* connection's credential, not the YouTube one — reporting reads its
    # own grant, and deleting the wrong one would leave this test passing vacuously.
    ctx.credentials.delete(f"conn:{sheets_connection.public_id}:access")
    ctx.credentials.delete(f"conn:{sheets_connection.public_id}:refresh")
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()

    # Nothing is sent — but note the *read* never happens either, because the failure is
    # upstream of the transport. That is the diagnostic's whole point.
    result = deliver(ctx, session, reporting, now=BASE)
    session.commit()

    assert result.ok is False
    assert "No credential is stored" in result.diagnostic
    assert "nothing was sent to Google" in result.diagnostic
    assert sheets_state.append_calls == 0
    assert sheets_state.read_calls == 0


def test_an_inaccessible_worksheet_is_classified_not_found(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    sheets_state.visible_worksheets.clear()
    ok, message = sheets_service.verify_access(ctx, session, now=BASE)
    assert ok is False
    assert "could not be found" in message


def test_an_inaccessible_spreadsheet_is_classified_not_found(
    ctx, session, sheets_state, reporting
) -> None:
    sheets_state.visible_spreadsheets.clear()
    ok, message = sheets_service.verify_access(ctx, session, now=BASE)
    assert ok is False
    assert "could not be found" in message


def test_a_forbidden_destination_points_at_sharing_not_at_the_network(
    ctx, session, sheets_state, reporting
) -> None:
    sheets_state.fail_with = ("forbidden", "the account cannot access this")
    ok, message = sheets_service.verify_access(ctx, session, now=BASE)
    assert ok is False
    assert "permission problem, not a network problem" in message


def test_an_expired_grant_points_at_reauthorization(ctx, session, sheets_state, reporting) -> None:
    sheets_state.fail_with = ("expired", "the grant is gone")
    ok, message = sheets_service.verify_access(ctx, session, now=BASE)
    assert ok is False
    assert "Reauthorize" in message


def test_a_revoked_connection_stops_reporting_without_erasing_the_configuration(
    ctx, session, sheets_state, reporting, sheets_connection
) -> None:
    """Disabling/revoking must not erase bookkeeping — ``COMP-07``."""
    sheets_connection.revoked_at = BASE
    session.commit()
    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()
    assert result.ok is False
    assert "revoked" in result.diagnostic
    assert sheets_service.get_config(session) is not None
    assert sheets_service.get_config(session).enabled is True


def test_a_successful_verify_records_the_time(ctx, session, sheets_state, reporting) -> None:
    ok, message = sheets_service.verify_access(ctx, session, now=BASE)
    assert ok is True
    session.commit()
    assert sheets_service.get_config(session).verified_at == BASE
    assert "Reached" in message


def test_a_test_write_writes_exactly_one_probe_row_and_never_a_report_row(
    ctx, session, sheets_state, reporting
) -> None:
    ok, message = sheets_service.test_write(ctx, session, now=BASE)
    assert ok is True
    assert len(sheets_state.rows) == 1
    assert sheets_state.rows[0][KEY_COLUMN].startswith("TEST_")
    assert "Delete it" in message


def test_reporting_disabled_means_no_read_and_no_write(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    reporting.enabled = False
    session.commit()
    result = sheets_service.sync_once(ctx, session, now=BASE)
    assert result.ok is False
    assert "not enabled" in result.diagnostic
    assert sheets_state.read_calls == 0
    assert sheets_state.append_calls == 0


# ==========================================================================
# Daily completion summaries
# ==========================================================================


def test_the_daily_summary_distinguishes_the_five_outcomes(
    ctx, session, reporting, connection, clip
) -> None:
    """``01`` §9: successful, failed, cancelled, pending, and manual review."""
    states = {
        "JOB-PUBLISHED": "Succeeded",
        "JOB-FAILED": "Failed",
        "JOB-CANCELLED": "Cancelled",
        "JOB-NEEDS_REVIEW": "Manual Review",
        "JOB-QUEUED": "Pending",
        "JOB-SCHEDULED": "Pending",
    }
    for state in states:
        job = make_job(session, make_media(session, clip), connection, title=state, publish_at=BASE)
        job.state = state
        session.commit()

    counts_by_channel = dict(
        sheets_service.summarise_day(session, reporting, report_date=PUBLISHED_KEY_DATE)
    )
    counts = counts_by_channel["UC-main"]
    assert counts["Succeeded"] == 1
    assert counts["Failed"] == 1
    assert counts["Cancelled"] == 1
    assert counts["Manual Review"] == 1
    assert counts["Pending"] == 2


def test_a_retrying_job_counts_as_pending_not_as_failed(
    ctx, session, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.FAILED_RETRY.value
    session.commit()
    counts = dict(sheets_service.summarise_day(session, reporting, report_date=PUBLISHED_KEY_DATE))[
        "UC-main"
    ]
    assert counts["Pending"] == 1
    assert counts["Failed"] == 0


def test_the_summary_row_uses_the_same_key_column_so_it_deduplicates_identically(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    session.commit()

    assert (
        sheets_service.enqueue_summaries(
            session, reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
        )
        == 1
    )
    session.commit()
    assert (
        sheets_service.enqueue_summaries(
            session, reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
        )
        == 0
    )
    assert session.query(SheetSummaryRecord).count() == 1

    # Both blocks go through the same reader and the same key column, so one pass
    # delivers both and neither can collide with the other.
    result = deliver(ctx, session, reporting, now=BASE)
    session.commit()
    assert result.appended == 2, "one upload row and one summary row"
    keys = [row[KEY_COLUMN] for row in sheets_state.rows]
    assert len(keys) == len(set(keys))
    assert any(k.startswith("SUM_") for k in keys)
    assert any(k.startswith("SCH_") for k in keys)


def test_the_summary_can_be_switched_off_without_losing_the_upload_rows(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    make_job(
        session, make_media(session, clip), connection, publish_at=BASE
    ).state = JobState.PUBLISHED.value
    session.commit()
    reporting.include_daily_summary = False
    session.commit()

    assert (
        sheets_service.enqueue_summaries(
            session, reporting, report_date=PUBLISHED_KEY_DATE, now=BASE
        )
        == 0
    )
    result = deliver(ctx, session, reporting, now=BASE)
    session.commit()
    assert result.appended == 1
    assert all(row[KEY_COLUMN].startswith("SCH_") for row in sheets_state.rows)


def test_a_summary_for_a_different_day_does_not_pick_up_todays_jobs(
    ctx, session, reporting, connection, clip
) -> None:
    make_job(
        session, make_media(session, clip), connection, publish_at=BASE
    ).state = JobState.PUBLISHED.value
    session.commit()
    other_day = PUBLISHED_KEY_DATE - timedelta(days=7)
    counts = dict(sheets_service.summarise_day(session, reporting, report_date=other_day))
    assert counts == {}


# ==========================================================================
# The worker integration, and the invariant that matters most
# ==========================================================================


def test_the_worker_report_sweep_runs_and_records_the_outcome(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    make_job(
        session, make_media(session, clip), connection, publish_at=BASE
    ).state = JobState.PUBLISHED.value
    session.commit()

    result = ReportSyncSweep(ctx).run(now=BASE)
    assert result.ran is True
    assert result.appended == 2, "the upload row and the daily summary"
    assert len(sheets_state.rows) == 2


def test_the_worker_report_sweep_does_nothing_when_reporting_is_off(
    ctx, session, sheets_state, reporting
) -> None:
    reporting.enabled = False
    session.commit()
    result = ReportSyncSweep(ctx).run(now=BASE)
    assert result.ran is False
    assert sheets_state.read_calls == 0


def test_a_sheets_failure_cannot_stop_the_worker_or_touch_a_job(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """A report failure is recorded, never escalated, and never reaches a job."""
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.SCHEDULED.value
    job.video_id = "fake-0009"
    session.commit()
    before = reload_state(ctx, job.id)

    sheets_state.fail_with = ("network", "everything is on fire")
    result = ReportSyncSweep(ctx).run(now=BASE)

    assert result.ran is True
    assert result.failures >= 1
    assert reload_state(ctx, job.id) == before, "the job is untouched by a report failure"


def test_the_sheets_module_cannot_reach_an_upload(ctx) -> None:
    """Structural: the module's import surface contains nothing that uploads.

    This is the enforcement of "a report write must never trigger another video
    upload", checked against the module's own namespace rather than by trusting a
    comment. A future import of a runner or a video connector fails here.
    """
    import hexton.services.sheets as module

    banned = {
        "JobRunner",
        "PublishConfirmationSweep",
        "YouTubeConnector",
        "FakeConnector",
        "insert",
        "upload",
    }
    namespace = set(dir(module))
    assert not (namespace & banned), f"the sheets service gained {sorted(namespace & banned)}"


def reload_state(ctx, job_id: int) -> tuple:
    with ctx.sessions() as session:
        job = session.get(ScheduledJob, job_id)
        return (
            job.state,
            job.video_id,
            job.insert_may_have_succeeded,
            job.published_at_remote,
        )


# ==========================================================================
# The API surface
# ==========================================================================


@pytest.fixture
def client(ctx):
    from hexton.api.app import create_app

    app = create_app(ctx)
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as c:
        yield c


@pytest.fixture
def auth(client) -> dict:
    """A session plus its CSRF token, as every mutating route requires."""
    from hexton.api.security import CSRF_HEADER

    response = client.post("/api/session")
    assert response.status_code == 200, response.text
    return {CSRF_HEADER: response.json()["csrf_token"]}


@pytest.fixture
def accepted_client(client, auth):
    """A client that has accepted the privacy policy, and can mutate.

    Reporting is an API-connected feature, so ``COMP-01`` gates it. The gate being in
    force is asserted separately in ``test_compliance.py``; here it is simply satisfied,
    so these tests are about reporting rather than about the gate.
    """
    response = client.post(
        "/api/compliance/policy/accept", json={"accepted_by": "tester"}, headers=auth
    )
    assert response.status_code == 200, response.text
    return client


def test_the_config_route_reports_required_and_optional_columns(accepted_client, reporting) -> None:
    response = accepted_client.get("/api/reporting/config")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert KEY_COLUMN in body["required_columns"]
    assert "Video Title" in body["optional_columns"]


def test_enabling_without_a_destination_is_refused_by_the_api(accepted_client, auth) -> None:
    response = accepted_client.put("/api/reporting/config", json={"enabled": True}, headers=auth)
    assert response.status_code == 422
    assert "before a spreadsheet" in response.json()["detail"]


def test_the_health_route_lists_the_reconciliation_limits(accepted_client, reporting) -> None:
    body = accepted_client.get("/api/reporting/health").json()
    assert len(body["reconciliation_limits"]) >= 4
    assert any("never edits or deletes" in limit for limit in body["reconciliation_limits"])


def test_the_preview_route_never_claims_published_without_evidence(
    accepted_client, ctx, session, reporting, connection, clip
) -> None:
    job = make_job(
        session, make_media(session, clip), connection, state="JOB-SCHEDULED", publish_at=BASE
    )
    job.video_id = "fake-0010"
    job.uploaded_at = BASE
    session.commit()

    body = accepted_client.get(f"/api/reporting/preview/{job.id}").json()
    assert body["claims_published"] is False
    assert body["video_id_present"] is True
    assert body["verified_publication_time_present"] is False


def test_a_job_with_no_publication_time_has_no_preview(
    accepted_client, ctx, session, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=None)
    session.commit()
    response = accepted_client.get(f"/api/reporting/preview/{job.id}")
    assert response.status_code == 409
    assert "will not invent one" in response.json()["detail"]


def test_the_outbox_route_separates_upload_rows_from_summaries(
    accepted_client, ctx, session, sheets_state, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    session.commit()
    sheets_service.enqueue_reports(session, reporting, now=BASE)
    sheets_service.enqueue_summaries(session, reporting, report_date=PUBLISHED_KEY_DATE, now=BASE)
    session.commit()

    body = accepted_client.get("/api/reporting/outbox").json()
    kinds = sorted({row["kind"] for row in body["rows"]})
    assert kinds == ["daily_summary", "upload"]
    upload = next(r for r in body["rows"] if r["kind"] == "upload")
    assert upload["schedule_id"] == job.public_id
    assert upload["sync_key"] == make_sync_key(PUBLISHED_KEY_DATE, job.public_id, SCHEMA_VERSION)


def test_no_token_ever_appears_in_a_reporting_response(
    accepted_client, ctx, session, reporting, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    for path in (
        "/api/reporting/config",
        "/api/reporting/health",
        "/api/reporting/outbox",
        "/api/reporting/limits",
        f"/api/reporting/preview/{job.id}",
    ):
        assert FAKE_SHEETS_TOKEN not in accepted_client.get(path).text


def test_sync_now_refuses_when_reporting_is_disabled(
    accepted_client, auth, ctx, session, reporting
) -> None:
    reporting.enabled = False
    session.commit()
    response = accepted_client.post("/api/reporting/sync", headers=auth)
    assert response.status_code == 409
    assert "separate, deliberate step" in response.json()["detail"]


def test_sync_now_runs_a_pass(
    accepted_client, auth, ctx, session, sheets_state, reporting, connection, clip
) -> None:
    make_job(
        session, make_media(session, clip), connection, publish_at=BASE
    ).state = JobState.PUBLISHED.value
    session.commit()
    body = accepted_client.post("/api/reporting/sync", headers=auth).json()
    assert body["ok"] is True
    assert body["appended"] == 2
    assert body["read_after_ambiguous_write"] is False
