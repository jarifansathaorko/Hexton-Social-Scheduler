"""Job execution: preflight, upload, classification, and recovery.

One job, one pass, one recorded decision. The runner never decides anything twice and
never acts on a remote outcome it cannot prove, which is what the whole duplicate-
prevention design exists to guarantee (``07`` §FEAS-04, ``08`` §7.2).

The shape of a pass:

1. **Claim** — :mod:`hexton.worker.lease` takes an atomic lease.
2. **Preflight** — the clip's digest, the metadata, the connection, the channel and
   the publication time are re-checked *after* the claim, because any of them can
   have changed since queueing and the queue must not ship a surprise.
3. **Reconcile if uncertain** — a job whose last attempt may already have reached
   YouTube is settled before anything new is sent. A second ``videos.insert`` is never
   issued while that is in doubt.
4. **Transfer** — open a session if there is none, then resume from whatever offset
   the provider actually reports rather than from what we last recorded.
5. **Classify** — a failure is either retryable-with-backoff, blocked-awaiting-a-human,
   or terminal. The classification decides the state, not the exception type.

No provider call is made outside this module and :mod:`hexton.connectors`.
"""

from __future__ import annotations

import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hexton import quota as quota_mod
from hexton.bootstrap import AppContext
from hexton.connectors.base import (
    ConnectorError,
    PlatformConnector,
    ProbeResult,
    VideoStatus,
)
from hexton.credentials.store import CredentialStore
from hexton.db import events as ev
from hexton.db.models import (
    PlatformConnection,
    RemoteObservation,
    ScheduledJob,
    UploadSession,
)
from hexton.domain.jobstate import (
    FailureKind,
    JobState,
    MentionState,
    PollResult,
    RemoteState,
    can_transition,
    video_has_been_public,
)
from hexton.domain.mention import assess as assess_mention
from hexton.domain.scheduling import check_publish_time, has_missed_window, next_retry_at
from hexton.domain.validation import apply_transforms, build_insert_body
from hexton.media import validate_file, verify_unchanged
from hexton.reconciliation.ladder import ReconcileResult, StepOutcome, reconcile
from hexton.worker.lease import claim_next, expire_stale_leases, heartbeat, release

log = logging.getLogger(__name__)

#: Provider endpoint for a new resumable session. Held here rather than in the
#: connector so the insert body and the endpoint cannot drift apart.
RESUMABLE_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"

#: Backoff floor. A job that failed with a transient error is not retried on the next
#: tick: the operator's machine and Google's edge both need a moment.
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600


class AuthorizationUnavailable(RuntimeError):
    """No usable credential for the connection. Blocks the job; never retries itself."""


class LeaseLost(RuntimeError):
    """Another worker owns this job now, so we stopped touching it mid-transfer.

    Raised from inside ``on_progress``, which the connector calls between chunks. It
    unwinds the transfer rather than letting a second process continue inside a
    resumable session that someone else is also writing to. No state is recorded here:
    the job is not ours to record against, and the new owner is responsible for
    reconciling what it finds.
    """


@dataclass(frozen=True)
class RunResult:
    job_id: int | None
    claimed: bool
    state: str | None
    outcome: str
    detail: str = ""


def record_unrefreshable(session: Session, connection: PlatformConnection, now: datetime) -> None:
    """Start the ``DF-21`` deletion clock when a grant stops refreshing.

    Two things used to be lost here. ``schedule_for_unrefreshable_token`` existed,
    was tested, and was never called from anywhere — so the 30-calendar-day
    deadline this policy requires was never written, and whether Authorized Data
    was deleted in time depended on someone happening to notice the connection had
    gone stale. And ``last_token_check_ok`` was only ever set to ``True``, so the
    compliance diagnostic's one remote-failure signal could never fire.

    Recorded in one function rather than on the runner, because the publication sweep
    discovers dead grants too and must write exactly the same record: two
    implementations of this clock is how one of them quietly stops running (F-22).
    """
    from hexton.compliance.retention import schedule_for_unrefreshable_token
    from hexton.db.models import DataRetention

    connection.last_token_check_at = now
    connection.last_token_check_ok = False

    already = session.execute(
        select(DataRetention).where(
            DataRetention.subject_type == "connection",
            DataRetention.subject_id == connection.public_id,
            DataRetention.purged_at.is_(None),
        )
    ).first()
    if already is not None:
        # One clock per connection. Restarting it on every attempt would push the
        # deadline out each time a job was retried, which is how a deletion
        # obligation quietly becomes permanent.
        return
    schedule_for_unrefreshable_token(session, connection, now=now)


