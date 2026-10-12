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
from sqlalchemy import select


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


def test_the_upload_queue_yields_to_the_quota_reserve(ctx, session, new_job) -> None:
    """F-26: ``quota.RESERVE_UNITS`` promises exactly this and nothing delivered it.

    ``can_afford`` was consulted by the publication sweep (reads) and by ``_push_status``
    (the 50-unit ``videos.update``) — never by a path about to issue a ``videos.insert``.
    With the shared 10,000-unit budget spent, the queue kept inserting until YouTube
    refused, spending the reserve that exists so verification polling still runs. Without
    verification there is no route from ``SCHEDULED`` to ``PUBLISHED`` at all.
    """
    from hexton import quota

    job = new_job(state="JOB-QUEUED", publish_at=FUTURE)
    conn = job.connection
    assert conn is not None
    # Everything except the reserve is already spent in this window.
    quota.add_units_on(session, conn.provider, quota.DAILY_UNITS - quota.RESERVE_UNITS, BASE)
    session.commit()

    before = ctx.connector.call_count("start_resumable_upload")
    runner_for(ctx).run_once(now=BASE)

    assert ctx.connector.call_count("start_resumable_upload") == before, "no insert was sent"
    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value, "deferred, not failed and not uploaded"
    assert row.failure_kind == FailureKind.QUOTA.value
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > BASE
    assert row.next_attempt_at <= quota.next_reset(BASE) + timedelta(minutes=1)
    messages = [msg for _k, msg in events_for(ctx, job.id)]
    assert any("reserve" in msg.lower() for msg in messages)


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


def test_a_reschedule_youtube_refused_is_not_recorded_as_scheduled_on_youtube(
    ctx, session, new_job
) -> None:
    """F-05: ``-> SCHEDULED`` is a claim about YouTube, so YouTube has to earn it.

    Reproduced before this was fixed: a job sitting in ``JOB-UPLOADED`` was rescheduled
    while the ``videos.update`` was refused, and it *still* landed in ``JOB-SCHEDULED``.
    The queue then read "Scheduled on YouTube" for a video YouTube was holding with no
    ``publishAt`` at all, while ``failure_kind`` and ``failure_message`` were both empty
    — so nothing an operator actually reads admitted the two had diverged, and the only
    trace of the refusal was a job event.
    """
    from hexton.connectors.base import VideoStatus

    job = new_job(state=JobState.UPLOADED.value, publish_at=FUTURE)
    with fresh(ctx) as s:
        row = s.get(ScheduledJob, job.id)
        row.video_id = "vid-refused"
        row.uploaded_at = BASE
        s.commit()
    ctx.connector.state.videos["vid-refused"] = VideoStatus(
        video_id="vid-refused", privacy_status="private", upload_status="uploaded"
    )
    ctx.connector.state.fail_always["update_video_status"] = FailureKind.PERMANENT

    new_time = FUTURE + timedelta(days=4)
    with fresh(ctx) as s:
        reschedule_job(ctx, s, s.get(ScheduledJob, job.id), new_time, now=BASE)
        s.commit()

    row = reload(ctx, job.id)
    # The local plan really did move — refusing to send is not refusing to plan.
    assert row.schedule.publish_at_utc == new_time
    assert row.state == JobState.UPLOADED.value, "must not claim YouTube took a schedule"
    assert row.failure_kind == FailureKind.HUMAN_REQUIRED.value
    assert row.failure_message is not None
    assert "disagree" in row.failure_message
    assert "YouTube did not accept the change" in row.failure_message
    # And the remote really does not hold the new time.
    assert ctx.connector.state.videos["vid-refused"].publish_at is None

    # Recovery: clear the cause and reschedule again, and only the disagreement this
    # function wrote is cleared — not some other failure the job may be carrying.
    del ctx.connector.state.fail_always["update_video_status"]
    later = new_time + timedelta(days=1)
    with fresh(ctx) as s:
        reschedule_job(ctx, s, s.get(ScheduledJob, job.id), later, now=BASE)
        s.commit()

    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.failure_kind is None
    assert row.failure_message is None
    assert ctx.connector.state.videos["vid-refused"].publish_at == later


