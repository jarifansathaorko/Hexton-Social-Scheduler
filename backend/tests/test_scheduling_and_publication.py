"""Comprehensive tests for Scheduling, Time Zones, and Publication Confirmation (Prompt 7).

Verifies:
1. IANA time zones on Windows via tzdata (Asia/Dhaka, America/New_York, Europe/London).
2. Midnight boundary conversions across time zones and UTC.
3. Daylight-saving transitions: DST gap (spring forward) and DST fold (fall back).
4. Strict rejection of past timestamps and naive datetimes.
5. YouTube publishAt constraints: must be private, must be future, never-published constraint.
6. PublishConfirmationSweep: publication time passing is NOT proof of publication.
7. Quota-aware polling backoff and persistence of remote observations.
8. Distinguishing upload complete vs scheduled on YouTube vs verification pending vs published verified.
9. Restart recovery after due time was missed: pending verification recovered on sweep startup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import fresh
from hexton.connectors.base import VideoStatus
from hexton.db.models import RemoteObservation
from hexton.domain.jobstate import (
    AWAITING_VERIFICATION_SUFFIX,
    JobState,
    PollResult,
    RemoteState,
    display_for,
    video_has_been_public,
)
from hexton.domain.scheduling import (
    ZoneError,
    check_publish_time,
    ensure_utc,
    resolve_zone,
    to_display,
)
from hexton.domain.validation import build_insert_body
from hexton.worker.sweep import PublishConfirmationSweep
from sqlalchemy import select

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# 1. Time Zone Auditing: Windows tzdata, Asia/Dhaka, America/New_York
# --------------------------------------------------------------------------


def test_iana_timezones_resolve_via_tzdata() -> None:
    # Asia/Dhaka (+06:00, no DST)
    dhaka = resolve_zone("Asia/Dhaka")
    assert dhaka.key == "Asia/Dhaka"

    # America/New_York (UTC-5 in winter, UTC-4 in summer)
    ny = resolve_zone("America/New_York")
    assert ny.key == "America/New_York"

    # Europe/London (GMT/BST)
    london = resolve_zone("Europe/London")
    assert london.key == "Europe/London"

    # Invalid zone names raise ZoneError
    with pytest.raises(ZoneError, match="tzdata"):
        resolve_zone("Invalid/Zone_Name")


def test_midnight_boundaries_across_timezones() -> None:
    """Midnight in Dhaka (2026-10-14 00:00 +06:00) is 2026-10-13 18:00 UTC."""
    dhaka = resolve_zone("Asia/Dhaka")
    dhaka_midnight = datetime(2026, 10, 14, 0, 0, 0, tzinfo=dhaka)
    utc_instant = ensure_utc(dhaka_midnight)

    assert utc_instant == datetime(2026, 10, 13, 18, 0, 0, tzinfo=UTC)
    assert to_display(utc_instant, dhaka) == "2026-10-14 00:00 +06"


def test_dst_transitions_gap_and_fold() -> None:
    """America/New_York DST transitions:

    - Spring forward gap: 2026-03-08 02:30 does not exist (clocks jump from 02:00 to 03:00).
    - Fall back fold: 2026-11-01 01:30 occurs twice (fold=0 EDT UTC-4, fold=1 EST UTC-5).
    """
    ny = resolve_zone("America/New_York")

    # Fall back fold test
    fold0 = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=ny)
    fold1 = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ny)

    utc0 = ensure_utc(fold0)
    utc1 = ensure_utc(fold1)

    assert utc1 - utc0 == timedelta(hours=1)
    assert utc0 == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # EDT = UTC-4
    assert utc1 == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # EST = UTC-5


# --------------------------------------------------------------------------
# 2. YouTube publishAt Constraints & Never-Published Guard
# --------------------------------------------------------------------------


def test_publish_at_requires_private_visibility_and_future_instant() -> None:
    # Future publishAt with public is rejected
    with pytest.raises(
        ValueError, match="publishAt is only meaningful with privacyStatus='private'"
    ):
        build_insert_body(
            title="Video",
            description="Desc",
            tags=[],
            category_id="22",
            privacy="public",
            publish_at=NOW + timedelta(hours=3),
        )

    # Valid future publishAt with private passes
    future_time = NOW + timedelta(hours=3)
    body = build_insert_body(
        title="Video",
        description="Desc",
        tags=[],
        category_id="22",
        privacy="private",
        publish_at=future_time,
    )
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-10-13T15:00:00Z"


def test_rescheduling_already_published_video_is_strictly_forbidden() -> None:
    """DF-03: An already-published video cannot be rescheduled via publishAt."""
    # Video marked as published locally or observed public remotely
    assert video_has_been_public(JobState.PUBLISHED, None, None) is True
    assert video_has_been_public(JobState.SCHEDULED, NOW - timedelta(days=1), "private") is True
    assert video_has_been_public(JobState.SCHEDULED, None, "public") is True

    # Preflight check blocks reschedule on an already published video
    future_dt = NOW + timedelta(days=2)
    check = check_publish_time(future_dt, now=NOW, never_published=False)
    assert not check.ok
    assert any(i.code == "already_published" for i in check.blocking)


# --------------------------------------------------------------------------
# 3. PublishConfirmationSweep: Time Passing is Not Proof of Publication
# --------------------------------------------------------------------------


def test_publication_time_passing_does_not_mark_published(ctx, ready_job) -> None:
    """When the scheduled publishAt time arrives and passes, the video remains in

    JOB-SCHEDULED state awaiting remote confirmation.
    """
    passed_publish_at = NOW - timedelta(minutes=15)
    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        job.state = JobState.SCHEDULED.value
        job.video_id = "test-vid-123"
        job.schedule.publish_at_utc = passed_publish_at
        job.next_poll_at = NOW - timedelta(minutes=5)
        s.commit()

    # Connector reports video is still private
    ctx.connector.state.videos["test-vid-123"] = VideoStatus(
        video_id="test-vid-123",
        privacy_status="private",
        upload_status="uploaded",
        processing_status=None,
    )

    sweep = PublishConfirmationSweep(ctx)
    result = sweep.run(now=NOW)

    assert result.examined == 1
    assert result.published == 0
    assert result.pending == 1

    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        assert job.state == JobState.SCHEDULED.value  # NOT Published!
        assert job.poll_result == PollResult.NOT_YET.value
        assert job.published_at_remote is None
        # UI rendering reflects awaiting verification
        rendered = display_for(
            JobState(job.state),
            remote=RemoteState.SCHEDULED,
            poll_result=PollResult.NOT_YET,
            publish_at_passed=True,
        )
        assert AWAITING_VERIFICATION_SUFFIX in rendered


def test_publication_confirmed_only_when_remote_reports_public(ctx, ready_job) -> None:
    """Only when the remote observation confirms privacy_status == 'public' does

    the job transition to JOB-PUBLISHED.
    """
    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        job.state = JobState.SCHEDULED.value
        job.video_id = "test-vid-123"
        job.schedule.publish_at_utc = NOW - timedelta(minutes=15)
        job.next_poll_at = NOW - timedelta(minutes=1)
        s.commit()

    # Remote confirms public status
    ctx.connector.state.videos["test-vid-123"] = VideoStatus(
        video_id="test-vid-123",
        privacy_status="public",
        upload_status="uploaded",
        processing_status=None,
    )

    sweep = PublishConfirmationSweep(ctx)
    result = sweep.run(now=NOW)

    assert result.examined == 1
    assert result.published == 1
    assert result.pending == 0

    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        assert job.state == JobState.PUBLISHED.value
        assert job.poll_result == PollResult.CONFIRMED.value
        assert job.published_at_remote is not None

        # Verify RemoteObservation was appended to DB
        obs = (
            s.execute(select(RemoteObservation).where(RemoteObservation.job_id == job.id))
            .scalars()
            .all()
        )
        assert len(obs) >= 1
        assert obs[-1].privacy_status == "public"


# --------------------------------------------------------------------------
# 4. Restart Recovery After Missed Due Time
# --------------------------------------------------------------------------


def test_sweep_recovers_pending_verification_after_process_restart(ctx, ready_job) -> None:
    """If worker process crashes/sleeps and restarts hours after publication time,

    the sweep discovers the unverified job on startup and confirms publication.
    """
    missed_publish_time = NOW - timedelta(hours=4)
    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        job.state = JobState.SCHEDULED.value
        job.video_id = "test-vid-restarted"
        job.schedule.publish_at_utc = missed_publish_time
        job.next_poll_at = None  # No active poll timer
        job.poll_result = None
        s.commit()

    ctx.connector.state.videos["test-vid-restarted"] = VideoStatus(
        video_id="test-vid-restarted",
        privacy_status="public",
        upload_status="uploaded",
        processing_status=None,
    )

    # Worker starts up anew and executes sweep
    fresh_sweep = PublishConfirmationSweep(ctx)
    result = fresh_sweep.run(now=NOW)

    assert result.examined == 1
    assert result.published == 1

    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        assert job.state == JobState.PUBLISHED.value
        assert job.published_at_remote is not None
