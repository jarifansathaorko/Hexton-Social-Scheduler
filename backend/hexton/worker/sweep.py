"""PublishConfirmationSweep — verifying that a scheduled video actually published.

The distinction this module exists to hold: a successful ``videos.insert`` means
YouTube accepted the file and a ``publishAt``. It does **not** mean the video is
public, and it does not mean YouTube finished processing it (``07`` §FEAS-03). So a
job whose publication time has passed is *awaiting verification* until a poll observes
``privacyStatus: public`` — and if the PC was asleep or the token had expired at that
moment, it stays awaiting verification rather than flipping to Published on hope.

Polling is paced rather than tick-driven. A fixed interval across a backlog would walk
straight through the daily quota, so the gap grows with the number of consecutive
"still not public" answers and stops entirely at a documented ceiling
(``08`` §7.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hexton import quota as quota_mod
from hexton.bootstrap import AppContext
from hexton.connectors.base import ConnectorError
from hexton.db import events as ev
from hexton.db.models import ScheduledJob
from hexton.domain.jobstate import FailureKind, JobState, PollResult
from hexton.worker.runner import (
    AuthorizationUnavailable,
    _record_observation,
    canonical_url,
    remote_state_of,
    resolve_access_token,
)

log = logging.getLogger(__name__)

#: States the sweep will look at. ``UPLOADED`` covers a job with no publication time
#: whose visibility still needs confirming.
SWEEPABLE = (JobState.SCHEDULED.value, JobState.UPLOADED.value)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


#: Poll pacing by how long ago publication was due.
#:
#: The first poll after the publication time is deliberately quick — an operator who
#: set a time wants to know promptly — and then the interval grows, because "not yet
#: public" after two minutes is not new information.
def poll_delay(elapsed: timedelta) -> timedelta:
    seconds = max(0.0, elapsed.total_seconds())
    if seconds < 120:
        return timedelta(seconds=30)
    if seconds < 600:
        return timedelta(minutes=2)
    if seconds < 3600:
        return timedelta(minutes=10)
    return timedelta(hours=1)


#: Hard ceiling on consecutive polls for one job. Past this the sweep stops spending
#: quota and leaves the job *awaiting verification*, which is the honest state.
MAX_POLLS_PER_JOB = 200

#: How far out a job is pushed once it stops polling.
#:
#: Far enough that no realistic worker uptime reaches it, so the job leaves the due set
#: permanently while staying a live row an operator can inspect and re-poll by hand.
POLL_CEILING_PARK = timedelta(days=365 * 100)


@dataclass(frozen=True)
class SweepResult:
    examined: int
    published: int
    pending: int
    blocked: int
    quota_deferred: int


class PublishConfirmationSweep:
    """Polls known video IDs and persists what it observed."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def run(self, *, now: datetime | None = None, limit: int = 25) -> SweepResult:
        now = now or datetime.now(UTC)
        examined = published = pending = blocked = deferred = 0

        with self.ctx.sessions() as session:
            due = list(
                session.execute(
                    select(ScheduledJob)
                    .where(
                        ScheduledJob.state.in_(SWEEPABLE),
                        ScheduledJob.video_id.is_not(None),
                        (ScheduledJob.next_poll_at.is_(None)) | (ScheduledJob.next_poll_at <= now),
                    )
                    .options(
                        selectinload(ScheduledJob.content),
                        selectinload(ScheduledJob.schedule),
                        selectinload(ScheduledJob.connection),
                        selectinload(ScheduledJob.observations),
                    )
                    .order_by(ScheduledJob.next_poll_at.asc().nullsfirst())
                    .limit(limit)
                ).scalars()
            )

            for job in due:
                examined += 1
                outcome = self._poll_one(session, job, now)
                if outcome == "published":
                    published += 1
                elif outcome == "blocked":
                    blocked += 1
                elif outcome == "quota_deferred":
                    deferred += 1
                else:
                    pending += 1
            session.commit()

        if examined:
            log.info(
                "Sweep: %d examined, %d confirmed published, %d pending, %d blocked, "
                "%d deferred on quota",
                examined,
                published,
                pending,
                blocked,
                deferred,
            )
        return SweepResult(examined, published, pending, blocked, deferred)

    # ------------------------------------------------------------------

    def _poll_one(self, session: Session, job: ScheduledJob, now: datetime) -> str:
        publish_at = job.schedule.publish_at_utc
        provider = job.connection.provider if job.connection else "youtube"

        if not quota_mod.can_afford_on(session, provider, quota_mod.COST_READ, now):
            # Defer rather than fail: the budget returns at the documented reset.
            job.next_poll_at = quota_mod.retry_at_reset(now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_VERIFICATION_PENDING,
                message="Publication verification is deferred until the next quota "
                "window; the video's state is unchanged.",
                payload={"next_poll_at": job.next_poll_at.isoformat()},
            )
            return "quota_deferred"

        access = self._token(session, job, now)
        if access is None:
            # The PC may have been asleep at publication time, or the consent screen's
            # seven-day window may have lapsed. Either way the video is still on
            # YouTube doing whatever it was going to do; only our ability to *see* it
            # is gone.
            job.poll_result = PollResult.UNKNOWN.value
            job.next_poll_at = now + timedelta(minutes=15)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_VERIFICATION_PENDING,
                message="Publication verification is pending: the stored credential is "
                "not available, so the remote status could not be read. Nothing was "
                "assumed about whether the video published.",
                payload={"publish_at": publish_at.isoformat() if publish_at else None},
            )
            return "pending"

        try:
            status = self.ctx.connector.get_video(access, job.video_id)
            quota_mod.add_units_on(session, provider, quota_mod.COST_READ, now)
        except ConnectorError as exc:
            if exc.kind is FailureKind.AUTHORIZATION:
                job.poll_result = PollResult.UNKNOWN.value
                job.next_poll_at = now + timedelta(minutes=15)
                ev.record_event(
                    session,
                    job_id=job.id,
                    kind=ev.KIND_VERIFICATION_PENDING,
                    message=f"Publication verification is pending: {exc}",
                )
                return "pending"
            job.poll_result = PollResult.UNKNOWN.value
            job.next_poll_at = now + poll_delay(_elapsed(publish_at, now))
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_OBSERVATION,
                message=f"The video status could not be read ({exc}); verification "
                f"remains pending and nothing was assumed.",
            )
            return "pending"

        if status is None:
            job.poll_result = PollResult.UNKNOWN.value
            job.next_poll_at = now + poll_delay(_elapsed(publish_at, now))
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_OBSERVATION,
                message=f"The recorded video ID {job.video_id} did not resolve. This is "
                f"not proof the video is gone — an expired token can look identical — "
                f"so the job is unchanged.",
                payload={"video_id": job.video_id},
            )
            return "pending"

        _record_observation(session, job, status, now)
        return self._apply(session, job, status, publish_at, now)

    def _apply(
        self,
        session: Session,
        job: ScheduledJob,
        status,
        publish_at: datetime | None,
        now: datetime,
    ) -> str:
        remote = remote_state_of(status, publish_at=publish_at, now=now)

        if status.privacy_status == "public":
            # The only path to PUBLISHED in the whole application: a persisted video ID
            # plus an observed remote ``public`` (``03`` §7.2, ``05`` §C-21).
            if job.video_id is None:  # pragma: no cover - guarded by the query
                return "pending"
            job.published_at_remote = status.publish_at or now
            job.poll_result = PollResult.CONFIRMED.value
            job.next_poll_at = None
            job.video_url = job.video_url or canonical_url(job.video_id)
            current = JobState(job.state)
            if current is not JobState.PUBLISHED:
                self._to(session, job, JobState.PUBLISHED, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_PUBLISHED,
                message="Confirmed published: the provider reports privacyStatus "
                "'public' for the persisted video ID.",
                payload={"video_id": job.video_id, "video_url": job.video_url},
            )
            return "published"

        # The provider says the video is not public, and that is the only claim being
        # recorded — whether the requested time has arrived or not. There is no
        # separate "not due yet" value because the state already says *Scheduled*;
        # what matters is that this is a fresh observation, not the initial
        # ``NEVER_POLLED``.
        job.poll_result = PollResult.NOT_YET.value
        self._pace(session, job, publish_at, now)

        if publish_at is not None and publish_at <= now:
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_VERIFICATION_PENDING,
                message=f"The requested publication time has passed and the provider "
                f"still reports {status.privacy_status!r}. The job stays *Scheduled — "
                f"publication awaiting verification* rather than being marked "
                f"published.",
                payload={"privacy_status": status.privacy_status, "remote_state": remote},
            )
        return "pending"

    def _pace(self, session: Session, job: ScheduledJob, publish_at, now: datetime) -> None:
        """Choose the next poll time, and stop at the documented ceiling."""
        polls = len([o for o in job.observations if not o.inconclusive])
        if polls >= MAX_POLLS_PER_JOB:
            # ``next_poll_at IS NULL`` means "never scheduled a poll", which the sweep's
            # due-query treats as due *now*. Leaving it NULL at the ceiling therefore
            # polled a capped job on every single tick — the ceiling existed to protect
            # the daily quota and instead guaranteed this job was the first thing
            # consumed by it, for ever. Parking it past any plausible run keeps the job
            # legible, keeps it out of the due set, and leaves it checkable by hand.
            job.next_poll_at = now + POLL_CEILING_PARK
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_VERIFICATION_PENDING,
                message=f"Stopped polling after {polls} observations to stay within "
                f"the quota. The job remains *publication awaiting verification* and "
                f"can be checked by hand; it is parked rather than left due, so it no "
                f"longer consumes quota on every sweep.",
                payload={"polls": polls, "parked_until": _iso(job.next_poll_at)},
            )
            return
        elapsed = _elapsed(publish_at, now)
        job.next_poll_at = now + poll_delay(elapsed)

    def _token(self, session: Session, job: ScheduledJob, now: datetime) -> str | None:
        """A usable access token for ``job``, or ``None`` when no usable grant exists.

        Delegates to :func:`~hexton.worker.runner.resolve_access_token` (F-22). This
        used to be a second, shorter implementation of the same rule: it returned
        whatever access-token string was stored without ever consulting
        ``token_expires_at``, so a verification that outlived the one-hour token kept
        polling with a dead credential and could never recover on its own; and it
        swallowed a refused refresh instead of stamping ``last_token_check_*`` and
        starting ``DF-21``'s 30-calendar-day deletion clock — the one path that
        actually *discovers* a grant has died never recorded that it had.
        """
        if job.connection is None:
            return None
        try:
            return resolve_access_token(
                self.ctx.credentials,
                self.ctx.connector,
                session,
                job.connection,
                now,
            )
        except AuthorizationUnavailable:
            return None

    def _to(self, session: Session, job: ScheduledJob, target: JobState, now: datetime) -> None:
        from hexton.domain.jobstate import can_transition

        current = JobState(job.state)
        if current is target:
            return
        if not can_transition(current, target):
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_NEEDS_REVIEW,
                message=f"Illegal transition {current.value} -> {target.value} refused.",
            )
            return
        job.state = target.value
        job.updated_at = now
        session.flush()


def _elapsed(publish_at: datetime | None, now: datetime) -> timedelta:
    if publish_at is None:
        return timedelta(0)
    return max(timedelta(0), now - publish_at)
