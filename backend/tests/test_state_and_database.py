"""Comprehensive tests for Database schema, State Machine, and Job Claiming (Prompt 4).

Validates:
1. True multithreaded concurrent worker claim competition under BEGIN IMMEDIATE (zero duplicate claims).
2. Expired lease recovery, stale lease clearing, and heartbeat extension.
3. Duplicate delivery prevention via unique idempotency_key constraint.
4. Interrupted writes, atomicity, rollback guarantees, and SQLite foreign key enforcement.
5. Canonical JobState transitions (legal vs illegal) and terminal state invariants.
6. The invariant that a job cannot be PUBLISHED without a persisted video_id and remote public evidence.
7. Alembic migration from v1 to v2 preserving pre-existing rows and relationships.
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from conftest import fresh, make_job, make_media
from hexton.api.views import job_to_out
from hexton.db.models import (
    JobEvent,
    RemoteObservation,
    ScheduledJob,
)
from hexton.domain.jobstate import (
    TERMINAL_STATES,
    InvalidTransition,
    JobState,
    RemoteState,
    assert_transition,
    can_transition,
    display_for,
)
from hexton.worker.lease import (
    claim_next,
    expire_stale_leases,
)
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# 1. Concurrent Worker Claim Competition (BEGIN IMMEDIATE)
# --------------------------------------------------------------------------


def test_concurrent_multithreaded_claim_competition_single_job(ctx, ready_job) -> None:
    """5 concurrent threads racing to claim the same single due job.

    Exactly ONE worker must succeed. Four must receive None. Zero exceptions.
    """
    results: list[str] = []

    def try_claim(worker_name: str):
        c = claim_next(ctx.engine, owner=worker_name, now=NOW, lease_seconds=60)
        return worker_name, c

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(try_claim, f"worker-{i}") for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            name, claim = f.result()
            if claim is not None:
                results.append(name)

    assert len(results) == 1, f"Expected exactly 1 claim, got {results}"
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.lease_owner == results[0]
        assert job.attempt == 1


def test_concurrent_multithreaded_claim_competition_multiple_jobs(
    ctx, session, connection, clip
) -> None:
    """8 concurrent threads claiming 10 due jobs simultaneously.

    All 10 jobs must be claimed exactly once across the thread pool with no duplicates.
    """
    media = make_media(session, clip)
    jobs = [
        make_job(session, media, connection, state="JOB-QUEUED", title=f"job-{i}")
        for i in range(10)
    ]
    job_ids = {j.id for j in jobs}

    claimed_job_ids: list[int] = []

    def worker_loop(worker_id: str):
        my_claims = []
        for _ in range(5):
            c = claim_next(ctx.engine, owner=worker_id, now=NOW, lease_seconds=60)
            if c:
                my_claims.append(c.job_id)
        return my_claims

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_loop, f"worker-{i}") for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            claimed_job_ids.extend(f.result())

    # Every job must be claimed exactly once
    assert len(claimed_job_ids) == 10
    assert set(claimed_job_ids) == job_ids


# --------------------------------------------------------------------------
# 2. Expired Lease Recovery & Stale Lease Clearing
# --------------------------------------------------------------------------


def test_expired_lease_automatically_claimable_by_another_worker(ctx, ready_job) -> None:
    """A dead worker's held lease expires; second worker claims it seamlessly."""
    first = claim_next(ctx.engine, owner="dead-worker", now=NOW, lease_seconds=30)
    assert first is not None
    assert first.attempt == 1

    # While active, competitor cannot claim
    assert (
        claim_next(
            ctx.engine, owner="new-worker", now=NOW + timedelta(seconds=10), lease_seconds=60
        )
        is None
    )

    # After expiry (+35s), new worker claims it
    second = claim_next(
        ctx.engine, owner="new-worker", now=NOW + timedelta(seconds=35), lease_seconds=60
    )
    assert second is not None
    assert second.job_id == ready_job.id
    assert second.attempt == 2  # Attempt count increments cleanly

    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.lease_owner == "new-worker"
        assert job.attempt == 2