def resolve_access_token(
    store: CredentialStore,
    connector: PlatformConnector,
    session: Session,
    connection: PlatformConnection,
    now: datetime,
) -> str:
    """Return a usable access token, refreshing when the stored one is stale.

    Module-level rather than a ``JobRunner`` method because both the upload path and
    the publication-confirmation sweep need a token, and they used to each have their
    own: the sweep returned whatever string was stored without ever reading
    ``token_expires_at``, never refreshed, never stamped ``last_token_check_*``, never
    charged for the call, and swallowed a refused refresh instead of starting ``DF-21``
    (F-22). One implementation, one set of rules, one record.

    Raises :class:`AuthorizationUnavailable` when no usable credential exists.
    """
    key = f"conn:{connection.public_id}"
    access = store.read(f"{key}:access")
    refresh = store.read(f"{key}:refresh")

    fresh = (
        connection.token_expires_at is not None
        and connection.token_expires_at > now + timedelta(seconds=60)
    )
    if access and fresh:
        return access

    if not refresh:
        record_unrefreshable(session, connection, now)
        raise AuthorizationUnavailable(
            "No refresh token is stored for this connection. Under a Google Cloud "
            "consent screen in *Testing* status, refresh tokens expire seven days "
            "after consent (DF-17), so this is expected to happen; reconnect the "
            "channel in Settings to continue."
        )

    try:
        tokens = connector.refresh(refresh)
    except ConnectorError as exc:
        record_unrefreshable(session, connection, now)
        raise AuthorizationUnavailable(f"The stored credential is no longer valid: {exc}") from exc

    store.write(f"{key}:access", tokens.access_token)
    if tokens.refresh_token and tokens.refresh_token != refresh:
        store.write(f"{key}:refresh", tokens.refresh_token)
    if tokens.expires_in:
        connection.token_expires_at = now + timedelta(seconds=tokens.expires_in)
    connection.last_token_check_at = now
    connection.last_token_check_ok = True
    if connection.status == "expired":
        connection.status = "active"
    session.flush()
    quota_mod.add_units_on(session, connection.provider, quota_mod.COST_READ, now)
    return tokens.access_token


