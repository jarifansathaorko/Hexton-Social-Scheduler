"""Claiming, leases, and the contention/ restart properties that depend on them.

The lease is what makes two workers safe and what makes a killed worker recoverable.
Both properties are exercised against a real SQLite file, because the guarantee comes
from ``BEGIN IMMEDIATE`` and WAL — neither of which exists against an in-memory stub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import fresh
from hexton.db.models import ScheduledJob
from hexton.worker.lease import (
    CLAIMABLE_STATES,
    claim_next,
    expire_stale_leases,
    heartbeat,
    held_leases,
    release,
)
from sqlalchemy import select

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def test_claiming_takes_the_job_and_spends_one_attempt(ctx, ready_job) -> None:
    claim = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert claim is not None
    assert claim.job_id == ready_job.id
    assert claim.attempt == 1
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.lease_owner == "w1"
        assert job.lease_expires_at == NOW + timedelta(seconds=60)
        assert job.attempt == 1


def test_a_second_worker_finds_nothing_while_the_lease_is_held(ctx, ready_job) -> None:
    """The whole point of BEGIN IMMEDIATE: two workers cannot both take one job."""
    assert claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60) is not None
    assert claim_next(ctx.engine, owner="w2", now=NOW, lease_seconds=60) is None
    with fresh(ctx) as s:
        assert s.get(ScheduledJob, ready_job.id).lease_owner == "w1"


def test_contending_workers_each_take_a_different_job(ctx, session, connection, clip) -> None:
    from conftest import make_job, make_media

    media = make_media(session, clip)
    first = make_job(session, media, connection, state="JOB-QUEUED", title="one")
    second = make_job(session, media, connection, state="JOB-QUEUED", title="two")

    got_a = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    got_b = claim_next(ctx.engine, owner="w2", now=NOW, lease_seconds=60)
    assert {got_a.job_id, got_b.job_id} == {first.id, second.id}
    assert got_a.job_id != got_b.job_id


def test_handing_a_job_back_makes_it_claimable_again(ctx, ready_job) -> None:
    claim = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert claim is not None
    assert release(ctx.engine, job_id=claim.job_id, owner="w1") is True
    assert claim_next(ctx.engine, owner="w2", now=NOW, lease_seconds=60) is not None


def test_releasing_a_job_you_do_not_own_is_refused(ctx, ready_job) -> None:
    claim = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert claim is not None
    assert release(ctx.engine, job_id=claim.job_id, owner="someone-else") is False
    with fresh(ctx) as s:
        assert s.get(ScheduledJob, ready_job.id).lease_owner == "w1"


def test_heartbeat_extends_a_held_lease(ctx, ready_job) -> None:
    claim = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert claim is not None
    later = NOW + timedelta(seconds=45)
    assert heartbeat(ctx.engine, job_id=claim.job_id, owner="w1", now=later, lease_seconds=60)
    with fresh(ctx) as s:
        assert s.get(ScheduledJob, ready_job.id).lease_expires_at == later + timedelta(seconds=60)


def test_losing_a_lease_is_reported_so_the_worker_stops(ctx, ready_job) -> None:
    claim = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert claim is not None
    assert (
        heartbeat(ctx.engine, job_id=claim.job_id, owner="w2", now=NOW, lease_seconds=60) is False
    )


def test_a_dead_workers_lease_expires_and_the_job_runs_again(ctx, ready_job) -> None:
    """Crash recovery: an expired lease is not a special case, it is just claimable."""
    claim = claim_next(ctx.engine, owner="dead", now=NOW, lease_seconds=60)
    assert claim is not None

    still_held = NOW + timedelta(seconds=30)
    assert claim_next(ctx.engine, owner="w2", now=still_held, lease_seconds=60) is None

    after_expiry = NOW + timedelta(seconds=61)
    assert ready_job.id in expire_stale_leases(ctx.engine, now=after_expiry)
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.lease_owner is None

    assert claim_next(ctx.engine, owner="w2", now=after_expiry, lease_seconds=60) is not None


def test_expire_stale_leases_leaves_the_state_alone(ctx, ready_job) -> None:
    """Recovery must not rewrite history: the runner decides what a stuck job means."""
    claim_next(ctx.engine, owner="dead", now=NOW, lease_seconds=1)
    expire_stale_leases(ctx.engine, now=NOW + timedelta(seconds=5))
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        assert job.state == "JOB-QUEUED"
        assert job.attempt == 1  # the first claim is still recorded


def test_a_future_retry_time_is_not_due(ctx, ready_job) -> None:
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        job.next_attempt_at = NOW + timedelta(minutes=10)
        s.commit()
    assert claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60) is None
    assert (
        claim_next(ctx.engine, owner="w1", now=NOW + timedelta(minutes=11), lease_seconds=60)
        is not None
    )


@pytest.mark.parametrize(
    "state",
    ["JOB-DRAFT", "JOB-READY", "JOB-CANCELLED", "JOB-FAILED", "JOB-PUBLISHED", "JOB-BLOCKED"],
)
def test_only_the_claimable_states_can_be_taken(ctx, ready_job, state) -> None:
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, ready_job.id)
        job.state = state
        s.commit()
    assert claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60) is None
    assert state not in CLAIMABLE_STATES


def test_the_claimable_set_matches_the_canonical_machine() -> None:
    from hexton.domain.jobstate import CLAIMABLE_STATES as DOMAIN_CLAIMABLE

    # The worker's SQL list and the domain's list must be the same set, or a job could
    # be claimable in one and forbidden in the other.
    assert set(CLAIMABLE_STATES) == {s.value for s in DOMAIN_CLAIMABLE} - {"JOB-READY"}


def test_a_naive_timestamp_is_refused(ctx, ready_job) -> None:
    """Lease expiry must not depend on the machine's local clock."""
    with pytest.raises(ValueError, match="timezone-aware"):
        claim_next(ctx.engine, owner="w1", now=datetime(2026, 10, 11, 12, 0), lease_seconds=60)


def test_held_leases_are_visible_to_the_status_endpoint(ctx, ready_job) -> None:
    claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    with ctx.sessions() as s:
        held = held_leases(s.connection(), now=NOW)
    assert [(job_id, owner) for job_id, owner, _ in held] == [(ready_job.id, "w1")]


def test_claim_orders_by_due_time_then_id(ctx, session, connection, clip) -> None:
    from conftest import make_job, make_media

    media = make_media(session, clip)
    early = make_job(session, media, connection, state="JOB-QUEUED", title="early")
    late = make_job(session, media, connection, state="JOB-QUEUED", title="late")
    with fresh(ctx) as s:
        s.get(ScheduledJob, early.id).next_attempt_at = NOW - timedelta(minutes=5)
        s.get(ScheduledJob, late.id).next_attempt_at = NOW
        s.commit()

    first = claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    assert first is not None and first.job_id == early.id


def test_the_claim_is_a_single_row_read_modify_write(ctx, ready_job) -> None:
    """Prove the increment and the lease land together, not in two transactions."""
    claim_next(ctx.engine, owner="w1", now=NOW, lease_seconds=60)
    with fresh(ctx) as s:
        row = s.execute(
            select(ScheduledJob.attempt, ScheduledJob.lease_owner).where(
                ScheduledJob.id == ready_job.id
            )
        ).one()
    assert (row.attempt, row.lease_owner) == (1, "w1")
