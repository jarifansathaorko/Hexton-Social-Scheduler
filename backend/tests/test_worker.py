"""Phase 2 §8 acceptance and regression tests.

Everything here runs against a real SQLite file, a real ``FakeConnector`` and a real
credential store, because the properties being tested — atomic claiming, persisted
session URIs, digest re-checks, state transitions — only exist in the real wiring.

The recurring theme is duplicate prevention: at no point may a job whose previous
``videos.insert`` might have been accepted issue a second one. Several tests assert
exactly that by counting how many resumable sessions were ever opened.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from conftest import BASE, FUTURE, PAST, events_for, fresh, reload
from hexton.db import events as ev
from hexton.db.models import (
    ChannelIdentity,
    MediaFile,
    PlatformConnection,
    ScheduledJob,
)
from hexton.domain.jobstate import FailureKind, JobState
from hexton.services.jobs import (
    JobOperationError,
    cancel_job,
    reschedule_job,
)
from hexton.worker.runner import JobRunner, canonical_url


def runner_for(ctx, owner: str = "worker-a") -> JobRunner:
    return JobRunner(ctx, owner=owner)


def job_state(ctx, job_id: int) -> str:
    with fresh(ctx) as s:
        return s.get(ScheduledJob, job_id).state


# ======================================================================#
# Valid and invalid media
# ======================================================================#


def test_a_valid_clip_is_uploaded_and_the_video_id_is_persisted(ctx, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    result = runner_for(ctx).run_once(now=BASE)

    assert result is not None and result.job_id == job.id
    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.video_id is not None
    assert row.video_url == canonical_url(row.video_id)
    assert row.uploaded_at is not None
    assert row.progress_percent == 100
    assert row.lease_owner is None
    assert ctx.connector.state.created_video_ids == [row.video_id]


def test_a_file_with_no_video_header_is_blocked_before_anything_is_sent(
    ctx, session, new_job, tmp_path
) -> None:
    """A renamed non-video must not reach the upload path, whatever it is called."""
    path = tmp_path / "not-really.mp4"
    payload = b"RIFFxxxxWAVEfmt " + b"\x00" * 64
    path.write_bytes(payload)

    media = MediaFile(
        path=str(path),
        original_name=path.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        container=None,
        is_valid=True,
        validated_at=BASE,
    )
    session.add(media)
    session.flush()

    job = new_job(media=media)
    session.commit()

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert row.failure_kind == FailureKind.VALIDATION.value
    assert "not recognised as a video clip" in (row.failure_message or "")
    assert ctx.connector.call_count("start_resumable_upload") == 0
    assert any(kind == ev.KIND_PREFLIGHT_FAILED for kind, _ in events_for(ctx, job.id))


def test_a_missing_file_blocks_the_job_rather_than_uploading_something_else(
    ctx, new_job, clip
) -> None:
    job = new_job()
    clip.unlink()

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert "no longer present" in (row.failure_message or "")
    assert ctx.connector.call_count("start_resumable_upload") == 0


def test_a_file_replaced_after_queueing_is_blocked(ctx, new_job, clip) -> None:
    """§1: a changed file must never be uploaded silently under an approved title."""
    job = new_job()
    original = clip.read_bytes()
    clip.write_bytes(original[:-1] + b"\x01")  # same length, different bytes

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert row.failure_kind == FailureKind.VALIDATION.value
    assert "no longer matches the SHA-256" in (row.failure_message or "")
    assert ctx.connector.call_count("start_resumable_upload") == 0


# ======================================================================#
# Missing and past publication times
# ======================================================================#


def test_a_job_with_no_publication_time_is_uploaded_but_not_claimed_published(ctx, new_job) -> None:
    """An upload that succeeded is not a publication."""
    job = new_job(publish_at=None)

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.UPLOADED.value
    assert row.video_id is not None
    assert row.state != JobState.PUBLISHED.value
    assert row.next_poll_at == BASE


def test_a_past_publication_time_hard_fails_and_sends_nothing(ctx, new_job) -> None:
    """DF-04: a past publishAt publishes immediately, so it is never corrected."""
    job = new_job(publish_at=PAST)

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.MISSED_WINDOW.value
    assert ctx.connector.call_count("start_resumable_upload") == 0
    assert ctx.connector.state.created_video_ids == []
    kinds = [k for k, _ in events_for(ctx, job.id)]
    assert ev.KIND_MISSED in kinds
    assert ev.KIND_PREFLIGHT_FAILED not in kinds  # it is "missed", not "invalid"


def test_rescheduling_a_missed_job_to_the_past_is_refused(ctx, session, new_job) -> None:
    job = new_job(publish_at=PAST)
    with pytest.raises(JobOperationError) as excinfo:
        reschedule_job(ctx, session, job, PAST - timedelta(hours=1), now=BASE)
    assert excinfo.value.code == "invalid_time"


# ======================================================================#
# Duplicate execution
# ======================================================================#


def test_a_job_cannot_be_claimed_by_two_workers_at_once(ctx, new_job) -> None:
    new_job()
    first = runner_for(ctx, "worker-a").run_once(now=BASE)
    second = runner_for(ctx, "worker-b").run_once(now=BASE)
    assert first is not None
    assert second is None  # the job was leased and then completed


def test_a_published_job_is_never_touched_again(ctx, new_job) -> None:
    """Terminal means terminal: no second insert, no state churn, no provider call."""
    job = new_job(state="JOB-PUBLISHED")
    row = reload(ctx, job.id)
    row.video_id = "vid-already"
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).video_id = "vid-already"
        s.commit()

    runner_for(ctx).execute(job.id, now=BASE)

    assert ctx.connector.call_count("start_resumable_upload") == 0
    assert job_state(ctx, job.id) == JobState.PUBLISHED.value


def test_a_cancelled_job_is_never_touched_again(ctx, new_job) -> None:
    job = new_job(state="JOB-CANCELLED")
    runner_for(ctx).execute(job.id, now=BASE)
    assert ctx.connector.call_count("start_resumable_upload") == 0
    assert job_state(ctx, job.id) == JobState.CANCELLED.value


# ======================================================================#
# Authorization and channel identity
# ======================================================================#


def test_an_expired_consent_screen_blocks_the_job_with_the_documented_reason(ctx, new_job) -> None:
    """DF-17: refresh tokens expire seven days after consent while in Testing."""
    job = new_job()
    with fresh(ctx) as s:
        conn = s.get(ScheduledJob, job.id).connection
        ctx.credentials.delete(f"conn:{conn.public_id}:access")
        ctx.credentials.delete(f"conn:{conn.public_id}:refresh")

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert row.failure_kind == FailureKind.AUTHORIZATION.value
    assert row.next_attempt_at is None  # blocked, not retrying
    assert "DF-17" in (row.failure_message or "")
    assert ctx.connector.call_count("start_resumable_upload") == 0


def test_a_revoked_connection_blocks_the_job(ctx, new_job) -> None:
    job = new_job()
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).connection.status = "revoked"
        s.commit()

    runner_for(ctx).run_once(now=BASE)
    assert job_state(ctx, job.id) == JobState.BLOCKED.value
    assert ctx.connector.call_count("start_resumable_upload") == 0


def test_a_job_with_no_channel_identity_is_blocked_not_guessed(ctx, new_job) -> None:
    job = new_job()
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).channel_identity_id = None
        s.commit()

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert "Hexton will not guess" in (row.failure_message or "")


def test_a_channel_from_another_connection_is_refused(ctx, session, new_job) -> None:
    """Publishing to the wrong channel is the failure the whole product prevents."""
    other = PlatformConnection(provider="youtube", label="Other", status="active", scopes=[])
    session.add(other)
    session.flush()
    foreign = ChannelIdentity(
        connection_id=other.id,
        channel_id="UC-other",
        title="Other channel",
        verification_source="channels_list_mine",
    )
    session.add(foreign)
    session.commit()

    job = new_job()
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).channel_identity_id = foreign.id
        s.commit()

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert "channel-mismatch" in (row.failure_message or "")
    assert ctx.connector.call_count("start_resumable_upload") == 0


# ======================================================================#
# Interrupted upload, worker restart, ambiguous response
# ======================================================================#


def test_an_interrupted_transfer_resumes_the_persisted_session(ctx, new_job) -> None:
    """§2: the session URI survives, so a retry never re-opens a second insert."""
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_next["perform_upload"] = 1

    runner_a = runner_for(ctx, "worker-a")
    assert runner_a.run_once(now=BASE) is not None

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.next_attempt_at is not None and row.next_attempt_at > BASE
    assert row.upload_sessions[0].session_uri  # persisted for recovery
    assert row.upload_sessions[0].phase == "started"
    assert ctx.connector.call_count("start_resumable_upload") == 1

    # --- the machine is killed here: the lease is still held by a dead worker ---
    with fresh(ctx) as s:
        stale = s.get(ScheduledJob, job.id)
        stale.lease_owner = "worker-a"
        stale.lease_expires_at = BASE - timedelta(seconds=1)
        stale.next_attempt_at = BASE - timedelta(seconds=1)
        s.commit()

    # A new process starts, reclaims the orphan, and carries on.
    runner_b = runner_for(ctx, "worker-b")
    assert runner_b.reclaim_orphans(now=BASE) == [job.id]
    assert runner_b.run_once(now=BASE) is not None

    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.video_id is not None
    assert row.attempt == 2
    # The decisive assertion: one session, one insert, resumed rather than restarted.
    assert ctx.connector.call_count("start_resumable_upload") == 1
    assert len(ctx.connector.state.created_video_ids) == 1


def test_a_killed_workers_lease_is_reclaimed_at_startup(ctx, new_job) -> None:
    job = new_job()
    from hexton.worker.lease import claim_next

    claim_next(ctx.engine, owner="dead-worker", now=BASE, lease_seconds=60)

    stale = runner_for(ctx, "worker-b").reclaim_orphans(now=BASE + timedelta(seconds=61))
    assert job.id in stale
    assert reload(ctx, job.id).lease_owner is None


def test_an_ambiguous_response_holds_the_job_and_never_re_inserts(ctx, new_job) -> None:
    """The whole duplicate-prevention argument in one test."""
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.drop_upload_response = True

    runner = runner_for(ctx)
    runner.run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.NEEDS_REVIEW.value
    assert row.insert_may_have_succeeded is True
    assert row.video_id is None  # no ID was returned, so nothing is claimed
    assert ctx.connector.call_count("start_resumable_upload") == 1
    assert any(k == ev.KIND_UPLOAD_AMBIGUOUS for k, _ in events_for(ctx, job.id))

    # The queue will not even claim it — NEEDS_REVIEW is not a claimable state.
    assert runner_for(ctx, "worker-b").run_once(now=BASE) is None

    # Even a forced pass must reconcile rather than re-send.
    runner.execute(job.id, now=BASE + timedelta(minutes=1))

    assert job_state(ctx, job.id) == JobState.NEEDS_REVIEW.value
    assert reload(ctx, job.id).insert_may_have_succeeded is True
    assert ctx.connector.call_count("start_resumable_upload") == 1, (
        "a second videos.insert was issued while the first outcome was unknown"
    )
    assert ctx.connector.state.created_video_ids == []


def test_needs_review_offers_no_automatic_retry(ctx, new_job) -> None:
    from hexton.domain.jobstate import can_retry, primary_action

    job = new_job(state="JOB-NEEDS_REVIEW")
    assert can_retry(JobState(job_state(ctx, job.id))) is False
    assert primary_action(JobState(job_state(ctx, job.id))) == "Resolve"


# ======================================================================#
# Retries, backoff, quota
# ======================================================================#


def test_a_transient_failure_is_scheduled_with_backoff(ctx, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_next["perform_upload"] = 1

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.failure_kind == FailureKind.TRANSIENT.value
    assert row.next_attempt_at is not None
    delay = (row.next_attempt_at - BASE).total_seconds()
    assert 30 <= delay <= 45  # 30s base plus at most 20% deterministic jitter
    assert any(k == ev.KIND_RETRY_SCHEDULED for k, _ in events_for(ctx, job.id))


def test_a_retried_job_makes_progress_without_a_second_session(ctx, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_next["perform_upload"] = 1
    runner = runner_for(ctx)
    runner.run_once(now=BASE)

    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).next_attempt_at = BASE
        s.commit()
    runner.run_once(now=BASE + timedelta(seconds=45))

    assert job_state(ctx, job.id) == JobState.SCHEDULED.value
    assert ctx.connector.call_count("start_resumable_upload") == 1


def test_spending_the_retry_budget_ends_in_a_terminal_failure(ctx, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    job.max_attempts = 2
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).max_attempts = 2
        s.commit()
    ctx.connector.state.fail_next["perform_upload"] = 5

    runner = runner_for(ctx)
    runner.run_once(now=BASE)
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).next_attempt_at = BASE
        s.commit()
    runner.run_once(now=BASE + timedelta(seconds=60))

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED.value
    assert row.attempt == 2
    assert any(k == ev.KIND_RETRY_EXHAUSTED for k, _ in events_for(ctx, job.id))


def test_a_quota_error_waits_for_the_documented_reset(ctx, new_job) -> None:
    from hexton import quota

    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_kind["start_resumable_upload"] = FailureKind.QUOTA
    ctx.connector.state.fail_next["start_resumable_upload"] = 1

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > quota.next_reset(BASE)  # the *next* window, not later today
    assert row.next_attempt_at <= quota.next_reset(BASE) + timedelta(minutes=1)


def test_an_authorization_failure_blocks_instead_of_retrying(ctx, new_job) -> None:
    job = new_job()
    ctx.connector.state.fail_always["start_resumable_upload"] = FailureKind.AUTHORIZATION

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert row.next_attempt_at is None
    assert any(k == ev.KIND_AUTH_FAILED for k, _ in events_for(ctx, job.id))


# ======================================================================#
# Rescheduling
# ======================================================================#


def test_rescheduling_before_upload_changes_the_local_plan(ctx, session, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    new_time = FUTURE + timedelta(days=3)

    reschedule_job(ctx, session, job, new_time, now=BASE)
    session.commit()

    row = reload(ctx, job.id)
    assert row.schedule.publish_at_utc == new_time
    assert row.rescheduled_at == BASE
    assert row.state == JobState.QUEUED.value  # QUEUED has no edge to SCHEDULED
    assert ctx.connector.call_count("update_video_status") == 0  # nothing to push yet
    assert any(k == ev.KIND_RESCHEDULED for k, _ in events_for(ctx, job.id))


def test_rescheduling_an_existing_video_is_pushed_to_youtube(ctx, session, new_job) -> None:
    """DF-06 applies: the whole snippet travels with the change or it is deleted."""
    job = new_job(publish_at=FUTURE)
    runner_for(ctx).run_once(now=BASE)
    row = reload(ctx, job.id)
    assert row.video_id is not None

    with fresh(ctx) as s:
        job2 = s.get(ScheduledJob, job.id)
        new_time = FUTURE + timedelta(days=4)
        reschedule_job(ctx, s, job2, new_time, now=BASE)
        s.commit()

    assert ctx.connector.call_count("update_video_status") == 1
    assert ctx.connector.state.videos[row.video_id].publish_at == new_time
    events = events_for(ctx, job.id)
    assert any(k == ev.KIND_RESCHEDULED and "YouTube accepted" in msg for k, msg in events)


def test_a_published_video_cannot_be_rescheduled(ctx, session, new_job) -> None:
    """DF-03: publishAt applies only to a video that has never been published."""
    job = new_job(state="JOB-PUBLISHED")
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).video_id = "vid-pub"
        s.commit()

    with fresh(ctx) as s:
        with pytest.raises(JobOperationError) as excinfo:
            reschedule_job(ctx, s, s.get(ScheduledJob, job.id), FUTURE, now=BASE)
    assert excinfo.value.code == "already_published"
    assert "never been published" in str(excinfo.value)


def test_rescheduling_to_a_past_time_is_refused(ctx, session, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    with pytest.raises(JobOperationError) as excinfo:
        reschedule_job(ctx, session, job, PAST, now=BASE)
    assert excinfo.value.code == "invalid_time"


# ======================================================================#
# Cancellation
# ======================================================================#


def test_cancelling_before_upload_says_nothing_was_created(ctx, session, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    outcome = cancel_job(ctx, session, job, now=BASE, reason="changed my mind")
    session.commit()

    assert outcome.local_cancelled is True
    assert outcome.remote_attempted is False
    assert outcome.remote_succeeded is None
    assert "No video had been created" in outcome.message
    assert job.cancel_remote_outcome == "not_applicable"
    assert reload(ctx, job.id).state == JobState.CANCELLED.value
    assert any(k == ev.KIND_CANCELLED_LOCAL for k, _ in events_for(ctx, job.id))


def test_cancelling_a_scheduled_publication_reports_the_remote_outcome(
    ctx, session, new_job
) -> None:
    job = new_job(publish_at=FUTURE)
    runner_for(ctx).run_once(now=BASE)
    video_id = reload(ctx, job.id).video_id

    with fresh(ctx) as s:
        outcome = cancel_job(ctx, s, s.get(ScheduledJob, job.id), now=BASE)
        s.commit()

    assert outcome.remote_attempted is True
    assert outcome.remote_succeeded is True
    assert "cancelled on YouTube" in outcome.message
    assert ctx.connector.state.videos[video_id].privacy_status == "private"
    assert ctx.connector.state.videos[video_id].publish_at is None
    assert reload(ctx, job.id).cancel_remote_outcome == "set_private"


def test_deleting_the_remote_video_is_a_separate_explicit_choice(ctx, session, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    runner_for(ctx).run_once(now=BASE)
    video_id = reload(ctx, job.id).video_id

    with fresh(ctx) as s:
        outcome = cancel_job(ctx, s, s.get(ScheduledJob, job.id), now=BASE, delete_remote=True)
        s.commit()

    assert outcome.remote_succeeded is True
    assert "was deleted from YouTube" in outcome.message
    assert video_id not in ctx.connector.state.videos
    assert reload(ctx, job.id).cancel_remote_outcome == "deleted"


def test_a_remote_cancellation_failure_is_reported_not_swallowed(ctx, session, new_job) -> None:
    """The operator must never believe the video is gone when it is not."""
    job = new_job(publish_at=FUTURE)
    runner_for(ctx).run_once(now=BASE)
    ctx.connector.state.fail_always["update_video_status"] = FailureKind.PERMANENT

    with fresh(ctx) as s:
        outcome = cancel_job(ctx, s, s.get(ScheduledJob, job.id), now=BASE)
        s.commit()

    assert outcome.local_cancelled is True
    assert outcome.remote_attempted is True
    assert outcome.remote_succeeded is False
    assert "may still publish at its original time" in outcome.message
    assert reload(ctx, job.id).cancel_remote_outcome == "remote_failed"
    assert any(k == ev.KIND_CANCEL_REMOTE_FAILED for k, _ in events_for(ctx, job.id))


def test_cancelling_without_a_credential_reports_remote_unknown(ctx, session, new_job) -> None:
    job = new_job(publish_at=FUTURE)
    runner_for(ctx).run_once(now=BASE)
    with fresh(ctx) as s:
        conn = s.get(ScheduledJob, job.id).connection
        ctx.credentials.delete(f"conn:{conn.public_id}:access")
        ctx.credentials.delete(f"conn:{conn.public_id}:refresh")

    with fresh(ctx) as s:
        outcome = cancel_job(ctx, s, s.get(ScheduledJob, job.id), now=BASE)
        s.commit()

    assert outcome.remote_attempted is False
    assert "checked by hand" in outcome.message
    assert reload(ctx, job.id).cancel_remote_outcome == "remote_unknown"


def test_a_published_job_cannot_be_cancelled(ctx, session, new_job) -> None:
    job = new_job(state="JOB-PUBLISHED")
    with pytest.raises(JobOperationError) as excinfo:
        cancel_job(ctx, session, job, now=BASE)
    assert excinfo.value.code == "not_cancellable"


# ======================================================================#
# Mentions reaching the UI honestly
# ======================================================================#


def test_a_requested_mention_needs_manual_action_but_does_not_block_upload(
    ctx, session, new_job
) -> None:
    job = new_job(publish_at=FUTURE)
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).content.mention_requested = True
        s.get(ScheduledJob, job.id).content.description = "shout out to @someone"
        s.commit()

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value  # the caption still went up
    assert row.content.mention_state == "manual_required"
    assert "DF-19" in (row.content.mention_reason or "")


# ----------------------------------------------------------------------
# Liveness: the API is a separate process and sees nothing else
# ----------------------------------------------------------------------


def test_the_worker_publishes_a_heartbeat_the_api_can_read(ctx) -> None:
    """Nothing else tells the API whether the queue is being serviced.

    The ``worker_heartbeat`` table was read by three endpoints and written by nobody,
    so every environment reported "no worker" no matter how many were running.
    """
    from hexton.db.models import WorkerHeartbeat
    from hexton.worker.heartbeat import latest_heartbeat, worker_is_alive
    from hexton.worker.main import Worker
    from sqlalchemy import select

    worker = Worker(ctx)
    window = ctx.settings.heartbeat_seconds
    session = ctx.sessions()
    try:
        assert worker_is_alive(session, heartbeat_seconds=window, now=BASE) is False
    finally:
        session.close()

    worker.startup(now=BASE)
    worker.tick(now=BASE)

    session = ctx.sessions()
    try:
        assert worker_is_alive(session, heartbeat_seconds=window, now=BASE) is True
        row = latest_heartbeat(session)
        assert row is not None
        assert row.owner == worker.runner.owner
        assert row.pid is not None
        assert row.started_at == BASE
        assert row.beat_at == BASE
    finally:
        session.close()

    # The loop runs every couple of seconds; it must update one row, not append.
    worker.tick(now=BASE + timedelta(seconds=2))
    worker.tick(now=BASE + timedelta(seconds=4))

    session = ctx.sessions()
    try:
        rows = session.execute(select(WorkerHeartbeat)).scalars().all()
        assert len(rows) == 1
        assert rows[0].beat_at == BASE + timedelta(seconds=4)
        assert rows[0].started_at == BASE  # first start, not the latest pass
    finally:
        session.close()

    # Silent for long enough to have missed two expected beats, and it is no longer
    # alive — a stale row must not keep claiming a worker that is gone. A single
    # late beat still counts, so the boundary is tested on both sides of it.
    last_beat = BASE + timedelta(seconds=4)
    window_seconds = window * 2
    assert (
        worker_is_alive(
            ctx.sessions(),
            heartbeat_seconds=window,
            now=last_beat + timedelta(seconds=window_seconds - 1),
        )
        is True
    )
    assert (
        worker_is_alive(
            ctx.sessions(),
            heartbeat_seconds=window,
            now=last_beat + timedelta(seconds=window_seconds),
        )
        is False
    )


def test_a_worker_that_cannot_beat_keeps_publishing(ctx, monkeypatch) -> None:
    """A heartbeat failure is reported, not raised: jobs matter more than the row."""
    import hexton.worker.main as worker_main

    def boom(*_args, **_kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(worker_main, "beat", boom)

    worker = worker_main.Worker(ctx)
    worker.startup(now=BASE)  # must not raise either
    report = worker.tick(now=BASE)

    assert report.idle is True