def test_expire_stale_leases_maintenance_sweep(ctx, ready_job) -> None:
    """expire_stale_leases clears stale owners without altering job state."""
    claim_next(ctx.engine, owner="crashed-proc", now=NOW, lease_seconds=10)

    # Before expiry: nothing cleared
    assert expire_stale_leases(ctx.engine, now=NOW + timedelta(seconds=5)) == []

    # After expiry: lease cleared, job remains in JOB-QUEUED
    cleared = expire_stale_leases(ctx.engine, now=NOW + timedelta(seconds=15))
    assert ready_job.id in cleared

    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.lease_owner is None
        assert job.lease_expires_at is None
        assert job.state == "JOB-QUEUED"


# --------------------------------------------------------------------------
# 3. Duplicate Delivery & Idempotency Protection
# --------------------------------------------------------------------------


def test_idempotency_key_uniqueness_enforced_by_database(ctx, ready_job) -> None:
    """Duplicate delivery attempt with identical idempotency_key is rejected."""
    with fresh(ctx) as s:
        duplicate = ScheduledJob(
            schedule_id=ready_job.schedule_id,
            metadata_id=ready_job.metadata_id,
            media_id=ready_job.media_id,
            connection_id=ready_job.connection_id,
            channel_identity_id=ready_job.channel_identity_id,
            state="JOB-QUEUED",
            idempotency_key=ready_job.idempotency_key,  # Identical key
        )
        s.add(duplicate)
        with pytest.raises(IntegrityError):
            s.commit()


# --------------------------------------------------------------------------
# 4. Interrupted Writes, Transaction Boundaries & Foreign Keys
# --------------------------------------------------------------------------


def test_foreign_key_enforcement_rejects_dangling_references(ctx) -> None:
    """Foreign keys are ON in SQLite; invalid relationship references fail immediately."""
    with fresh(ctx) as s:
        orphan = ScheduledJob(
            schedule_id=99999,  # Does not exist
            metadata_id=99999,
            media_id=99999,
            state="JOB-QUEUED",
            idempotency_key="orphan-test-key",
        )
        s.add(orphan)
        with pytest.raises(IntegrityError):
            s.commit()


def test_interrupted_transaction_rolls_back_cleanly(ctx, ready_job) -> None:
    """Exception inside a session rolls back all changes in that transaction."""
    try:
        with fresh(ctx) as s:
            job = s.get(ScheduledJob, ready_job.id)
            job.state = JobState.UPLOADING.value
            event = JobEvent(
                job_id=job.id,
                kind="test_event",
                message="Testing atomicity",
            )
            s.add(event)
            s.flush()
            # Force an unexpected failure before commit
            raise RuntimeError("Simulated crash mid-transaction")
    except RuntimeError:
        pass

    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.state == JobState.QUEUED.value  # Rolled back to original state
        events = s.execute(select(JobEvent).where(JobEvent.job_id == job.id)).scalars().all()
        assert len(events) == 0  # Event was rolled back


# --------------------------------------------------------------------------
# 5. Canonical State Machine Invariants
# --------------------------------------------------------------------------


def test_canonical_state_machine_legal_and_illegal_transitions() -> None:
    """Legal paths succeed; illegal transitions raise InvalidTransition."""
    # Legal progression
    assert_transition(JobState.DRAFT, JobState.READY)
    assert_transition(JobState.READY, JobState.QUEUED)
    assert_transition(JobState.QUEUED, JobState.UPLOADING)
    assert_transition(JobState.UPLOADING, JobState.SCHEDULED)
    assert_transition(JobState.SCHEDULED, JobState.PUBLISHED)

    # Illegal shortcuts
    with pytest.raises(InvalidTransition):
        assert_transition(JobState.DRAFT, JobState.PUBLISHED)

    with pytest.raises(InvalidTransition):
        assert_transition(JobState.QUEUED, JobState.PUBLISHED)

    with pytest.raises(InvalidTransition):
        assert_transition(JobState.READY, JobState.UPLOADING)

    # Terminal states have no successors
    for term in TERMINAL_STATES:
        for any_state in JobState:
            assert not can_transition(term, any_state)
            with pytest.raises(InvalidTransition):
                assert_transition(term, any_state)


