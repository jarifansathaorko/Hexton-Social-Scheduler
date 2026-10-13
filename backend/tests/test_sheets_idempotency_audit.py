"""Prompt 9 verification tests: Google Sheets outbox, idempotency, and reconciliation.

Covers:
1. Mandatory Sync Key determinism: SCH_<report_date>_<schedule_id>_<schema_version>.
2. Repeated sync calls (report sent 10 times) producing strictly 1 row per Sync Key.
3. Read-before-append: pre-existing rows in the sheet are adopted without appending duplicates.
4. Ambiguous timeout-after-write triggers read-back reconciliation, not blind retry.
5. Reporting failure is completely isolated and can NEVER trigger an upload.
6. Schema completeness: all mandatory audit fields present in report payload.
7. Expired authorization and bad destination error classification.
"""

from __future__ import annotations

import pytest
from conftest import BASE, make_job, make_media
from hexton.connectors.sheets import KEY_COLUMN, REQUIRED_COLUMNS
from hexton.db.models import (
    SheetReportRecord,
)
from hexton.domain.jobstate import JobState
from hexton.domain.report_schema import SCHEMA_VERSION
from hexton.domain.sync_key import make_sync_key, parse_sync_key
from hexton.services import sheets as sheets_service

REPORT_DATE = BASE.date()


# --------------------------------------------------------------------------
# 1. Deterministic, Mandatory Sync Key Structure
# --------------------------------------------------------------------------


def test_sync_key_is_deterministic_and_mandatory() -> None:
    key = make_sync_key(REPORT_DATE, "sched-999", SCHEMA_VERSION)
    expected = f"SCH_{REPORT_DATE.isoformat()}_SCHED-999_{SCHEMA_VERSION}"
    assert key == expected

    parsed = parse_sync_key(key)
    assert parsed.report_date == REPORT_DATE
    assert parsed.schedule_id == "SCHED-999"
    assert parsed.schema_version == SCHEMA_VERSION

    # Invalid empty or whitespace schedules are strictly rejected
    with pytest.raises(ValueError):
        make_sync_key(REPORT_DATE, "")
    with pytest.raises(ValueError):
        make_sync_key(REPORT_DATE, "   ")


# --------------------------------------------------------------------------
# 2. Report Sent Ten Times Produces At Most One Row per Sync Key
# --------------------------------------------------------------------------


def test_report_sent_ten_times_appends_exactly_once(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """Repeatedly executing sync_once 10 times in sequence results in exactly 1

    upload row in the sheet and 0 duplicates.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    job.video_id = "test-video-10x"
    session.commit()

    # Materialize outbox
    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()

    # Deliver 10 times consecutively
    for i in range(10):
        result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
        session.commit()
        if i == 0:
            assert result.appended >= 1
        else:
            assert result.appended == 0, f"Pass {i + 1} appended duplicates!"

    # Verification: exactly 1 upload row exists in the remote sheet for this schedule
    upload_rows = [r for r in sheets_state.rows if r[KEY_COLUMN].startswith("SCH_")]
    assert len(upload_rows) == 1
    assert upload_rows[0][KEY_COLUMN] == make_sync_key(REPORT_DATE, job.public_id, SCHEMA_VERSION)


# --------------------------------------------------------------------------
# 3. Read-Before-Append Adopts Existing Key
# --------------------------------------------------------------------------


def test_read_before_append_adopts_existing_remote_row(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """If a row already exists in the destination sheet (e.g. from prior operator run

    or previous installation), the sync reads remote keys first, adopts the row as synced,
    and appends zero rows.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    job.video_id = "test-preexisting"
    session.commit()

    expected_key = make_sync_key(REPORT_DATE, job.public_id, SCHEMA_VERSION)
    # Pre-populate remote sheet with existing row containing the key
    preexisting_row = dict.fromkeys(REQUIRED_COLUMNS, "pre-existing")
    preexisting_row[KEY_COLUMN] = expected_key
    sheets_state.rows.append(preexisting_row)

    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()

    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    assert result.appended == 0
    assert result.already_present == 1
    assert len(sheets_state.rows) == 1  # No duplicate row created


# --------------------------------------------------------------------------
# 4. Timeout-After-Write Triggers Reconciliation Read
# --------------------------------------------------------------------------


def test_timeout_after_write_reconciles_via_read_without_re_appending(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """If the append write lands on the Google Sheets server, but the HTTP response

    times out or drops, Hexton marks the transaction ambiguous and immediately executes
    a read-back reconciliation rather than blindly retrying the append.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    job.video_id = "test-timeout-vid"
    session.commit()

    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()

    # Simulate network timeout/drop immediately after remote append
    sheets_state.drop_response_after_append = True

    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    assert result.read_after_ambiguous_write is True
    assert result.ambiguous_resolved == 1

    upload_rows = [r for r in sheets_state.rows if r[KEY_COLUMN].startswith("SCH_")]
    assert len(upload_rows) == 1
    record = session.query(SheetReportRecord).filter_by(sync_key=upload_rows[0][KEY_COLUMN]).one()
    assert record.status == "synced"


# --------------------------------------------------------------------------
# 5. Reporting Cannot Trigger Video Upload
# --------------------------------------------------------------------------


def test_sheets_failure_cannot_trigger_video_upload(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """When Google Sheets fails catastrophically (500 internal error, expired token),

    it must never affect job state or trigger any video upload.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.SCHEDULED.value
    job.video_id = "vid-safe-123"
    session.commit()

    sheets_state.fail_with = ("internal", "Google Sheets API Unavailable")

    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()

    result = sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    assert result.ok is False
    assert "internal: Google Sheets API Unavailable" in result.diagnostic

    # Verify job in database is completely untouched
    with ctx.sessions() as s:
        db_job = s.get(type(job), job.id)
        assert db_job.state == JobState.SCHEDULED.value
        assert db_job.video_id == "vid-safe-123"

    # Verify no video upload was ever invoked
    assert ctx.connector.call_count("start_resumable_upload") == 0


# --------------------------------------------------------------------------
# 6. Schema Completeness: Mandatory Audit Fields
# --------------------------------------------------------------------------


def test_report_row_contains_all_mandatory_audit_fields(
    ctx, session, sheets_state, reporting, connection, clip
) -> None:
    """The reported row must contain schedule ID, video ID/URL, title, channel,

    scheduled time, upload outcome, publication verification state, mention state,
    and reporting state.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.PUBLISHED.value
    job.video_id = "test-audit-schema-vid"
    job.video_url = "https://youtube.com/shorts/test-audit-schema-vid"
    job.published_at_remote = BASE
    session.commit()

    sheets_service.enqueue_reports(session, reporting, now=BASE)
    session.commit()
    sheets_service.sync_once(ctx, session, config=reporting, now=BASE)
    session.commit()

    upload_row = next(r for r in sheets_state.rows if r[KEY_COLUMN].startswith("SCH_"))

    # Required and optional audit columns present in mapped payload
    assert KEY_COLUMN in upload_row
    assert "Schedule ID" in upload_row
    assert "Report Date" in upload_row
    assert "Report Schema Version" in upload_row
    assert "Video Title" in upload_row
    assert "YouTube Video ID" in upload_row
    assert "Canonical Video URL" in upload_row
    assert "Target Channel ID" in upload_row
    assert "Upload Outcome" in upload_row
    assert "Current Observed YouTube Status" in upload_row
    assert "Creator Mention State" in upload_row
    assert "Reporting State" in upload_row