class JobRunner:
    """Executes one claimed job at a time, on behalf of one worker identity."""

    def __init__(self, ctx: AppContext, *, owner: str | None = None) -> None:
        self.ctx = ctx
        self.owner = owner or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reclaim_orphans(self, *, now: datetime) -> list[int]:
        """Clear leases held by workers that are no longer running.

        Called at startup. A job found here was interrupted — the process was killed,
        the machine slept, or Windows restarted. Its *state* is deliberately left
        alone: whatever step it was in is still the truth, and the next pass will
        probe the session rather than assume.
        """
        stale = expire_stale_leases(self.ctx.engine, now=now)
        if stale:
            log.warning("Reclaimed %d stale lease(s): %s", len(stale), stale)
        return stale

    def run_once(self, *, now: datetime | None = None) -> RunResult | None:
        """Claim and execute exactly one job. Returns ``None`` when nothing is due."""
        now = now or datetime.now(UTC)
        claim = claim_next(
            self.ctx.engine,
            owner=self.owner,
            now=now,
            lease_seconds=self.ctx.settings.lease_seconds,
        )
        if claim is None:
            return None
        try:
            state = self.execute(claim.job_id, now=now)
        except BaseException:
            # A crash inside the job must not strand the lease. Releasing here means
            # the next pass picks it up promptly rather than waiting out the lease.
            release(self.ctx.engine, job_id=claim.job_id, owner=self.owner)
            raise
        return RunResult(job_id=claim.job_id, claimed=True, state=state, outcome="executed")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, job_id: int, *, now: datetime | None = None) -> str:
        """Run one job to completion of *this attempt*. Returns its resulting state."""
        now = now or datetime.now(UTC)
        factory = self.ctx.sessions

        with factory() as session:
            job = _load_job(session, job_id)
            if job is None:
                return "missing"
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_CLAIMED,
                message=f"Worker {self.owner} claimed the job (attempt {job.attempt}).",
                payload={"owner": self.owner, "attempt": job.attempt},
            )
            session.commit()

        state = self._run_steps(job_id, now=now)
        release(self.ctx.engine, job_id=job_id, owner=self.owner)
        return state

    def _run_steps(self, job_id: int, *, now: datetime) -> str:
        with self.ctx.sessions() as session:
            job = _load_job(session, job_id)
            if job is None:
                return "missing"
            if job.state in {JobState.CANCELLED.value, JobState.PUBLISHED.value}:
                return job.state

            # --- 1. preflight ------------------------------------------------
            blocked = self._preflight(session, job, now)
            if blocked:
                session.commit()
                return job.state

            # --- 2. credential ------------------------------------------------
            assert job.connection is not None
            try:
                access = self._access_token(session, job.connection, now)
            except AuthorizationUnavailable as exc:
                self._block(session, job, FailureKind.AUTHORIZATION, str(exc), now)
                session.commit()
                return job.state

            # --- 3. reconcile before sending anything new ----------------------
            if job.insert_may_have_succeeded:
                result = self._reconcile(session, job, access, now)
                session.commit()
                if result.outcome is not StepOutcome.RESUME:
                    return job.state
            elif job.upload_sessions and any(
                s.session_uri and s.phase != "completed" for s in job.upload_sessions
            ):
                # A session exists but the outcome was never recorded — the process
                # died mid-transfer. Probe before sending a byte, so a crash cannot
                # turn into a duplicate.
                result = self._reconcile(session, job, access, now)
                session.commit()
                if result.outcome is not StepOutcome.RESUME:
                    return job.state

            # --- 4. transfer ---------------------------------------------------
            return self._transfer(session, job, access, now)

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def _preflight(self, session: Session, job: ScheduledJob, now: datetime) -> bool:
        """Re-check everything that could have changed since queueing.

        Returns ``True`` when the job must stop. Every stop records an event, because
        a job that silently disappears into a state the operator did not cause is the
        worst possible failure mode for a scheduler.
        """
        conn = job.connection
        if conn is None or conn.status != "active":
            self._block(
                session,
                job,
                FailureKind.AUTHORIZATION,
                "No active platform connection is selected for this job. Reconnect the "
                "channel in Settings before queueing it again.",
                now,
            )
            return True

        if job.channel is None:
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                "No verified channel identity is selected for this job. Hexton will "
                "not guess which channel to publish to.",
                now,
            )
            return True

        if job.channel.connection_id != conn.id:
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                "The selected channel does not belong to the selected connection. This "
                "is a channel-mismatch and is refused rather than published to the "
                "wrong channel.",
                now,
            )
            return True

        # --- media -----------------------------------------------------------
        report = verify_unchanged(job.media.path, job.media.sha256)
        unchanged, problem = report
        if not unchanged:
            assert problem is not None
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                problem.message,
                now,
                kind_name=ev.KIND_PREFLIGHT_FAILED,
                code=problem.code,
            )
            return True
        live = validate_file(job.media.path)
        if not live.ok:
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                "; ".join(i.message for i in live.blocking_issues),
                now,
                kind_name=ev.KIND_PREFLIGHT_FAILED,
            )
            return True
        job.media.verified_at = now
        session.flush()

        # --- metadata --------------------------------------------------------
        from hexton.domain.validation import validate_metadata

        verdict = validate_metadata(
            title=job.content.title,
            description=job.content.description,
            tags=list(job.content.tags),
            category_id=job.content.category_id,
            privacy=job.content.privacy,
        )
        if not verdict.ok:
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                "; ".join(f"{i.code}: {i.message}" for i in verdict.errors),
                now,
                kind_name=ev.KIND_PREFLIGHT_FAILED,
            )
            return True

        # --- publication time ------------------------------------------------
        check = check_publish_time(
            job.schedule.publish_at_utc,
            now=now,
            never_published=not video_has_been_public(
                JobState(job.state),
                job.published_at_remote,
                job.observations[-1].privacy_status if job.observations else None,
            ),
            min_lead_minutes=job.schedule.min_lead_minutes,
            warning_lead_minutes=self.ctx.settings.warning_lead_minutes,
        )
        if check.verdict.value == "missed":
            self._transition(session, job, JobState.MISSED_WINDOW, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_MISSED,
                message="The publication time had already passed when the job was "
                "claimed; nothing was sent, because a past publishAt publishes "
                "immediately.",
                payload={"publish_at": _iso(job.schedule.publish_at_utc)},
            )
            return True
        if not check.ok:
            self._block(
                session,
                job,
                FailureKind.VALIDATION,
                "; ".join(i.message for i in check.blocking),
                now,
                kind_name=ev.KIND_PREFLIGHT_FAILED,
            )
            return True

        for issue in check.issues:
            if not issue.blocking:
                ev.record_event(
                    session,
                    job_id=job.id,
                    kind=ev.KIND_PREFLIGHT,
                    message=issue.message,
                    payload={"code": issue.code},
                )

        # --- mention ----------------------------------------------------------
        assessment = assess_mention(
            mention_requested=job.content.mention_requested,
            text=f"{job.content.title}\n{job.content.description}",
            operator_confirmed_in_studio=job.content.mention_confirmed_at is not None,
        )
        if assessment.state.value != job.content.mention_state:
            job.content.mention_state = assessment.state.value
            job.content.mention_reason = assessment.reason
            job.content.mention_handles = list(assessment.handles_found)
            session.flush()
        if assessment.state is MentionState.MANUAL_REQUIRED:
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_PREFLIGHT,
                message=assessment.reason,
                payload={"handles": list(assessment.handles_found)},
            )

        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_PREFLIGHT,
            message="Preflight passed: clip digest, metadata, connection, channel and "
            "publication time all re-checked after claiming.",
        )
        return False

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def _access_token(self, session: Session, connection: PlatformConnection, now: datetime) -> str:
        """Return a usable access token, refreshing when the stored one is stale.

        A thin delegate. The rules live in :func:`resolve_access_token`, which the
        publication-confirmation sweep shares, so the two paths cannot drift apart
        again (F-22).
        """
        return resolve_access_token(
            self.ctx.credentials, self.ctx.connector, session, connection, now
        )

    def _record_unrefreshable(
        self, session: Session, connection: PlatformConnection, now: datetime
    ) -> None:
        """Start the ``DF-21`` deletion clock when a grant stops refreshing.

        Delegates to :func:`record_unrefreshable` for the same reason as
        :meth:`_access_token`: the sweep writes this clock too.
        """
        record_unrefreshable(session, connection, now)

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _reconcile(
        self,
        session: Session,
        job: ScheduledJob,
        access: str,
        now: datetime,
    ) -> ReconcileResult:
        """Settle an uncertain outcome before anything new is sent."""
        assert job.connection is not None
        upload = _current_session(job)
        probe: ProbeResult | None = None
        probe_error: str | None = None

        if upload is not None and upload.session_uri:
            try:
                probe = self.ctx.connector.probe_upload(
                    access, upload.session_uri, upload.total_bytes
                )
                quota_mod.add_units_on(session, job.connection.provider, quota_mod.COST_READ, now)
            except ConnectorError as exc:
                probe_error = str(exc)

        known: VideoStatus | None = None
        known_error: str | None = None
        if job.video_id:
            try:
                known = self.ctx.connector.get_video(access, job.video_id)
                quota_mod.add_units_on(session, job.connection.provider, quota_mod.COST_READ, now)
                if known is not None:
                    _record_observation(session, job, known, now)
            except ConnectorError as exc:
                known_error = str(exc)

        candidates: tuple[VideoStatus, ...] = ()
        result = reconcile(
            has_session_uri=bool(upload and upload.session_uri),
            has_video_id=bool(job.video_id),
            probe=probe,
            probe_error=probe_error,
            known_video=known,
            known_video_error=known_error,
            candidates=candidates,
        )

        # R3: a recent-upload match is evidence for a human, never a replacement ID.
        if result.outcome is StepOutcome.UNRESOLVED and job.channel is not None:
            try:
                ids = self.ctx.connector.list_recent_upload_ids(access, job.channel.channel_id)
                quota_mod.add_units_on(session, job.connection.provider, quota_mod.COST_READ, now)
            except ConnectorError:
                ids = []
            matched: list[str] = []
            for vid in ids:
                if vid == job.video_id:
                    continue
                try:
                    status = self.ctx.connector.get_video(access, vid)
                except ConnectorError:
                    continue
                if status is None:
                    continue
                from hexton.reconciliation.ladder import classify_candidate

                if classify_candidate(
                    status,
                    title=job.content.title,
                    privacy=job.content.privacy,
                    publish_at=job.schedule.publish_at_utc,
                    started_at=now,
                ):
                    matched.append(vid)
            if matched:
                result = ReconcileResult(
                    step="R3",
                    outcome=StepOutcome.NEEDS_OPERATOR,
                    evidence=result.evidence,
                    safe_to_retry_insert=False,
                    candidates=tuple(matched),
                    reason=(
                        f"{len(matched)} recent upload(s) match this schedule's title, "
                        f"visibility and publication time. A match is evidence that a "
                        f"video exists, not proof that *this* request created it, so "
                        f"one must be confirmed by hand."
                    ),
                )

        self._apply_reconcile(session, job, result, upload, now)
        return result

    def _apply_reconcile(
        self,
        session: Session,
        job: ScheduledJob,
        result: ReconcileResult,
        upload: UploadSession | None,
        now: datetime,
    ) -> None:
        payload = {
            "step": result.step,
            "outcome": result.outcome.value,
            "evidence": result.evidence.value,
            "safe_to_retry_insert": result.safe_to_retry_insert,
            "candidates": list(result.candidates),
        }

        if (
            result.outcome is StepOutcome.RESUME
            and upload is not None
            and result.bytes_received is not None
        ):
            # Trust the provider's offset, not our own: our record is a guess, the
            # provider's answer is the truth about what it holds.
            upload.bytes_uploaded = max(0, result.bytes_received)
            upload.phase = "resumed"
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_RECONCILED,
                message=result.reason,
                payload={**payload, "resumed_from": upload.bytes_uploaded},
            )
            if job.state == JobState.NEEDS_REVIEW.value:
                self._transition(session, job, JobState.UPLOADING, now)
            session.flush()
            return

        if result.outcome is StepOutcome.RESOLVED and result.video_id:
            job.video_id = result.video_id
            job.video_url = canonical_url(result.video_id)
            job.insert_may_have_succeeded = False
            job.uploaded_at = job.uploaded_at or now
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_RECONCILED,
                message=result.reason,
                payload=payload,
            )
            self._after_upload(session, job, now)
            session.flush()
            return

        # Everything else stops and asks a human. That is the honest outcome: the
        # alternative is a duplicate video or a job that never runs again.
        job.insert_may_have_succeeded = True
        self._transition(session, job, JobState.NEEDS_REVIEW, now)
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_RECONCILE_UNRESOLVED,
            message=result.reason,
            payload=payload,
        )
        session.flush()

    # ------------------------------------------------------------------
    # Transfer
    # ------------------------------------------------------------------

    def _transfer(
        self,
        session: Session,
        job: ScheduledJob,
        access: str,
        now: datetime,
    ) -> str:
        assert job.connection is not None
        upload = _current_session(job)
        total = job.media.size_bytes
        content_type = job.media.container or "video/mp4"

        if upload is None or not upload.session_uri:
            if not quota_mod.can_afford_on(
                session, job.connection.provider, quota_mod.COST_INSERT, now
            ):
                # ``RESERVE_UNITS`` exists "so a sweep or a reschedule is never the
                # thing that exhausts the budget", and the reserve was never consulted
                # from here — only from the sweep and from ``_push_status``. Spending it
                # on uploads would starve verification polling, which is the only route
                # from SCHEDULED to PUBLISHED at all. Gated before the session is
                # created so no resumable session and no charge exist for a call that
                # was never made; a transfer already in flight is never interrupted,
                # because its ``videos.insert`` has already been paid for.
                job.next_attempt_at = quota_mod.retry_at_reset(now)
                job.failure_kind = FailureKind.QUOTA.value
                job.failure_message = (
                    "The daily quota has no room for another videos.insert without "
                    "eating the reserve that keeps publication verification running, "
                    "so nothing was sent. The upload waits for the documented reset."
                )
                self._transition(session, job, JobState.FAILED_RETRY, now)
                ev.record_event(
                    session,
                    job_id=job.id,
                    kind=ev.KIND_RETRY_SCHEDULED,
                    message="The upload is deferred until the next quota window: the "
                    "budget is at its reserve and no videos.insert was issued.",
                    payload={"next_attempt_at": _iso(job.next_attempt_at)},
                )
                session.commit()
                return job.state

            upload = UploadSession(
                job_id=job.id,
                session_uri="",
                phase="created",
                total_bytes=total,
                file_sha256=job.media.sha256,
                content_type=content_type,
                attempt=job.attempt,
            )
            session.add(upload)
            session.flush()
            # Inside the try. Building the body is not network I/O, but it *can* raise
            # ``ConnectorError`` when consent refuses a transform the metadata needs,
            # and it used to sit outside — so a permanent, classifiable failure
            # travelled up as an unhandled exception instead of being recorded against
            # the job. The operator saw a job stuck at Uploading and a traceback in a
            # log, and no retry policy was ever applied.
            try:
                body = self._insert_body(job)
                started = self.ctx.connector.start_resumable_upload(
                    access,
                    metadata=body,
                    total_bytes=total,
                    content_type=content_type,
                    endpoint=RESUMABLE_ENDPOINT,
                )
            except ConnectorError as exc:
                self._fail(session, job, exc, now, during="session start")
                # Commit rather than let the context manager roll the pass back:
                # without this the failure would vanish and the job would be
                # claimed, retried and failed again for ever.
                session.commit()
                return job.state
            quota_mod.add_units_on(session, job.connection.provider, quota_mod.COST_INSERT, now)
            upload.session_uri = started.session_uri
            upload.phase = "started"
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_SESSION_STARTED,
                message="Opened a resumable upload session. The session URI is "
                "persisted so the transfer survives this process ending.",
                payload={"total_bytes": total, "attempt": job.attempt},
            )
        else:
            upload.attempt = job.attempt

        self._transition(session, job, JobState.UPLOADING, now)
        session.commit()

        start_offset = max(0, upload.bytes_uploaded)

        def on_progress(done: int, total_bytes: int) -> None:
            self._report_progress(job.id, done, total_bytes, now)

        try:
            outcome = self.ctx.connector.perform_upload(
                access,
                session_uri=upload.session_uri,
                source_path=job.media.path,
                total_bytes=total,
                content_type=content_type,
                start_offset=start_offset,
                on_progress=on_progress,
            )
        except LeaseLost:
            # Not ours any more. Nothing is recorded — the new owner will find the
            # persisted session URI and its offset and reconcile from there, which is
            # exactly what that row exists for. Writing a state here would be writing
            # into someone else's lease.
            log.warning(
                "Lease lost mid-transfer for job %s; stopping without recording a state.",
                job.id,
            )
            return "lease_lost"
        except ConnectorError as exc:
            with self.ctx.sessions() as fresh:
                current = _load_job(fresh, job.id)
                if current is None:
                    return "missing"
                self._fail(fresh, current, exc, now, during="transfer")
                fresh.commit()
                return current.state

        with self.ctx.sessions() as fresh:
            current = _load_job(fresh, job.id)
            if current is None:
                return "missing"
            session_row = _current_session(current)
            if session_row is not None:
                session_row.bytes_uploaded = outcome.bytes_sent
            if outcome.ambiguous:
                # The bytes may have landed and the answer never came back. Nothing
                # is retried automatically from here; the next pass reconciles.
                current.insert_may_have_succeeded = True
                self._transition(fresh, current, JobState.NEEDS_REVIEW, now)
                ev.record_event(
                    fresh,
                    job_id=current.id,
                    kind=ev.KIND_UPLOAD_AMBIGUOUS,
                    message="The upload body completed but the provider's response was "
                    "never received. The job is held for review and no second "
                    "videos.insert will be issued.",
                    payload={"bytes_sent": outcome.bytes_sent},
                )
                fresh.commit()
                return current.state

            if session_row is not None:
                session_row.phase = "completed"
                session_row.video_id = outcome.video_id
            current.video_id = outcome.video_id
            current.video_url = canonical_url(outcome.video_id)
            current.uploaded_at = now
            current.progress_percent = 100
            current.insert_may_have_succeeded = False
            ev.record_event(
                fresh,
                job_id=current.id,
                kind=ev.KIND_UPLOAD_COMPLETE,
                message=f"The upload body completed; video {outcome.video_id} is now on "
                f"YouTube. This is not a publication claim: the video is at "
                f"{current.content.privacy!r} and may still be processing.",
                payload={
                    "video_id": outcome.video_id,
                    "video_url": current.video_url,
                    "bytes_sent": outcome.bytes_sent,
                },
            )
            self._after_upload(fresh, current, now)
            fresh.commit()
            return current.state

    def _insert_body(self, job: ScheduledJob) -> dict[str, Any]:
        description, hashtags, log_lines = apply_transforms(
            job.content.description,
            list(job.content.hashtags),
            consent=job.content.consent_transforms,
        )
        if log_lines:
            job.content.description = description
            job.content.hashtags = hashtags
            job.content.transform_log = list(job.content.transform_log) + log_lines

        publish_at = job.schedule.publish_at_utc
        if publish_at is not None and has_missed_window(
            publish_at, now=datetime.now(UTC), grace_minutes=0
        ):
            # A publishAt already in the past publishes immediately (DF-04), which is
            # a silent publication. Refuse here even if preflight passed a moment ago.
            raise ConnectorError(
                "The publication time passed while this job was waiting. YouTube "
                "treats a past publishAt as 'publish immediately', so the request was "
                "not sent; reschedule the job.",
                kind=FailureKind.PERMANENT,
                retryable=False,
            )

        return build_insert_body(
            title=job.content.title,
            description=description,
            tags=list(job.content.tags) + list(hashtags),
            category_id=job.content.category_id,
            privacy=job.content.privacy,
            publish_at=publish_at,
        )

    def _after_upload(self, session: Session, job: ScheduledJob, now: datetime) -> None:
        """Move an uploaded job to its post-transfer state.

        Upload completion and publication are different claims, so the state that
        follows is chosen from evidence rather than optimism.
        """
        publish_at = job.schedule.publish_at_utc
        if publish_at is not None and publish_at > now:
            self._transition(session, job, JobState.SCHEDULED, now)
            job.next_poll_at = publish_at
            job.poll_result = PollResult.NOT_YET.value
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_SCHEDULED_REMOTE,
                message="YouTube accepted a scheduled publication. The video is private "
                "and will publish at the requested time; the local state is *Scheduled*, "
                "not *Published*.",
                payload={"publish_at": _iso(publish_at)},
            )
        else:
            self._transition(session, job, JobState.UPLOADED, now)
            job.next_poll_at = now
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_UPLOAD_COMPLETE,
                message="The upload is complete and no publication time was requested. "
                "The video's visibility is whatever was sent explicitly; publication "
                "verification follows.",
            )

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _fail(
        self,
        session: Session,
        job: ScheduledJob,
        exc: ConnectorError,
        now: datetime,
        *,
        during: str,
    ) -> None:
        """Classify a connector failure and record the resulting decision."""
        kind = exc.kind

        if kind in (FailureKind.AMBIGUOUS, FailureKind.HUMAN_REQUIRED):
            job.insert_may_have_succeeded = True
            self._transition(session, job, JobState.NEEDS_REVIEW, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_UPLOAD_AMBIGUOUS,
                message=f"The outcome of the {during} is unknown: {exc}. The job is held "
                f"for review; no second videos.insert will be issued automatically.",
                payload={"kind": kind.value, "during": during},
            )
            return

        if kind is FailureKind.AUTHORIZATION:
            self._block(session, job, kind, f"{exc}", now, kind_name=ev.KIND_AUTH_FAILED)
            return

        if kind is FailureKind.QUOTA:
            job.next_attempt_at = quota_mod.retry_at_reset(now)
            job.failure_kind = kind.value
            job.failure_message = str(exc)
            self._transition(session, job, JobState.FAILED_RETRY, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_RETRY_SCHEDULED,
                message="Quota is exhausted, so the job waits for the next documented "
                "reset rather than burning calls into an unavailable window.",
                payload={"next_attempt_at": _iso(job.next_attempt_at)},
            )
            return

        if kind is FailureKind.PERMANENT or not exc.retryable:
            job.failure_kind = kind.value
            job.failure_message = str(exc)
            self._transition(session, job, JobState.FAILED, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_RETRY_EXHAUSTED,
                message=f"The {during} failed permanently: {exc}",
                payload={"kind": kind.value, "during": during},
            )
            return

        # Transient.
        job.failure_kind = kind.value
        job.failure_message = str(exc)
        if job.attempt >= job.max_attempts:
            self._transition(session, job, JobState.FAILED, now)
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_RETRY_EXHAUSTED,
                message=f"The {during} failed {job.attempt} time(s) and the retry budget "
                f"is spent: {exc}",
                payload={"kind": kind.value, "attempt": job.attempt, "during": during},
            )
            return

        # Deterministic jitter derived from the job itself, so a restart re-derives
        # the same schedule instead of re-rolling it.
        jitter = ((job.id * 2654435761) % 1000) / 1000 * 0.2
        job.next_attempt_at = next_retry_at(
            job.attempt,
            now=now,
            base_seconds=BACKOFF_BASE_SECONDS,
            max_seconds=BACKOFF_MAX_SECONDS,
            jitter_ratio=jitter,
        )
        self._transition(session, job, JobState.FAILED_RETRY, now)
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_RETRY_SCHEDULED,
            message=f"The {during} failed transiently; retry {job.attempt + 1} of "
            f"{job.max_attempts} is scheduled. The upload resumes from the provider's "
            f"reported offset rather than restarting.",
            payload={
                "kind": kind.value,
                "next_attempt_at": _iso(job.next_attempt_at),
                "during": during,
            },
        )

    def _block(
        self,
        session: Session,
        job: ScheduledJob,
        kind: FailureKind,
        message: str,
        now: datetime,
        *,
        kind_name: str = ev.KIND_BLOCKED,
        code: str | None = None,
    ) -> None:
        job.failure_kind = kind.value
        job.failure_message = message
        job.next_attempt_at = None
        self._transition(session, job, JobState.BLOCKED, now)
        ev.record_event(
            session,
            job_id=job.id,
            kind=kind_name,
            message=message,
            payload={"failure_kind": kind.value, "code": code},
        )

    def _transition(
        self, session: Session, job: ScheduledJob, target: JobState, now: datetime
    ) -> None:
        """Apply a state change, refusing any move the canonical machine forbids."""
        current = JobState(job.state)
        if current is target:
            return
        if not can_transition(current, target):
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_NEEDS_REVIEW,
                message=f"Illegal transition {current.value} -> {target.value} was "
                f"refused; the job stays at {current.value}.",
                payload={"from": current.value, "to": target.value},
            )
            log.error("Refused illegal transition %s -> %s for job %s", current, target, job.id)
            return
        job.state = target.value
        job.updated_at = now
        session.flush()

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def _report_progress(self, job_id: int, done: int, total: int, now: datetime) -> None:
        """Publish progress and keep the lease alive.

        On its own short transaction so a progress write cannot hold the lock the
        worker itself needs, and so a killed process leaves the last reported figure
        rather than an uncommitted one.

        The heartbeat's answer is not decoration. ``False`` means this job is no longer
        ours — the lease expired and another worker has claimed it — and carrying on
        would put two processes inside one resumable session at once, which is the
        duplicate-upload case the whole lease protocol exists to prevent. Raising from
        the callback is what stops the transfer mid-flight; the connector just calls
        ``on_progress`` on its way through, so the exception unwinds the transfer with
        us.
        """
        percent = int(done * 100 / total) if total else 0
        with self.ctx.sessions() as session:
            job = session.get(ScheduledJob, job_id)
            if job is None:
                return
            job.progress_percent = max(0, min(100, percent))
            session.commit()
        held = heartbeat(
            self.ctx.engine,
            job_id=job_id,
            owner=self.owner,
            now=now,
            lease_seconds=self.ctx.settings.lease_seconds,
        )
        if not held:
            raise LeaseLost(job_id)


