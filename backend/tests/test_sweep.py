"""Publication confirmation — the only path from Scheduled to Published.

``03`` §7.2 and ``05`` §C-21: a persisted video ID **and** an observed remote
``privacyStatus: public``. Nothing else in the application may set PUBLISHED, and a
successful ``videos.insert`` is emphatically not that evidence. These tests cover the
"did it actually publish?" half of Phase 2 §8, including the three ways the sweep can
honestly say *I don't know* instead of guessing.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import BASE, FUTURE, PAST, events_for, fresh, reload
from hexton import quota
from hexton.db import events as ev
from hexton.db.models import RemoteObservation, ScheduledJob
from hexton.domain.jobstate import JobState, PollResult
from hexton.worker.runner import JobRunner
from hexton.worker.sweep import MAX_POLLS_PER_JOB, PublishConfirmationSweep, poll_delay


def uploaded(ctx, new_job, *, publish_at=FUTURE) -> tuple[int, str]:
    """A job that has genuinely uploaded and is waiting for its publication time."""
    job = new_job(publish_at=publish_at)
    JobRunner(ctx, owner="uploader").run_once(now=BASE)
    row = reload(ctx, job.id)
    assert row.state == JobState.SCHEDULED.value
    assert row.video_id
    return job.id, row.video_id


def make_due(ctx, job_id: int, *, at=BASE - timedelta(hours=1)) -> None:
    """Move the publication time and the next poll into the past."""
    with fresh(ctx) as s:
        job = s.get(ScheduledJob, job_id)
        job.schedule.publish_at_utc = at
        job.schedule.report_date = at
        job.next_poll_at = BASE - timedelta(minutes=5)
        s.commit()


# ----------------------------------------------------------------------#
# Still private
# ----------------------------------------------------------------------#


def test_a_passed_publication_time_that_is_still_private_stays_awaiting(ctx, new_job) -> None:
    """The heart of it: the time passing is not evidence that anything published."""
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.examined == 1
    assert result.published == 0
    assert result.pending == 1

    row = reload(ctx, job_id)
    assert row.state == JobState.SCHEDULED.value  # NOT Published
    assert row.poll_result == PollResult.NOT_YET.value
    assert row.published_at_remote is None
    assert row.video_id == video_id  # never discarded
    messages = [msg for kind, msg in events_for(ctx, job_id)]
    assert any("awaiting verification" in m for m in messages)
    assert ev.KIND_PUBLISHED not in [k for k, _ in events_for(ctx, job_id)]


def test_awaiting_verification_is_reflected_in_the_ui_label(ctx, new_job) -> None:
    from hexton.domain.jobstate import RemoteState, display_for

    job_id, _ = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    PublishConfirmationSweep(ctx).run(now=BASE)

    row = reload(ctx, job_id)
    label = display_for(
        JobState(row.state),
        remote=RemoteState.SCHEDULED,
        poll_result=PollResult(row.poll_result),
        publish_at_passed=True,
    )
    assert "verification" in label.lower()


def test_a_video_that_has_not_published_yet_is_not_rushed(ctx, new_job) -> None:
    job_id, _ = uploaded(ctx, new_job)  # publish_at is still FUTURE
    with fresh(ctx) as s:
        s.get(ScheduledJob, job_id).next_poll_at = BASE - timedelta(minutes=5)
        s.commit()

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.pending == 1 and result.published == 0
    row = reload(ctx, job_id)
    assert row.state == JobState.SCHEDULED.value
    assert row.published_at_remote is None


# ----------------------------------------------------------------------#
# Confirmed published
# ----------------------------------------------------------------------#


def test_an_observed_public_status_is_the_only_route_to_published(ctx, new_job) -> None:
    """§8 item 14 — a persisted video ID *plus* observed remote evidence."""
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    ctx.connector.set_video_public(video_id)

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.published == 1
    assert result.pending == 0

    row = reload(ctx, job_id)
    assert row.state == JobState.PUBLISHED.value
    assert row.poll_result == PollResult.CONFIRMED.value
    assert row.published_at_remote is not None
    assert row.video_id == video_id
    assert row.video_url.endswith(video_id)
    assert row.next_poll_at is None  # nothing left to poll
    kinds = [k for k, _ in events_for(ctx, job_id)]
    assert ev.KIND_PUBLISHED in kinds


def test_confirming_publishing_is_idempotent(ctx, new_job) -> None:
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    ctx.connector.set_video_public(video_id)

    PublishConfirmationSweep(ctx).run(now=BASE)
    second = PublishConfirmationSweep(ctx).run(now=BASE)

    # PUBLISHED is terminal, so it drops out of SWEEPABLE entirely.
    assert second.examined == 0
    assert reload(ctx, job_id).state == JobState.PUBLISHED.value


def test_confirmation_does_not_rely_on_the_activity_feed(ctx, new_job) -> None:
    """DF-08: a feed or playlist match is evidence only, never proof of publication."""
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    ctx.connector.set_video_public(video_id)
    # The provider's own status lookup is what decides — simulate the feed being
    # unaware by removing the recent-uploads listing entirely.
    ctx.connector.state.recent_upload_ids = []

    PublishConfirmationSweep(ctx).run(now=BASE)

    assert reload(ctx, job_id).state == JobState.PUBLISHED.value


# ----------------------------------------------------------------------#
# Honest "I don't know"
# ----------------------------------------------------------------------#


def test_without_a_credential_nothing_is_assumed(ctx, new_job) -> None:
    """The PC may have slept, or the seven-day consent window may have lapsed."""
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    with fresh(ctx) as s:
        conn = s.get(ScheduledJob, job_id).connection
        ctx.credentials.delete(f"conn:{conn.public_id}:access")
        ctx.credentials.delete(f"conn:{conn.public_id}:refresh")

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.pending == 1 and result.published == 0
    row = reload(ctx, job_id)
    assert row.state == JobState.SCHEDULED.value  # not Published, not Failed
    assert row.poll_result == PollResult.UNKNOWN.value
    assert row.published_at_remote is None
    assert row.video_id == video_id
    assert any("Nothing was assumed" in msg for _k, msg in events_for(ctx, job_id))
    assert ctx.connector.call_count("get_video") == 0


def test_a_video_id_that_does_not_resolve_is_not_read_as_deletion(ctx, new_job) -> None:
    job_id, video_id = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    del ctx.connector.state.videos[video_id]

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.pending == 1
    row = reload(ctx, job_id)
    assert row.state == JobState.SCHEDULED.value
    assert row.poll_result == PollResult.UNKNOWN.value
    assert any("not proof the video is gone" in msg for _k, msg in events_for(ctx, job_id))


def test_an_empty_quota_window_defers_instead_of_failing(ctx, new_job) -> None:
    job_id, _ = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    quota.add_units(ctx.engine, "youtube", quota.DAILY_UNITS, BASE)

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.quota_deferred == 1
    row = reload(ctx, job_id)
    assert row.state == JobState.SCHEDULED.value
    assert row.next_poll_at is not None
    assert row.next_poll_at > quota.next_reset(BASE)  # the next window, not later today
    assert ctx.connector.call_count("get_video") == 0
    assert any("deferred until the next quota" in msg for _k, msg in events_for(ctx, job_id))


def test_a_job_without_a_video_id_is_never_polled(ctx, new_job) -> None:
    """Nothing to confirm means no confirmation is attempted, let alone recorded."""
    new_job(state="JOB-SCHEDULED", publish_at=PAST)

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.examined == 0
    assert ctx.connector.call_count("get_video") == 0


def test_an_uncertain_job_is_not_polled_over_by_the_sweep(ctx, new_job) -> None:
    """NEEDS_REVIEW is not in SWEEPABLE: a human resolves it, not a poll."""
    job = new_job(state="JOB-NEEDS_REVIEW", publish_at=PAST)
    with fresh(ctx) as s:
        s.get(ScheduledJob, job.id).video_id = "vid-uncertain"
        s.commit()

    result = PublishConfirmationSweep(ctx).run(now=BASE)

    assert result.examined == 0
    assert reload(ctx, job.id).state == JobState.NEEDS_REVIEW.value
    assert ev.KIND_PUBLISHED not in [k for k, _ in events_for(ctx, job.id)]


# ----------------------------------------------------------------------#
# Evidence and pacing
# ----------------------------------------------------------------------#


def test_every_poll_appends_an_observation(ctx, new_job) -> None:
    job_id, _ = uploaded(ctx, new_job)
    make_due(ctx, job_id)

    PublishConfirmationSweep(ctx).run(now=BASE)
    with fresh(ctx) as s:
        first = len(s.get(ScheduledJob, job_id).observations)

    with fresh(ctx) as s:
        s.get(ScheduledJob, job_id).next_poll_at = BASE
        s.commit()
    PublishConfirmationSweep(ctx).run(now=BASE)
    with fresh(ctx) as s:
        second = len(s.get(ScheduledJob, job_id).observations)

    assert second > first  # appended, never overwritten


def test_poll_delay_grows_with_how_long_ago_publication_was_due() -> None:
    assert poll_delay(timedelta(seconds=0)) == timedelta(seconds=30)
    assert poll_delay(timedelta(seconds=119)) == timedelta(seconds=30)
    assert poll_delay(timedelta(minutes=3)) == timedelta(minutes=2)
    assert poll_delay(timedelta(minutes=11)) == timedelta(minutes=10)
    assert poll_delay(timedelta(hours=2)) == timedelta(hours=1)


def test_the_delay_never_regresses_as_publication_recedes(ctx, new_job) -> None:
    delays = [poll_delay(timedelta(seconds=s)) for s in (0, 60, 300, 900, 7200)]
    assert delays == sorted(delays)


def test_polling_stops_at_the_documented_ceiling(ctx, new_job) -> None:
    """Past the ceiling the sweep stops spending quota and stays honest instead."""
    job_id, _ = uploaded(ctx, new_job)
    make_due(ctx, job_id)
    with fresh(ctx) as s:
        for i in range(MAX_POLLS_PER_JOB):
            s.add(
                RemoteObservation(
                    job_id=job_id,
                    observed_at=BASE - timedelta(minutes=i),
                    remote_state="scheduled",
                    privacy_status="private",
                    inconclusive=False,
                )
            )
        s.commit()

    PublishConfirmationSweep(ctx).run(now=BASE)

    row = reload(ctx, job_id)
    assert row.next_poll_at is None  # stopped, rather than polling for ever
    assert row.state == JobState.SCHEDULED.value
    assert any("can be checked by hand" in msg for _k, msg in events_for(ctx, job_id))