def test_a_reschedule_never_calls_a_video_with_no_id_scheduled(ctx, session, new_job) -> None:
    """There is nothing on YouTube to schedule, so no state may say otherwise.

    ``NEEDS_REVIEW -> SCHEDULED`` is legal, which made an ambiguous job with no video
    ID claim "Scheduled on YouTube" the moment its publication time was edited.
    """
    job = new_job(state=JobState.NEEDS_REVIEW.value, publish_at=FUTURE)
    session.commit()
    assert job.video_id is None

    with fresh(ctx) as s:
        reschedule_job(ctx, s, s.get(ScheduledJob, job.id), FUTURE + timedelta(days=3), now=BASE)
        s.commit()

    row = reload(ctx, job.id)
    assert row.state == JobState.NEEDS_REVIEW.value
    assert row.failure_message is None  # nothing was refused; nothing was sent either
    assert ctx.connector.call_count("update_video_status") == 0


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


def test_a_video_that_is_public_but_not_yet_labelled_published_cannot_be_rescheduled(
    ctx, session, new_job
) -> None:
    """F-05: the DF-03 refusal keyed only on ``state is PUBLISHED``.

    ``published_at_remote`` and the newest observation are independent evidence about
    the *video*, and either can be ahead of the state machine. Reading only the label
    let a genuinely public video be handed a ``publishAt`` that the documented
    behaviour says is not honoured — so the local row would show a schedule that
    YouTube has silently declined.
    """
    from hexton.db.models import RemoteObservation

    job = new_job(state="JOB-UPLOADED")
    with fresh(ctx) as s:
        row = s.get(ScheduledJob, job.id)
        row.video_id = "vid-was-public"
        s.add(
            RemoteObservation(
                job_id=row.id,
                observed_at=BASE,
                remote_state="public",
                privacy_status="public",
                inconclusive=False,
            )
        )
        s.commit()

    with fresh(ctx) as s:
        with pytest.raises(JobOperationError) as excinfo:
            reschedule_job(ctx, s, s.get(ScheduledJob, job.id), FUTURE, now=BASE)
    assert excinfo.value.code == "already_published"
    assert "never published" in str(excinfo.value)
    # Nothing was pushed and nothing was recorded as moved.
    assert ctx.connector.call_count("update_video_status") == 0
    assert reload(ctx, job.id).schedule.publish_at_utc != FUTURE


def test_a_video_recorded_as_published_earlier_is_refused_on_the_same_grounds(
    ctx, session, new_job
) -> None:
    """The sweep's own timestamp is evidence enough on its own."""
    job = new_job(state="JOB-SCHEDULED")
    with fresh(ctx) as s:
        row = s.get(ScheduledJob, job.id)
        row.video_id = "vid-swept"
        row.published_at_remote = BASE - timedelta(minutes=5)
        s.commit()

    with fresh(ctx) as s:
        with pytest.raises(JobOperationError) as excinfo:
            reschedule_job(ctx, s, s.get(ScheduledJob, job.id), FUTURE, now=BASE)
    assert excinfo.value.code == "already_published"


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


def test_cancelling_a_job_whose_upload_outcome_is_unknown_does_not_say_nothing_was_created(
    ctx, session, new_job
) -> None:
    """F-08: "No video had been created" was asserted purely from ``video_id is None``.

    A video ID only exists once the provider's answer came back. When the transfer ended
    without one, the ID is absent precisely *because* the outcome is unknown — and that
    is the one case where telling an operator nothing was created is most damaging: they
    would re-upload, and possibly hand a client a link to the duplicate.
    """
    job = new_job(state=JobState.NEEDS_REVIEW.value)
    job.insert_may_have_succeeded = True
    session.commit()

    outcome = cancel_job(ctx, session, job, now=BASE, reason="changed my mind")
    session.commit()

    assert outcome.local_cancelled is True
    assert outcome.remote_attempted is False
    assert outcome.remote_succeeded is None
    assert "may still exist on YouTube" in outcome.message
    assert "No video had been created" not in outcome.message
    assert reload(ctx, job.id).cancel_remote_outcome == "remote_unknown"
    # The flag survives the cancellation: cancelling is not a resolution.
    assert reload(ctx, job.id).insert_may_have_succeeded is True


