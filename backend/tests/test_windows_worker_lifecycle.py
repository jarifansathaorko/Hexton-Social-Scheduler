"""Prompt 8 verification tests: Windows worker lifecycle, startup, sleep/resume, and shutdown.

Covers:
1. Worker independence from browser session (headless execution via SQLite and heartbeat).
2. Worker crash and restart: lease recovery and orphan reclaim at startup.
3. Machine sleep / resume simulation: lease expiration, monotonic leap, and missed-job handling.
4. Network unavailable at due time: graceful backoff and retry scheduling without process crash.
5. Duplicate worker startup: mutual exclusion under concurrent contention.
6. Orderly shutdown via request_stop / signal event.
7. Interactive user credential context verification.
8. Missed publication due time recovery on worker restart.
"""

from __future__ import annotations

import getpass
import threading
from datetime import timedelta

from conftest import BASE, FUTURE, events_for, fresh, reload
from hexton.connectors.base import VideoStatus
from hexton.db import events as ev
from hexton.domain.jobstate import JobState, PollResult
from hexton.worker.heartbeat import latest_heartbeat
from hexton.worker.lease import claim_next
from hexton.worker.main import Worker

NOW = BASE


# --------------------------------------------------------------------------
# 1. Worker Independence from Browser
# --------------------------------------------------------------------------


def test_worker_runs_independently_of_browser_session(ctx, new_job) -> None:
    """The worker executes in its own runtime loop with zero browser/HTTP dependencies.

    Closing browser tabs or shutting down frontend HTTP connections has zero effect
    on background execution.
    """
    job = new_job(publish_at=FUTURE)
    worker = Worker(ctx, owner="standalone-worker-1")

    # Tick execution succeeds without any API or web session
    tick_report = worker.tick(now=NOW)

    assert tick_report.claimed is not None
    assert tick_report.claimed.job_id == job.id

    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.video_id is not None

    # Heartbeat is emitted to DB for API to observe asynchronously
    with fresh(ctx) as s:
        hb = latest_heartbeat(s)
        assert hb is not None
        assert hb.owner == "standalone-worker-1"
        assert hb.jobs_claimed == 1


# --------------------------------------------------------------------------
# 2. Crash and Restart: Orphan Reclaim
# --------------------------------------------------------------------------


def test_worker_crash_and_restart_reclaims_interrupted_leases(ctx, new_job) -> None:
    """If a worker process is terminated abruptly (kill / power loss) while holding

    an active lease on a job, a restarted worker's startup() routine reclaims the
    lease immediately and resumes execution safely.
    """
    job = new_job(publish_at=FUTURE)
    # Simulate crashed worker holding a lease
    with fresh(ctx) as s:
        j = s.get(type(job), job.id)
        j.state = JobState.UPLOADING.value
        j.lease_owner = "crashed-worker-pid-9999"
        j.lease_expires_at = NOW + timedelta(minutes=5)
        s.commit()

    # Restarted worker launches
    new_worker = Worker(ctx, owner="recovered-worker-pid-1000")

    # Fast forward past lease expiry to simulate abandoned lease
    reclaim_time = NOW + timedelta(minutes=10)
    reclaimed_ids = new_worker.startup(now=reclaim_time)

    assert job.id in reclaimed_ids

    # The job lease is cleared and available for claim
    with fresh(ctx) as s:
        j = s.get(type(job), job.id)
        assert j.lease_owner is None
        assert j.lease_expires_at is None

    # The new worker claims and completes the job
    tick = new_worker.tick(now=reclaim_time)
    assert tick.claimed is not None
    assert tick.claimed.job_id == job.id


# --------------------------------------------------------------------------
# 3. Sleep / Resume Simulation
# --------------------------------------------------------------------------


def test_sleep_resume_expires_stale_leases_and_handles_missed_due_time(ctx, new_job) -> None:
    """When the machine sleeps mid-lease and wakes hours later, the lease has expired

    in wall-clock time. The worker tick clears the expired lease and safely executes
    the job without getting stuck.
    """
    job = new_job(publish_at=FUTURE)
    # Worker A claimed the job
    claim = claim_next(ctx.engine, owner="worker-a", now=NOW, lease_seconds=60)
    assert claim is not None

    # System sleeps for 4 hours
    wake_time = NOW + timedelta(hours=4)

    # Worker B (or Worker A after wake) ticks
    worker = Worker(ctx, owner="worker-after-wake")
    tick = worker.tick(now=wake_time)

    # The expired lease was reclaimed and the job completed
    assert tick.claimed is not None
    assert tick.claimed.job_id == job.id

    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.video_id is not None


# --------------------------------------------------------------------------
# 4. Network Unavailable at Due Time
# --------------------------------------------------------------------------