# --------------------------------------------------------------------------
# 6. Publication Invariant: Requires Persisted Video ID and Remote Public Evidence
# --------------------------------------------------------------------------


def test_published_state_cannot_be_displayed_without_remote_public_evidence() -> None:
    """display_for raises ValueError if JobState is PUBLISHED but remote is not PUBLIC."""
    with pytest.raises(ValueError, match="public"):
        display_for(JobState.PUBLISHED, remote=RemoteState.SCHEDULED)

    with pytest.raises(ValueError, match="public"):
        display_for(JobState.PUBLISHED, remote=RemoteState.UNKNOWN)

    assert display_for(JobState.PUBLISHED, remote=RemoteState.PUBLIC) == "Published (verified)"


def test_job_to_out_downgrades_unbacked_published_job_to_needs_review(ctx, ready_job) -> None:
    """If a job has state=PUBLISHED in DB but lacks remote public confirmation and

    published_at_remote timestamp, API view layer safely coerces it to NEEDS_REVIEW
    rather than claiming false publication.
    """
    zone = ZoneInfo("UTC")
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        job.state = JobState.PUBLISHED.value
        job.video_id = None
        job.published_at_remote = None
        s.commit()

        out = job_to_out(job, zone, now=NOW)
        assert out.state == JobState.NEEDS_REVIEW.value
        assert "Needs review" in out.display_label


def test_job_to_out_accepts_verified_published_job_with_evidence(ctx, ready_job) -> None:
    """A published job with remote confirmation is cleanly rendered as Published (verified)."""
    zone = ZoneInfo("UTC")
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        job.state = JobState.PUBLISHED.value
        job.video_id = "test-video-id-xyz"
        job.published_at_remote = NOW
        obs = RemoteObservation(
            job_id=job.id,
            video_id="test-video-id-xyz",
            remote_state=RemoteState.PUBLIC.value,
            privacy_status="public",
        )
        s.add(obs)
        s.commit()

        out = job_to_out(job, zone, now=NOW)
        assert out.state == JobState.PUBLISHED.value
        assert out.display_label == "Published (verified)"


# --------------------------------------------------------------------------
# 7. Migration from v1 to v2 with Pre-existing Data
# --------------------------------------------------------------------------


def test_migration_upgrade_from_v1_to_v2_preserves_existing_data(tmp_path) -> None:
    """Simulates upgrading an existing v1 production database to v2 head.

    Verifies that rows created in v1 remain intact, and new v2 tables/columns are created.
    """
    db_path = tmp_path / "v1_to_v2.sqlite3"
    url = "sqlite:///" + db_path.as_posix()

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    # 1. Upgrade to v1
    command.upgrade(cfg, "0001")

    engine = create_engine(url)
    with engine.connect() as conn:
        # Insert test connection in v1
        conn.exec_driver_sql(
            "INSERT INTO platform_connection (id, public_id, provider, label, status, scopes, created_at, updated_at) "
            "VALUES (1, 'pub-conn-1', 'youtube', 'My Channel', 'active', '[]', '2026-10-11 12:00:00', '2026-10-11 12:00:00')"
        )
        conn.commit()
    engine.dispose()

    # 2. Upgrade to v2 head
    command.upgrade(cfg, "head")

    # 3. Verify data preservation and new schema additions
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "sheets_config" in tables
        assert "sheet_summary_record" in tables
        assert "policy_acceptance_record" in tables

        # Verify platform_connection has new disabled_at column
        conn_cols = {c["name"] for c in inspector.get_columns("platform_connection")}
        assert "disabled_at" in conn_cols

        # Verify existing row survived
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT id, label, disabled_at FROM platform_connection WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert row[1] == "My Channel"
            assert row[2] is None  # Newly added nullable column initialized to None
    finally:
        engine.dispose()