# ----------------------------------------------------------------------
# Helpers shared with the sweep
# ----------------------------------------------------------------------


def canonical_url(video_id: str) -> str:
    """The canonical watch URL. Written at the same moment as the video ID."""
    return f"https://www.youtube.com/watch?v={video_id}"


def remote_state_of(
    status: VideoStatus | None, *, publish_at: datetime | None, now: datetime
) -> str:
    """Map a provider status onto the canonical :class:`RemoteState`.

    Kept here rather than on the connector so the vocabulary has exactly one home
    (``02`` §4.2) and the sweep and the runner cannot drift apart.
    """
    if status is None:
        return RemoteState.UNKNOWN.value
    if status.upload_status == "failed":
        return RemoteState.FAILED.value
    if status.upload_status == "uploaded" and status.processing_status == "processing":
        return RemoteState.UPLOADED_PROCESSING.value
    if status.privacy_status == "public":
        return RemoteState.PUBLIC.value
    if status.privacy_status == "private":
        # Prefer the provider's own ``publishAt``: this function's entire job is to
        # say what the *remote* record shows, and the local plan is only a fallback
        # for providers that omit the field.
        when = status.publish_at or publish_at
        if when is not None and when > now:
            return RemoteState.SCHEDULED.value
        if status.processing_status == "processing":
            return RemoteState.UPLOADED_PROCESSING.value
        # Private with no publication time ahead of it. This used to fall through to
        # ``SCHEDULED`` as well, which is a publication claim the provider never made:
        # nothing is going to publish this video, so the dashboard showed "Scheduled"
        # for a row that would sit there for ever. ``PRIVATE_UNLISTED_OR_OTHER`` says
        # only what was observed — it is private — and makes no promise about when.
        return RemoteState.PRIVATE_UNLISTED_OR_OTHER.value
    return RemoteState.PRIVATE_UNLISTED_OR_OTHER.value