def test_a_job_mid_transfer_cannot_be_cancelled_out_from_under_the_worker(
    ctx, session, new_job
) -> None:
    """F-08: ``cancel_job`` wrote ``JOB-CANCELLED`` directly into the column.

    ``UPLOADING -> CANCELLED`` is not a legal transition, so the write only looked like
    a cancellation: the worker commits its own result a moment later and overwrites it,
    leaving a row that says "cancelled" beside a video that exists. The service now
    refuses instead of recording something the state machine will undo.
    """
    job = new_job(state="JOB-UPLOADING")
    job.lease_owner = "worker-a"
    job.lease_expires_at = BASE + timedelta(seconds=60)
    session.commit()

    with pytest.raises(JobOperationError) as excinfo:
        cancel_job(ctx, session, job, now=BASE)
    assert excinfo.value.code == "upload_in_progress"
    assert reload(ctx, job.id).state == JobState.UPLOADING.value
    assert reload(ctx, job.id).cancelled_at is None


def test_a_job_whose_uploading_lease_has_lapsed_can_be_cancelled(ctx, session, new_job) -> None:
    """The refusal is about a *live* transfer, not about the label on the row."""
    job = new_job(state="JOB-UPLOADING")
    job.lease_owner = "worker-a"
    job.lease_expires_at = BASE - timedelta(seconds=1)
    session.commit()

    outcome = cancel_job(ctx, session, job, now=BASE)
    session.commit()

    assert outcome.local_cancelled is True
    assert reload(ctx, job.id).state == JobState.CANCELLED.value


def test_progress_stops_the_transfer_when_the_lease_is_lost(ctx, new_job, monkeypatch) -> None:
    """F-10: ``heartbeat()``'s answer was discarded.

    ``False`` means another worker has claimed this job. Carrying on would put two
    processes inside one resumable session — the duplicate-upload case the lease
    protocol exists to prevent — so the progress callback raises and unwinds the
    transfer instead of silently continuing.
    """
    import hexton.worker.runner as runner_mod
    from hexton.worker.runner import LeaseLost

    job = new_job()
    runner = runner_for(ctx)

    monkeypatch.setattr(runner_mod, "heartbeat", lambda *_a, **_k: False)
    with pytest.raises(LeaseLost):
        runner._report_progress(job.id, 10, 100, BASE)

    # And a heartbeat that still succeeds does not.
    monkeypatch.setattr(runner_mod, "heartbeat", lambda *_a, **_k: True)
    runner._report_progress(job.id, 10, 100, BASE)
    assert reload(ctx, job.id).progress_percent == 10


def test_a_lost_lease_mid_transfer_leaves_the_job_to_its_new_owner(
    ctx, new_job, monkeypatch
) -> None:
    """The lease-lost path records nothing: the row belongs to whoever holds it now."""
    from hexton.worker.runner import LeaseLost

    job = new_job()
    runner = runner_for(ctx)

    def steal(*_args, **_kwargs):
        raise LeaseLost(job.id)

    monkeypatch.setattr(runner, "_report_progress", steal)

    result = runner.run_once(now=BASE)
    assert result is not None
    assert result.state == "lease_lost", "the transfer reported where it stopped"

    row = reload(ctx, job.id)
    # Not failed, not reviewed, not marked by us in any way.
    assert row.state == JobState.UPLOADING.value
    assert row.failure_kind in (None, "", FailureKind.NONE.value)
    assert not any(k == ev.KIND_RETRY_EXHAUSTED for k, _ in events_for(ctx, job.id))


def test_a_grant_that_cannot_refresh_starts_the_df21_deletion_clock(ctx, session, new_job) -> None:
    """F-16: ``schedule_for_unrefreshable_token`` existed and was never called.

    The 30-calendar-day deadline ``DF-21`` requires when a grant stops refreshing was
    therefore never written — whether Authorized Data was deleted in time depended on
    someone happening to notice the connection had gone stale. ``revoked_by`` stayed
    ``"client"`` too, recording an operator action nobody took.
    """
    from hexton.db.models import DataRetention

    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_always["refresh"] = FailureKind.AUTHORIZATION

    runner_for(ctx).run_once(now=BASE)

    row = reload(ctx, job.id)
    assert row.state == JobState.BLOCKED.value
    assert row.failure_kind == FailureKind.AUTHORIZATION.value

    session.expire_all()
    conn = session.get(ScheduledJob, job.id).connection
    assert conn.last_token_check_ok is False, "the compliance diagnostic reads this"
    assert conn.last_token_check_at == BASE

    rows = (
        session.execute(select(DataRetention).where(DataRetention.subject_id == conn.public_id))
        .scalars()
        .all()
    )
    assert rows, "DF-21's deadline must actually be written"
    assert rows[0].reason == "revocation_google"
    assert rows[0].deadline == BASE + timedelta(days=30)