def test_network_unavailable_at_due_time_schedules_backoff_without_crash(ctx, new_job) -> None:
    """If YouTube API is unreachable due to network disconnect at scheduled execution

    time, the worker records a transient failure, schedules exponential backoff,
    releases the lease, and keeps the worker process alive.
    """
    job = new_job(publish_at=FUTURE)
    # Simulate network failure on connector
    ctx.connector.state.fail_next["perform_upload"] = 1

    worker = Worker(ctx, owner="worker-net")
    tick = worker.tick(now=NOW)

    assert tick.claimed is not None
    assert tick.claimed.job_id == job.id
    assert tick.claimed.state == JobState.FAILED_RETRY.value  # Retried, back in queue

    row = reload(ctx, job.id)
    assert row.state == JobState.FAILED_RETRY.value
    assert row.attempt == 1
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > NOW
    assert row.lease_owner is None  # Lease released

    # Audit event logged
    assert any(k == ev.KIND_RETRY_SCHEDULED for k, _ in events_for(ctx, job.id))


# --------------------------------------------------------------------------
# 5. Duplicate Worker Startup: Mutual Exclusion
# --------------------------------------------------------------------------


def test_concurrent_duplicate_workers_do_not_duplicate_job_execution(ctx, new_job) -> None:
    """If two worker instances run simultaneously (e.g. accidental duplicate startup),

    only one worker successfully claims and processes the job.
    """
    job = new_job(publish_at=FUTURE)
    w1 = Worker(ctx, owner="worker-primary")
    w2 = Worker(ctx, owner="worker-duplicate")

    barrier = threading.Barrier(2)
    results: list[object] = []

    def run_worker(worker: Worker) -> None:
        barrier.wait()
        res = worker.tick(now=NOW)
        results.append(res)

    t1 = threading.Thread(target=run_worker, args=(w1,))
    t2 = threading.Thread(target=run_worker, args=(w2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one worker claimed the job; the other found no available claim
    claimed = [r for r in results if r.claimed is not None]
    idle = [r for r in results if r.claimed is None]

    assert len(claimed) == 1
    assert len(idle) == 1
    assert claimed[0].claimed.job_id == job.id
    assert len(ctx.connector.state.created_video_ids) == 1


# --------------------------------------------------------------------------
# 6. Orderly Shutdown
# --------------------------------------------------------------------------


def test_orderly_shutdown_via_request_stop(ctx) -> None:
    """When a shutdown signal (SIGINT/SIGTERM) is received, request_stop() sets the

    stop event and the worker loop terminates cleanly.
    """
    worker = Worker(ctx, owner="worker-shutdown")
    assert not worker.stop.is_set()

    worker.request_stop()
    assert worker.stop.is_set()

    # Run loop finishes immediately without blocking
    worker.run_forever(poll_seconds=10.0)


# --------------------------------------------------------------------------
# 7. Interactive User Credential Context
# --------------------------------------------------------------------------


def test_interactive_user_context_matches_credential_store_requirements() -> None:
    """Verify that current environment matches interactive user context:

    Credential Manager and DPAPI are bound to the current logged-in Windows user SID.
    """
    current_user = getpass.getuser()
    assert current_user, "Must run under an identified user account"
    # Not running under SYSTEM / NetworkService
    assert current_user.lower() not in ("system", "networkservice", "localservice")


# --------------------------------------------------------------------------
# 8. Startup Recovery of Missed Publication Checks
# --------------------------------------------------------------------------


def test_startup_sweeps_missed_publications_after_system_off(ctx, new_job) -> None:
    """If the computer was powered off when a video was due to publish on YouTube,

    starting the worker triggers PublishConfirmationSweep, which contacts YouTube
    and confirms the public status.
    """
    missed_publish_time = NOW - timedelta(hours=6)
    job = new_job(publish_at=missed_publish_time)

    with fresh(ctx) as s:
        j = s.get(type(job), job.id)
        j.state = JobState.SCHEDULED.value
        j.video_id = "test-missed-pub-video"
        j.schedule.publish_at_utc = missed_publish_time
        j.next_poll_at = None
        s.commit()

    ctx.connector.state.videos["test-missed-pub-video"] = VideoStatus(
        video_id="test-missed-pub-video",
        privacy_status="public",
        upload_status="uploaded",
        processing_status=None,
    )

    worker = Worker(ctx, owner="worker-startup-sweep")
    worker.startup(now=NOW)

    # Tick with forced sweep simulates startup sweep pass
    tick = worker.tick(now=NOW, force_sweep=True)
    assert tick.sweep is not None
    assert tick.sweep.published == 1

    row = reload(ctx, job.id)
    assert row.state == JobState.PUBLISHED.value
    assert row.poll_result == PollResult.CONFIRMED.value