def _record_observation(
    session: Session, job: ScheduledJob, status: VideoStatus, now: datetime
) -> None:
    observation = RemoteObservation(
        job_id=job.id,
        observed_at=now,
        video_id=status.video_id,
        remote_state=remote_state_of(status, publish_at=job.schedule.publish_at_utc, now=now),
        privacy_status=status.privacy_status,
        upload_status=status.upload_status,
        processing_status=status.processing_status,
        publish_at=status.publish_at,
    )
    session.add(observation)
    job.last_polled_at = now
    session.flush()


def _load_job(session: Session, job_id: int) -> ScheduledJob | None:
    return session.execute(
        select(ScheduledJob)
        .where(ScheduledJob.id == job_id)
        .options(
            selectinload(ScheduledJob.schedule),
            selectinload(ScheduledJob.content),
            selectinload(ScheduledJob.media),
            selectinload(ScheduledJob.connection),
            selectinload(ScheduledJob.channel),
            selectinload(ScheduledJob.upload_sessions),
        )
    ).scalar_one_or_none()


def _current_session(job: ScheduledJob) -> UploadSession | None:
    """The newest non-completed upload session for a job.

    A job has at most one live session; older completed ones are history and are kept
    because they carry the offsets an audit needs.
    """
    live = [s for s in job.upload_sessions if s.phase != "completed"]
    if live:
        return max(live, key=lambda s: s.id)
    sessions = sorted(job.upload_sessions, key=lambda s: s.id)
    return sessions[-1] if sessions else None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