def test_the_df21_clock_is_started_once_and_never_pushed_out(ctx, session, new_job) -> None:
    """A retry that keeps failing must not keep rescheduling the deletion."""
    from hexton.db.models import DataRetention

    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_always["refresh"] = FailureKind.AUTHORIZATION

    for _ in range(3):
        runner_for(ctx).run_once(now=BASE)

    conn = session.get(ScheduledJob, job.id).connection
    rows = (
        session.execute(select(DataRetention).where(DataRetention.subject_id == conn.public_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1, "one clock per connection, or the deadline never arrives"


def test_a_grant_without_a_refresh_token_starts_the_same_clock(ctx, session, new_job) -> None:
    """DF-17's seven-day expiry is the same situation from DF-21's point of view."""
    from hexton.db.models import DataRetention

    job = new_job(publish_at=FUTURE)
    conn = session.get(ScheduledJob, job.id).connection
    ctx.credentials.delete(f"conn:{conn.public_id}:refresh")
    ctx.credentials.delete(f"conn:{conn.public_id}:access")
    conn.token_expires_at = None
    session.commit()

    runner_for(ctx).run_once(now=BASE)

    assert reload(ctx, job.id).state == JobState.BLOCKED.value
    rows = (
        session.execute(select(DataRetention).where(DataRetention.subject_id == conn.public_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# ======================================================================#
# Deletion
# ======================================================================#


def test_a_draft_and_a_plain_cancelled_job_are_deletable(ctx, session, new_job) -> None:
    from hexton.services.jobs import delete_draft

    draft = new_job(state="JOB-DRAFT")
    delete_draft(session, draft)
    session.flush()

    cancelled = new_job(state="JOB-CANCELLED")
    delete_draft(session, cancelled)
    session.flush()
    assert session.get(ScheduledJob, cancelled.id) is None


def test_a_cancelled_job_that_still_points_at_a_video_is_not_deletable(
    ctx, session, new_job
) -> None:
    """F-17: ``delete_draft``'s whole guard was the state label.

    ``CANCELLED`` was on the allowed list, but a cancelled job is routinely the one
    holding a video ID — cancelling after upload is the normal way a publication gets
    pulled back. Deleting it then removed the only local record of a video that still
    exists on YouTube, with no way to reconstruct which schedule produced it.
    """
    from hexton.services.jobs import delete_draft

    job = new_job(state="JOB-CANCELLED")
    job.video_id = "vid-cancelled"
    session.commit()

    with pytest.raises(JobOperationError) as excinfo:
        delete_draft(session, job)
    assert excinfo.value.code == "not_deletable"
    assert "still refers to a video" in str(excinfo.value)
    session.rollback()
    assert session.get(ScheduledJob, job.id) is not None


def test_a_cancelled_job_with_an_unresolved_upload_is_not_deletable(ctx, session, new_job) -> None:
    """No video ID, and still not deletable — the outcome is what is being kept."""
    from hexton.services.jobs import delete_draft

    job = new_job(state="JOB-CANCELLED")
    job.insert_may_have_succeeded = True
    session.commit()

    with pytest.raises(JobOperationError) as excinfo:
        delete_draft(session, job)
    assert excinfo.value.code == "not_deletable"
    session.rollback()


def test_an_in_flight_job_is_never_deletable(ctx, session, new_job) -> None:
    from hexton.services.jobs import delete_draft

    for state in ("JOB-QUEUED", "JOB-UPLOADING", "JOB-UPLOADED", "JOB-SCHEDULED"):
        job = new_job(state=state)
        session.flush()
        with pytest.raises(JobOperationError) as excinfo:
            delete_draft(session, job)
        assert excinfo.value.code == "not_deletable"
        session.rollback()


# ======================================================================#
# The videos.update gate (D-6 / FEAS-06 / DF-06)
# ======================================================================#


def test_the_snippet_gate_refuses_a_manual_required_job(ctx, session, new_job) -> None:
    """``may_issue_videos_update`` existed since Phase 0 and nothing ever called it.

    ``07-feasibility-checklist.md`` §FEAS-06 records it as the thing that "encodes the
    failure fallback": *the app must not issue ``videos.update`` on ``manual_required``
    jobs at all*. It was a documented intention, not a property of the running system —
    both ``videos.update`` sites sent their request unconditionally.
    """
    from hexton.services.jobs import may_issue_snippet_update

    job = new_job(state="JOB-SCHEDULED", publish_at=FUTURE)
    job.content.mention_requested = True
    job.content.description = "clip with @alice in it"
    job.content.mention_state = "manual_required"
    session.commit()

    allowed, why = may_issue_snippet_update(job)
    assert allowed is False
    assert "DF-06" in why
    assert "Studio" in why

    # ...and every other state still passes, because only D-6 pins a prohibition.
    for state, requested in (
        ("not_requested", False),
        ("text_only", True),
        ("verified", True),
        ("failed", True),
    ):
        job.content.mention_requested = requested
        job.content.mention_state = state
        # ``current_state`` deliberately refuses to grant VERIFIED from a column: it
        # needs the attestation timestamp, which is the whole point of that state.
        job.content.mention_confirmed_at = BASE if state == "verified" else None
        session.commit()
        assert may_issue_snippet_update(job)[0] is True, state


def test_rescheduling_a_manual_required_job_does_not_touch_youtube(ctx, session, new_job) -> None:
    """The local plan moves; YouTube is told plainly that it was not asked to move."""
    job = new_job(state="JOB-SCHEDULED", publish_at=FUTURE)
    job.video_id = "vid-manual"
    job.content.mention_requested = True
    job.content.description = "clip with @alice in it"
    job.content.mention_state = "manual_required"
    session.commit()
    before = ctx.connector.call_count("update_video_status")

    new_time = FUTURE + timedelta(days=2)
    reschedule_job(ctx, session, job, new_time, now=BASE)
    session.commit()

    assert ctx.connector.call_count("update_video_status") == before
    row = reload(ctx, job.id)
    # The local plan really did move - this is a refusal to send, not a refusal to plan.
    assert row.schedule.publish_at_utc == new_time
    # ...and the record says the two disagree, rather than leaving the disagreement
    # buried in an event log the queue never shows (F-05).
    assert row.failure_kind == FailureKind.HUMAN_REQUIRED.value
    assert row.failure_message is not None and "disagree" in row.failure_message
    messages = [msg for _k, msg in events_for(ctx, job.id)]
    assert any("YouTube still holds the previous publication time" in m for m in messages)
    assert any("no videos.update was sent" in m for m in messages)


def test_cancelling_a_manual_required_job_says_the_video_may_still_publish(
    ctx, session, new_job
) -> None:
    """The one sentence an operator must not be left without."""
    job = new_job(state="JOB-SCHEDULED", publish_at=FUTURE)
    job.video_id = "vid-manual-2"
    job.content.mention_requested = True
    job.content.description = "clip with @alice in it"
    job.content.mention_state = "manual_required"
    session.commit()

    outcome = cancel_job(ctx, session, job, now=BASE)
    session.commit()

    assert outcome.local_cancelled is True
    assert outcome.remote_attempted is False
    assert outcome.remote_succeeded is None
    assert "may still publish at its original time" in outcome.message
    assert "YouTube Studio" in outcome.message
    assert ctx.connector.call_count("update_video_status") == 0
    row = reload(ctx, job.id)
    assert row.state == JobState.CANCELLED.value
    assert row.cancel_remote_outcome == "not_attempted", "and not a success it did not have"


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


def test_an_exception_in_the_job_runner_does_not_end_the_worker(ctx, monkeypatch) -> None:
    """F-07: containment was applied to report-sync only.

    ``_transfer`` built the insert body *outside* its ``try``, so a classifiable
    ``ConnectorError`` — including a consent refusal, which is not a network condition
    — escaped ``run_once``, then ``tick``, then ``run_forever``. The worker process
    exited and every later scheduled upload silently never ran.
    """
    import hexton.worker.main as worker_main

    worker = worker_main.Worker(ctx, owner="worker-containment")
    worker.startup(now=BASE)

    calls = {"n": 0}

    def exploding_run_once(**_kwargs):
        calls["n"] += 1
        raise RuntimeError("the connector blew up mid-transfer")

    monkeypatch.setattr(worker.runner, "run_once", exploding_run_once)

    first = worker.tick(now=BASE)
    second = worker.tick(now=BASE)

    assert calls["n"] == 2, "the loop keeps going rather than unwinding"
    assert first.idle is True and second.idle is True
    assert first.claimed is None


def test_an_exception_in_the_publish_sweep_does_not_end_the_worker(ctx, monkeypatch) -> None:
    import hexton.worker.main as worker_main

    worker = worker_main.Worker(ctx, owner="worker-sweep-containment")
    worker.startup(now=BASE)

    def exploding_sweep(**_kwargs):
        raise RuntimeError("the sweep blew up")

    monkeypatch.setattr(worker.sweep, "run", exploding_sweep)

    report = worker.tick(now=BASE, force_sweep=True)

    assert report.sweep is None
    # The heartbeat is still written afterwards, so liveness is not lost either.
    assert worker.ctx is not None


def test_the_worker_starts_when_retention_itself_is_broken(ctx, monkeypatch) -> None:
    """F-07/F-01: ``startup`` runs the retention sweep, and that sweep used to be able
    to take the whole worker down with it."""
    import hexton.worker.main as worker_main

    def boom(*_args, **_kwargs):
        raise RuntimeError("retention is broken")

    monkeypatch.setattr(worker_main.RetentionSweeper, "run", boom)

    worker = worker_main.Worker(ctx, owner="worker-retention-containment")
    assert worker.startup(now=BASE) == []

    report = worker.tick(now=BASE)
    assert report.idle is True


def test_a_publish_time_that_passes_mid_transfer_is_recorded_not_raised(
    ctx, new_job, monkeypatch
) -> None:
    """F-06: ``_insert_body`` raises ``ConnectorError`` when the publish time has passed.

    It was called *outside* the ``try`` that classifies connector failures, so a
    permanent, already-classified refusal — the DF-04 guard that stops a ``publishAt``
    in the past from becoming an immediate public upload — escaped unhandled. The job
    kept no recorded decision and the exception took the worker down with it instead of
    failing one job.
    """
    from hexton.connectors.base import ConnectorError

    job = new_job()
    runner = runner_for(ctx)

    # The DF-04 guard itself: a publish time already in the past is a classified,
    # permanent refusal — not an unexpected crash — and it changes nothing on its own.
    with fresh(ctx) as s:
        row = s.get(ScheduledJob, job.id)
        row.schedule.publish_at_utc = PAST
        with pytest.raises(ConnectorError) as excinfo:
            runner._insert_body(row)
        assert excinfo.value.retryable is False
        assert row.state == JobState.QUEUED.value
        # Put the plan back so preflight approves the job and the *transfer* is what
        # fails — that is the ordering the defect actually lived in.
        row.schedule.publish_at_utc = FUTURE
        s.commit()

    # Now the real path. The guard lives inside the classification ``try`` now, so a
    # ``ConnectorError`` raised *by the body builder itself* — not by the network —
    # must produce a recorded decision instead of propagating. Re-pointing the method
    # reproduces exactly that timing: the publish time passes after preflight has
    # already approved the job.
    def late_guard(_job):
        raise ConnectorError(
            "The publication time passed while this job was waiting.",
            kind=FailureKind.PERMANENT,
            retryable=False,
        )

    live = runner_for(ctx)
    live._insert_body = late_guard  # type: ignore[method-assign]

    result = live.run_once(now=BASE)
    assert result is not None, "the pass produced a decision instead of an exception"

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED.value
    assert row.failure_kind == FailureKind.PERMANENT.value
    assert "publication time passed" in (row.failure_message or "")
    assert any(kind == ev.KIND_RETRY_EXHAUSTED for kind, _ in events_for(ctx, job.id))
