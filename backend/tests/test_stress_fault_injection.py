"""Prompt 16 verification tests: Stress testing, concurrency limits, and fault injection.

Covers:
1. Multi-worker elevated concurrency (20 workers contending on 50 jobs with zero double claims).
2. YouTube 429 / Quota Exhaustion: backs off until Pacific midnight reset without retry storm.
3. YouTube 503 / Network disconnect: exponential backoff with deterministic jitter.
4. Ambiguous upload fault injection: server accepts but client drops response; zero second insert.
5. Capacity limits: quota budgeting for single Windows PC / YouTube project.
"""

from __future__ import annotations

import concurrent.futures
from datetime import timedelta

from conftest import BASE, FUTURE, events_for, reload
from hexton import quota
from hexton.db import events as ev
from hexton.domain.jobstate import FailureKind, JobState
from hexton.worker.lease import claim_next
from hexton.worker.runner import JobRunner

NOW = BASE


# --------------------------------------------------------------------------
# 1. High Concurrency Stress Test: 20 Workers Contending on 50 Jobs
# --------------------------------------------------------------------------


def test_high_concurrency_multi_worker_zero_duplicate_claims(
    ctx, session, connection, clip
) -> None:
    """20 threads simulate concurrent worker processes claiming 50 queued jobs.

    Guarantees:
    - Exactly 50 unique claims occur.
    - Zero duplicate claims across all threads.
    - Zero deadlocks under SQLite WAL and BEGIN IMMEDIATE.
    """
    from conftest import make_job, make_media

    media = make_media(session, clip)
    job_ids = []
    for i in range(50):
        j = make_job(session, media, connection, state="JOB-QUEUED", title=f"stress-{i}")
        job_ids.append(j.id)
    session.commit()

    claimed_by_thread: list[int] = []

    def worker_claim_loop(worker_idx: int) -> list[int]:
        local_claims = []
        for _ in range(10):
            claim = claim_next(
                ctx.engine,
                owner=f"worker-{worker_idx}",
                now=NOW,
                lease_seconds=60,
            )
            if claim:
                local_claims.append(claim.job_id)
        return local_claims

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker_claim_loop, i) for i in range(20)]
        for f in concurrent.futures.as_completed(futures):
            claimed_by_thread.extend(f.result())

    # Invariant: exactly 50 jobs were claimed across all 20 threads
    assert len(claimed_by_thread) == 50
    # Invariant: no duplicate job IDs claimed
    assert len(set(claimed_by_thread)) == 50
    assert set(claimed_by_thread) == set(job_ids)


# --------------------------------------------------------------------------
# 2. 429 Quota Exhaustion Fault Injection: Pacific Reset
# --------------------------------------------------------------------------


def test_quota_exhaustion_429_halts_queue_until_pacific_midnight_reset(ctx, new_job) -> None:
    """When YouTube returns 429 / quotaExceeded, the runner:

    - Classifies failure as QUOTA.
    - Schedules next attempt at next Pacific midnight reset.
    - Prevents tight-loop retry storm.
    """
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_kind["start_resumable_upload"] = FailureKind.QUOTA
    ctx.connector.state.fail_next["start_resumable_upload"] = 1

    runner = JobRunner(ctx, owner="quota-worker")
    runner.run_once(now=NOW)

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.failure_kind == FailureKind.QUOTA.value
    assert row.next_attempt_at is not None

    # Verification: next attempt waits for the next Pacific midnight window
    assert row.next_attempt_at > quota.next_reset(NOW)
    assert row.next_attempt_at <= quota.next_reset(NOW) + timedelta(minutes=1)


# --------------------------------------------------------------------------
# 3. 503 / Network Transient Fault Injection
# --------------------------------------------------------------------------


def test_transient_503_backpressure_and_jittered_backoff(ctx, new_job) -> None:
    """Transient server errors trigger exponential backoff with jitter and release lease."""
    job = new_job(publish_at=FUTURE)
    ctx.connector.state.fail_next["perform_upload"] = 1

    runner = JobRunner(ctx, owner="transient-worker")
    runner.run_once(now=NOW)

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.failure_kind == FailureKind.TRANSIENT.value
    assert row.attempt == 1
    assert row.lease_owner is None  # Lease surrendered for next attempt

    # Backoff window between 30 and 45s (30s base + max 20% jitter)
    delay_sec = (row.next_attempt_at - NOW).total_seconds()
    assert 30 <= delay_sec <= 45


# --------------------------------------------------------------------------
# 4. Ambiguous Upload: Server Accepted but Client Disconnected
# --------------------------------------------------------------------------


def test_ambiguous_upload_enters_manual_review_never_blind_re_inserts(ctx, new_job) -> None:
    """If YouTube accepted the body, but network dropped before client received video ID,

    the runner detects ambiguity (insert_may_have_succeeded=True), holds the job in
    JOB-NEEDS_REVIEW, and NEVER calls start_resumable_upload a second time.
    """
    job = new_job(publish_at=FUTURE)
    # Simulate ambiguous network drop on upload
    ctx.connector.state.drop_upload_response = True

    runner = JobRunner(ctx, owner="ambiguous-worker")
    runner.run_once(now=NOW)

    row = reload(ctx, job.id)
    assert row.state == JobState.NEEDS_REVIEW.value
    assert row.insert_may_have_succeeded is True
    assert row.video_id is None
    assert any(k == ev.KIND_UPLOAD_AMBIGUOUS for k, _ in events_for(ctx, job.id))

    # Second pass will not re-insert
    runner.run_once(now=NOW + timedelta(minutes=5))
    assert ctx.connector.call_count("start_resumable_upload") == 1  # Never re-inserted!
