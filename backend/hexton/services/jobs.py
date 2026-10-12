"""Job operations: create, edit, duplicate, queue, reschedule, cancel.

These live outside the routers because they are the rules, not the transport. The API
contributes authentication and validation of shapes; whether a job *may* be queued,
rescheduled or cancelled, and what actually happens to the video on YouTube when it
is, is decided here and is testable without an HTTP client.

Three documented constraints are enforced at this layer rather than left to a screen:

* ``DF-03`` — a publication time applies only to a video that has never been
  published. Rescheduling a published video is refused with that reason, rather than
  being attempted and silently ignored by the API.
* ``DF-06`` — changing anything on an existing video sends the **complete** snippet.
  There is no code path here that can send a partial one.
* Cancelling locally and cancelling on YouTube are different operations with
  different outcomes, and this module reports which one actually happened.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from hexton import quota as quota_mod
from hexton.api.schemas import IssueOut, JobFields
from hexton.bootstrap import AppContext
from hexton.connectors.base import ConnectorError
from hexton.db import events as ev
from hexton.db.models import (
    ChannelIdentity,
    ContentMetadata,
    MediaFile,
    PlatformConnection,
    Schedule,
    ScheduledJob,
)
from hexton.domain.jobstate import (
    FailureKind,
    JobState,
    can_transition,
    video_has_been_public,
)
from hexton.domain.mention import assess as assess_mention
from hexton.domain.scheduling import (
    ZoneError,
    check_publish_time,
    ensure_utc,
    resolve_zone,
)
from hexton.domain.validation import normalise_hashtags, validate_metadata
from hexton.media import validate_file
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: States an operator may edit the *content* of. Once bytes have left for YouTube the
#: title and description are no longer the whole truth about the remote video, so they
#: are frozen rather than quietly diverging.
EDITABLE_STATES = frozenset(
    {
        JobState.DRAFT,
        JobState.READY,
        JobState.QUEUED,
        JobState.FAILED_RETRY,
        JobState.BLOCKED,
        JobState.MISSED_WINDOW,
    }
)


class JobOperationError(RuntimeError):
    """An operation the operator asked for that cannot be performed, with the reason."""

    def __init__(self, message: str, *, code: str = "not_permitted") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PreparedJob:
    """A validated set of fields plus everything validation learned about them."""

    fields: JobFields
    issues: list[IssueOut] = field(default_factory=list)
    schedule_issues: list[IssueOut] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues + self.schedule_issues)


def prepare(ctx: AppContext, session: Session, data: JobFields, *, now: datetime) -> PreparedJob:
    """Validate a job's fields without persisting anything."""
    issues: list[IssueOut] = []

    verdict = validate_metadata(
        title=data.title,
        description=data.description,
        tags=list(data.tags) + normalise_hashtags(data.hashtags),
        category_id=data.category_id,
        privacy=data.privacy,
    )
    issues += [
        IssueOut(code=i.code, message=i.message, severity=i.severity.value) for i in verdict.issues
    ]

    media = session.get(MediaFile, data.media_id) if data.media_id else None
    if media is None:
        issues.append(
            IssueOut(
                code="unknown_media",
                message="That media file is not in the library.",
                severity="error",
            )
        )
    else:
        report = validate_file(media.path)
        if not report.ok:
            issues += [
                IssueOut(code=i.code, message=i.message, severity="error")
                for i in report.blocking_issues
            ]

    if data.connection_id is not None:
        conn = session.get(PlatformConnection, data.connection_id)
        if conn is None:
            issues.append(
                IssueOut(
                    code="unknown_connection",
                    message="That connection does not exist.",
                    severity="error",
                )
            )
        elif conn.status != "active":
            issues.append(
                IssueOut(
                    code="connection_not_active",
                    message=f"The selected connection is {conn.status!r}; reconnect it before queueing.",
                    severity="warning",
                )
            )
    if data.channel_identity_id is not None:
        channel = session.get(ChannelIdentity, data.channel_identity_id)
        if channel is None:
            issues.append(
                IssueOut(
                    code="unknown_channel",
                    message="That channel identity does not exist.",
                    severity="error",
                )
            )
        elif data.connection_id is not None and channel.connection_id != data.connection_id:
            issues.append(
                IssueOut(
                    code="channel_mismatch",
                    message="The selected channel does not belong to the selected connection.",
                    severity="error",
                )
            )

    if data.mention_requested:
        text = f"{data.title}\n{data.description}"
        assessment = assess_mention(mention_requested=True, text=text)
        issues.append(IssueOut(code="mention_state", message=assessment.reason, severity="warning"))

    schedule_issues: list[IssueOut] = []
    if data.display_timezone:
        # An unknown zone would otherwise be stored and then raise the first time any
        # screen tried to render a publication time in it — an unexplained 500 rather
        # than a field the operator can correct.
        try:
            resolve_zone(data.display_timezone)
        except ZoneError:
            schedule_issues.append(
                IssueOut(
                    code="unknown_timezone",
                    message=f"{data.display_timezone!r} is not a time zone this "
                    "install can resolve, so publication times could not be "
                    "displayed in it. Use an IANA key such as 'Asia/Dhaka'.",
                    severity="error",
                )
            )
    if data.publish_at_utc is not None and data.privacy == "public":
        # DF-04: YouTube treats publishAt on a public video as "publish now", so the
        # time would be silently ignored. Refused rather than quietly rewritten.
        schedule_issues.append(
            IssueOut(
                code="public_conflicts_with_publish_at",
                message="YouTube treats a publication time on a public video as "
                "'publish immediately', so the time would be ignored. Keep the "
                "video private to schedule it, or remove the time to publish now.",
                severity="error",
            )
        )
    if data.publish_at_utc is not None:
        check = check_publish_time(
            data.publish_at_utc,
            now=now,
            never_published=True,
            min_lead_minutes=data.min_lead_minutes or ctx.settings.min_lead_minutes,
            warning_lead_minutes=ctx.settings.warning_lead_minutes,
        )
        schedule_issues += [
            IssueOut(code=i.code, message=i.message, severity="error" if i.blocking else "warning")
            for i in check.issues
        ]

    return PreparedJob(fields=data, issues=issues, schedule_issues=schedule_issues)


# ----------------------------------------------------------------------
# Create / update
# ----------------------------------------------------------------------


def create_job(
    ctx: AppContext,
    session: Session,
    data: JobFields,
    *,
    now: datetime,
    queue: bool = False,
) -> ScheduledJob:
    """Persist a new job. Refuses to create one that would be invalid to run."""
    prepared = prepare(ctx, session, data, now=now)
    if not prepared.ok:
        messages = "; ".join(
            i.message for i in prepared.issues + prepared.schedule_issues if i.severity == "error"
        )
        raise JobOperationError(f"The job is not valid: {messages}", code="invalid")

    media = session.get(MediaFile, data.media_id)
    assert media is not None

    # Idempotency first: a retried POST must not become a second job. The column is
    # NOT NULL and unique, so a key is minted when the caller did not supply one.
    key = (data.idempotency_key or "").strip() or uuid.uuid4().hex
    if len(key) > 64:
        raise JobOperationError(
            "That idempotency key is longer than 64 characters.",
            code="invalid_idempotency_key",
        )
    existing = session.execute(
        select(ScheduledJob).where(ScheduledJob.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    assessment = assess_mention(
        mention_requested=data.mention_requested,
        text=f"{data.title}\n{data.description}",
    )

    metadata = ContentMetadata(
        title=data.title,
        description=data.description,
        tags=[t.strip() for t in data.tags if t.strip()],
        hashtags=normalise_hashtags(data.hashtags),
        category_id=data.category_id,
        privacy=data.privacy,
        mention_requested=data.mention_requested,
        mention_handles=list(assessment.handles_found),
        mention_state=assessment.state.value,
        mention_reason=assessment.reason,
        consent_transforms=data.consent_transforms,
    )
    schedule = Schedule(
        publish_at_utc=data.publish_at_utc,
        display_timezone=data.display_timezone or ctx.settings.display_timezone,
        min_lead_minutes=data.min_lead_minutes or ctx.settings.min_lead_minutes,
        report_date=(data.publish_at_utc or now),
    )
    job = ScheduledJob(
        schedule=schedule,
        content=metadata,
        media=media,
        connection_id=data.connection_id,
        channel_identity_id=data.channel_identity_id,
        state=JobState.READY.value if queue else JobState.DRAFT.value,
        max_attempts=ctx.settings.worker_max_attempts,
        next_attempt_at=None,
        idempotency_key=key,
    )
    session.add(job)
    session.flush()

    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_CREATED,
        message=f"Job created from {media.original_name or media.path}.",
        payload={"media_id": media.id, "privacy": data.privacy},
    )
    for issue in prepared.issues + prepared.schedule_issues:
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_VALIDATED,
            message=f"{issue.severity}: {issue.message}",
            payload={"code": issue.code, "severity": issue.severity},
        )

    if queue:
        queue_job(session, job, now=now)
    else:
        session.flush()
    return job


def update_job(
    ctx: AppContext,
    session: Session,
    job: ScheduledJob,
    data: JobFields,
    *,
    now: datetime,
) -> ScheduledJob:
    """Edit a job's content and plan.

    Refused once the job has left the local-only states: at that point the title and
    description may already exist on YouTube, and editing them here would leave the
    local record and the remote video disagreeing with no record of which one is
    right.
    """
    state = JobState(job.state)
    if state not in EDITABLE_STATES:
        raise JobOperationError(
            f"A job in {state.value} can no longer be edited here, because its content "
            f"may already exist on YouTube. Duplicate it to make a new version, so the "
            f"two never silently diverge.",
            code="not_editable",
        )

    prepared = prepare(ctx, session, data, now=now)
    if not prepared.ok:
        messages = "; ".join(
            i.message for i in prepared.issues + prepared.schedule_issues if i.severity == "error"
        )
        raise JobOperationError(f"The job is not valid: {messages}", code="invalid")

    media = session.get(MediaFile, data.media_id)
    assert media is not None
    assessment = assess_mention(
        mention_requested=data.mention_requested, text=f"{data.title}\n{data.description}"
    )

    job.media = media
    job.connection_id = data.connection_id
    job.channel_identity_id = data.channel_identity_id
    job.content.title = data.title
    job.content.description = data.description
    job.content.tags = [t.strip() for t in data.tags if t.strip()]
    job.content.hashtags = normalise_hashtags(data.hashtags)
    job.content.category_id = data.category_id
    job.content.privacy = data.privacy
    job.content.mention_requested = data.mention_requested
    job.content.mention_handles = list(assessment.handles_found)
    job.content.mention_state = assessment.state.value
    job.content.mention_reason = assessment.reason
    job.content.consent_transforms = data.consent_transforms
    job.schedule.publish_at_utc = data.publish_at_utc
    job.schedule.display_timezone = data.display_timezone or job.schedule.display_timezone
    job.schedule.min_lead_minutes = data.min_lead_minutes or job.schedule.min_lead_minutes
    job.schedule.report_date = data.publish_at_utc or job.schedule.report_date
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_VALIDATED,
        message="Job content updated by the operator.",
    )
    session.flush()
    return job


def duplicate_job(
    ctx: AppContext,
    session: Session,
    job: ScheduledJob,
    *,
    now: datetime,
) -> ScheduledJob:
    """Copy a job back to a fresh draft, keeping the media reference.

    Deliberately resets the plan to *no publication time*: copying a schedule forward
    is how a job gets published twice, and an operator duplicating a clip almost
    always means "same content, different moment".
    """
    data = JobFields(
        media_id=job.media_id,
        title=job.content.title,
        description=job.content.description,
        tags=list(job.content.tags),
        hashtags=list(job.content.hashtags),
        category_id=job.content.category_id,
        privacy=job.content.privacy,  # type: ignore[arg-type]
        connection_id=job.connection_id,
        channel_identity_id=job.channel_identity_id,
        publish_at_utc=None,
        display_timezone=job.schedule.display_timezone,
        min_lead_minutes=job.schedule.min_lead_minutes,
        mention_requested=job.content.mention_requested,
        consent_transforms=job.content.consent_transforms,
    )
    clone = create_job(ctx, session, data, now=now, queue=False)
    ev.record_event(
        session,
        job_id=clone.id,
        kind=ev.KIND_CREATED,
        message=f"Duplicated from job {job.public_id}. The publication time was "
        f"deliberately cleared so a schedule is never published twice by accident.",
        payload={"source_job_id": job.id},
    )
    return clone


def delete_draft(session: Session, job: ScheduledJob) -> None:
    """Remove a job that never left this machine.

    ``CANCELLED`` is allowed because cancelling *is* the operator saying they are done
    with it — but only while that remains true. A cancelled job can still be holding a
    video ID, an unresolved ``insert_may_have_succeeded``, or a remote outcome that was
    never settled: deleting it then destroys the only record of something that exists
    on YouTube, which is precisely what the guard below exists to prevent for every
    other state. So the state check and the evidence check are the same check.
    """
    state = JobState(job.state)
    if state not in (JobState.DRAFT, JobState.CANCELLED):
        raise JobOperationError(
            f"Only a draft or cancelled job can be deleted here. A job that has "
            f"reached {state.value} may have a video on YouTube, so deleting the local "
            f"record would lose the audit trail for something that still exists "
            f"remotely.",
            code="not_deletable",
        )
    if job.video_id is not None or job.insert_may_have_succeeded:
        raise JobOperationError(
            "This cancelled job still refers to a video — or to an upload whose "
            "outcome was never confirmed — so it is the audit trail for something "
            "that may exist on YouTube. Delete the video first, or leave the record "
            "in place.",
            code="not_deletable",
        )
    session.delete(job)


# ----------------------------------------------------------------------
# Queue / retry
# ----------------------------------------------------------------------


def queue_job(session: Session, job: ScheduledJob, *, now: datetime) -> None:
    """Make a job claimable now."""
    state = JobState(job.state)

    # Checked before anything else, and for every state: a past publication time is
    # a refusal to queue in its own right, not a special case of re-queueing a
    # missed window. YouTube reads a past publishAt as "publish now", so queueing
    # would publish at once rather than when the operator asked.
    publish_at = job.schedule.publish_at_utc
    if publish_at is not None and ensure_utc(publish_at) <= now:
        raise JobOperationError(
            "This job's publication time has passed, and YouTube treats a past "
            'publication time as "publish immediately", so queueing it now would '
            "publish at once instead of when you asked. Set a new time first.",
            code="publish_time_in_past",
        )

    if state is JobState.DRAFT:
        # Queueing *is* the operator asserting the job is valid, and that is exactly
        # what READY means. Promotion happens here rather than by widening the state
        # machine, so DRAFT -> READY -> QUEUED stays the one documented route.
        job.state = JobState.READY.value
        job.updated_at = now
        state = JobState.READY
    if state not in (
        JobState.READY,
        JobState.FAILED_RETRY,
        JobState.BLOCKED,
        JobState.MISSED_WINDOW,
    ):
        raise JobOperationError(f"A job in {state.value} cannot be queued.", code="not_queueable")
    if not can_transition(state, JobState.QUEUED):
        raise JobOperationError(f"{state.value} cannot move to queued.", code="not_queueable")

    job.state = JobState.QUEUED.value
    job.next_attempt_at = now
    job.failure_kind = None
    job.failure_message = None
    job.updated_at = now
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_QUEUED,
        message="Queued by the operator; the worker will claim it on its next pass.",
    )
    session.flush()


def retry_job(session: Session, job: ScheduledJob, *, now: datetime) -> None:
    """Operator-initiated retry, used where the automatic budget is spent."""
    state = JobState(job.state)
    if state is JobState.NEEDS_REVIEW:
        raise JobOperationError(
            "This job's outcome is uncertain, so a retry could create a second copy "
            "on YouTube. Resolve it first by confirming or rejecting the candidate "
            "video.",
            code="needs_review_first",
        )
    if state is JobState.PUBLISHED:
        raise JobOperationError("This job is already published.", code="already_published")
    queue_job(session, job, now=now)


# ----------------------------------------------------------------------
# Reschedule
# ----------------------------------------------------------------------


#: Opening of the record written when a reschedule moved the local plan but YouTube
#: refused (or was never asked). It is also the marker the next successful reschedule
#: looks for, so that clearing a disagreement never erases some *other* failure the job
#: is still carrying — a successful ``videos.update`` must not silence a failed upload.
_RESCHEDULE_DISAGREEMENT = (
    "The publication time changed in Hexton but not on YouTube, so the two disagree:"
)


def _clear_reschedule_disagreement(job: ScheduledJob) -> None:
    """Drop a disagreement this function wrote, and only that one."""
    if job.failure_message and job.failure_message.startswith(_RESCHEDULE_DISAGREEMENT):
        job.failure_kind = None
        job.failure_message = None


def reschedule_job(
    ctx: AppContext,
    session: Session,
    job: ScheduledJob,
    publish_at: datetime,
    *,
    now: datetime,
) -> ScheduledJob:
    """Move a publication time, respecting what the API will actually honour.

    ``DF-03``: ``publishAt`` applies only to a video that has **never been published**.
    Once a video is public, the same API path cannot schedule it again, so this is
    refused with that reason rather than attempted.
    """
    at = ensure_utc(publish_at, field="publish_at_utc")
    state = JobState(job.state)

    observed_privacy = job.observations[-1].privacy_status if job.observations else None
    has_been_public = video_has_been_public(state, job.published_at_remote, observed_privacy)

    if has_been_public:
        # Not just ``state is PUBLISHED``. ``DF-03`` is about the *video*, and the
        # video's history is a separate question from this job's label: a sweep can
        # have recorded ``published_at_remote`` on a row still sitting in ``UPLOADED``,
        # and the newest observation can read ``public`` while the state machine has
        # not caught up. Letting either through would push a ``publishAt`` that the
        # documentation says is not honoured on an already-published video — so the
        # local row would show a schedule YouTube has quietly declined, which is the
        # wrong-remote-state failure in its most convincing form.
        raise JobOperationError(
            "This video is already published. A publication time can only be set on a "
            "video that has never been published — publishAt applies only to a video "
            "that has never published, and this API does not honour it afterwards, so "
            "Hexton will not record a schedule it cannot send.",
            code="already_published",
        )

    check = check_publish_time(
        at,
        now=now,
        never_published=not has_been_public,
        min_lead_minutes=job.schedule.min_lead_minutes,
        warning_lead_minutes=ctx.settings.warning_lead_minutes,
    )
    if not check.ok:
        messages = "; ".join(i.message for i in check.blocking)
        raise JobOperationError(
            f"That publication time is not usable: {messages}", code="invalid_time"
        )

    job.schedule.publish_at_utc = at
    job.schedule.report_date = at
    job.rescheduled_at = now
    job.updated_at = now

    remote_note: str | None = None
    remote_accepted = False
    if job.video_id is not None:
        # The video already exists on YouTube, so the change has to be sent there too.
        # ``DF-06`` means the whole snippet goes with it, or the description is deleted.
        pushed = _push_status(
            ctx,
            session,
            job,
            privacy="private",
            publish_at=at,
            now=now,
            reason="rescheduled",
        )
        remote_note = pushed.sentence
        remote_accepted = pushed.accepted

    if job.video_id is None:
        # Nothing exists on YouTube to hold this time, so no state may claim it does.
        # ``NEEDS_REVIEW -> SCHEDULED`` is legal, which would otherwise let an
        # ambiguous job with no video ID announce "Scheduled on YouTube" the moment an
        # operator edited its publication time (F-05).
        pass
    elif remote_accepted:
        state = JobState(job.state)
        if state is not JobState.SCHEDULED and can_transition(state, JobState.SCHEDULED):
            job.state = JobState.SCHEDULED.value
        _clear_reschedule_disagreement(job)
    else:
        # The local plan moved and the remote did not. Saying so is the whole of the
        # obligation: an operator who sees "Scheduled on YouTube" for a video YouTube
        # still holds at the old time — or with no time at all — has been told a lie by
        # the one screen they trust (F-05).
        job.failure_kind = FailureKind.HUMAN_REQUIRED.value
        job.failure_message = f"{_RESCHEDULE_DISAGREEMENT} {remote_note}"
    job.next_poll_at = at
    job.poll_result = None

    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_RESCHEDULED,
        message=(
            f"Publication moved to {at.isoformat()}." + (f" {remote_note}" if remote_note else "")
        ),
        payload={"publish_at": at.isoformat(), "remote": remote_note},
    )
    session.flush()
    return job


def may_issue_snippet_update(job: ScheduledJob) -> tuple[bool, str]:
    """Whether a ``videos.update`` carrying this job's own snippet may be sent.

    ``D-6`` / ``FEAS-06`` / ``DF-06`` / ``AC-MVP-009``. ``may_issue_videos_update``
    has existed since Phase 0, is what ``07-feasibility-checklist.md`` §FEAS-06
    describes as encoding the failure fallback, and was never called from production
    code — so the rule "the app must not issue ``videos.update`` on a
    ``manual_required`` job at all" was a documented intention rather than a property
    of the running system. Both ``videos.update`` sites in the application go through
    here now.

    ``description_changing=False`` is a fact rather than a relaxation. Content is
    frozen once bytes have left this machine (``EDITABLE_STATES``), so the snippet
    travelling with this request is byte-for-byte the one Hexton uploaded: nothing
    Hexton holds is being rewritten. What Hexton *cannot* know is whether an operator
    edited the description in YouTube Studio since — the API has no way to read that
    back (``DF-19``) — and that uncertainty is exactly what ``manual_required``
    records and what ``D-6`` answers with "do not issue the update; edit in Studio".
    ``rewrite_risk``'s decision branch is therefore not the one that applies here, and
    passing ``description_changing=True`` would additionally refuse every
    ``text_only`` job on the strength of an ``@handle`` the description does not
    contain.
    """
    from hexton.domain.mention import may_issue_videos_update
    from hexton.services.mentions import current_state

    return may_issue_videos_update(
        current_state(job),
        update_includes_full_snippet=True,
        description_changing=False,
    )


@dataclass(frozen=True)
class PushOutcome:
    """Whether YouTube took a ``videos.update``, plus the sentence to record with it.

    ``sentence`` is a description for an event log. ``accepted`` is the only part a
    state machine may act on: F-05 was a caller reading a sentence that said "YouTube
    was not told" and writing ``SCHEDULED`` anyway, because nothing in the return type
    distinguished "accepted" from "here is what happened".
    """

    accepted: bool
    sentence: str


def _push_status(
    ctx: AppContext,
    session: Session,
    job: ScheduledJob,
    *,
    privacy: str,
    publish_at: datetime | None,
    now: datetime,
    reason: str,
) -> PushOutcome:
    """Send a *complete* status+snippet change for an existing video.

    The sentence is what the caller records: an operator is entitled to know whether
    YouTube accepted the change, refused it, or was never asked. ``accepted`` is True
    only when the provider itself answered — not when the local bookkeeping succeeded.
    """
    conn = job.connection
    if conn is None or conn.status != "active":
        return PushOutcome(
            False,
            "The local plan was updated, but no connection is active, so YouTube was "
            "not told and still holds the previous publication time.",
        )

    token = _token(ctx, conn)
    if token is None:
        return PushOutcome(
            False,
            "The local plan was updated, but no usable credential is stored, so YouTube "
            "was not told and still holds the previous publication time.",
        )

    if not quota_mod.can_afford_on(session, conn.provider, quota_mod.COST_UPDATE, now):
        return PushOutcome(
            False,
            "The local plan was updated, but the daily quota has no room for the "
            "50-unit videos.update, so YouTube was not told yet.",
        )

    allowed, why_not = may_issue_snippet_update(job)
    if not allowed:
        # The local plan has already been written by the caller, so the sentence this
        # returns is appended to that record — it must say plainly that YouTube still
        # holds the previous publication time, which is what every other refusal in
        # this function says.
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_NEEDS_REVIEW,
            message="The local plan changed but no videos.update was sent: " + why_not,
            payload={"reason": reason},
        )
        return PushOutcome(
            False,
            "The local plan was updated, but YouTube was not asked to change it "
            f"because this job's mention forbids a metadata update: {why_not} YouTube "
            "still holds the previous publication time.",
        )

    try:
        status = ctx.connector.update_video_status(
            token,
            job.video_id or "",
            privacy=privacy,
            publish_at=publish_at,
            full_snippet={
                "title": job.content.title,
                "description": job.content.description,
                "tags": list(job.content.tags) + list(job.content.hashtags),
                "categoryId": job.content.category_id,
            },
        )
    except ConnectorError as exc:
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_CANCEL_REMOTE_FAILED if reason == "cancel" else ev.KIND_NEEDS_REVIEW,
            message=f"YouTube did not accept the change ({exc}). The local plan was "
            f"updated, so the two now disagree; this is reported rather than hidden.",
            payload={"reason": reason},
        )
        return PushOutcome(False, f"YouTube did not accept the change: {exc}")

    quota_mod.add_units_on(session, conn.provider, quota_mod.COST_UPDATE, now)
    return PushOutcome(
        True,
        f"YouTube accepted the change; the video is now {status.privacy_status!r}"
        + (f" with a publication time of {publish_at.isoformat()}." if publish_at else "."),
    )


def _token(ctx: AppContext, connection: PlatformConnection) -> str | None:
    key = f"conn:{connection.public_id}"
    access = ctx.credentials.read(f"{key}:access")
    if access:
        return access
    refresh = ctx.credentials.read(f"{key}:refresh")
    if not refresh:
        return None
    try:
        tokens = ctx.connector.refresh(refresh)
    except ConnectorError:
        return None
    ctx.credentials.write(f"{key}:access", tokens.access_token)
    return tokens.access_token


# ----------------------------------------------------------------------
# Cancellation
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CancelOutcome:
    local_cancelled: bool
    remote_attempted: bool
    remote_succeeded: bool | None
    message: str


def cancel_job(
    ctx: AppContext,
    session: Session,
    job: ScheduledJob,
    *,
    now: datetime,
    delete_remote: bool = False,
    reason: str = "",
) -> CancelOutcome:
    """Cancel, distinguishing the local act from the remote one.

    Cancelling a local job is always possible. Cancelling a publication that already
    exists on YouTube is a different claim, and when it fails the outcome says so —
    an operator who cancelled and walked away believing the video was gone would be
    the worst failure this application could produce.
    """
    state = JobState(job.state)
    if state in (JobState.PUBLISHED, JobState.CANCELLED):
        raise JobOperationError(
            f"A job in {state.value} cannot be cancelled.", code="not_cancellable"
        )

    if (
        state is JobState.UPLOADING
        and job.lease_owner
        and (job.lease_expires_at is None or job.lease_expires_at > now)
    ):
        # ``UPLOADING -> CANCELLED`` is not in the transition table, and writing the
        # column directly would only pretend. A worker is inside the transfer right
        # now: it will commit its own result a moment later and overwrite whatever we
        # set here, leaving an operator with a row that says "cancelled" and a video
        # that exists. Wait for the transfer to end — or for its lease to lapse — and
        # then decide, which is exactly what the state machine already requires.
        raise JobOperationError(
            "This job is uploading right now, so cancelling it would be overwritten by "
            "the worker a moment later. Wait for the transfer to finish (or for its "
            "lease to expire) and cancel then.",
            code="upload_in_progress",
        )

    job.cancelled_at = now
    job.state = JobState.CANCELLED.value
    job.next_attempt_at = None
    job.next_poll_at = None
    job.updated_at = now
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_CANCELLED_LOCAL,
        message=f"Cancelled locally{f': {reason}' if reason else ''}. This stops Hexton "
        f"from touching the job; it says nothing about what is already on YouTube.",
        payload={"reason": reason, "delete_remote": delete_remote},
    )

    if job.video_id is None:
        if job.insert_may_have_succeeded:
            # The bytes may be on their way right now, or may already have landed. The
            # absence of a video *ID* is not the absence of a video, and saying so is
            # the one sentence an operator would repeat to a client: "nothing was ever
            # uploaded". The honest answer is that the outcome is unknown and remains
            # so — which is also why ``insert_may_have_succeeded`` is not cleared here.
            job.cancel_remote_outcome = "remote_unknown"
            session.flush()
            return CancelOutcome(
                local_cancelled=True,
                remote_attempted=False,
                remote_succeeded=None,
                message="Cancelled locally. Hexton has no video ID for this job, but an "
                "earlier upload attempt ended without a definite answer, so a video may "
                "still exist on YouTube. It was not touched and must be checked by hand "
                "before anything is uploaded again.",
            )
        job.cancel_remote_outcome = "not_applicable"
        session.flush()
        return CancelOutcome(
            local_cancelled=True,
            remote_attempted=False,
            remote_succeeded=None,
            message="Cancelled locally. No video had been created, so nothing needed "
            "removing from YouTube.",
        )

    conn = job.connection
    token = _token(ctx, conn) if conn and conn.status == "active" else None
    if token is None:
        job.cancel_remote_outcome = "remote_unknown"
        session.flush()
        return CancelOutcome(
            local_cancelled=True,
            remote_attempted=False,
            remote_succeeded=None,
            message=f"Cancelled locally. Video {job.video_id} still exists on YouTube "
            f"and could not be reached to change, so it must be checked by hand.",
        )

    try:
        if delete_remote:
            removed = ctx.connector.delete_video(token, job.video_id)
            quota_mod.add_units_on(session, conn.provider, quota_mod.COST_UPDATE, now)
            job.cancel_remote_outcome = "deleted" if removed else "not_found"
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_CANCELLED_REMOTE,
                message=f"Video {job.video_id} was deleted from YouTube."
                if removed
                else f"YouTube did not have video {job.video_id}.",
            )
            return CancelOutcome(
                local_cancelled=True,
                remote_attempted=True,
                remote_succeeded=removed,
                message=(
                    f"Cancelled locally, and video {job.video_id} was deleted from YouTube."
                    if removed
                    else f"Cancelled locally. YouTube reported that video "
                    f"{job.video_id} does not exist."
                ),
            )

        allowed, why_not = may_issue_snippet_update(job)
        if not allowed:
            job.cancel_remote_outcome = "not_attempted"
            ev.record_event(
                session,
                job_id=job.id,
                kind=ev.KIND_CANCEL_REMOTE_FAILED,
                message=f"YouTube was not asked to change video {job.video_id}, so its "
                f"publication was not cancelled there: {why_not}",
                payload={"video_id": job.video_id},
            )
            session.flush()
            return CancelOutcome(
                local_cancelled=True,
                remote_attempted=False,
                remote_succeeded=None,
                message=f"Cancelled locally, but no change was sent to YouTube for "
                f"video {job.video_id}: {why_not} It may still publish at its original "
                f"time, so make it private in YouTube Studio now, or cancel with "
                f'"delete the video" instead.',
            )

        status = ctx.connector.update_video_status(
            token,
            job.video_id,
            privacy="private",
            publish_at=None,
            full_snippet={
                "title": job.content.title,
                "description": job.content.description,
                "tags": list(job.content.tags) + list(job.content.hashtags),
                "categoryId": job.content.category_id,
            },
        )
        quota_mod.add_units_on(session, conn.provider, quota_mod.COST_UPDATE, now)
        job.cancel_remote_outcome = f"set_{status.privacy_status}"
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_CANCELLED_REMOTE,
            message=f"The publication was cancelled on YouTube: video {job.video_id} is "
            f"now {status.privacy_status!r} with no publication time.",
        )
        return CancelOutcome(
            local_cancelled=True,
            remote_attempted=True,
            remote_succeeded=True,
            message=f"Cancelled locally, and the publication was cancelled on YouTube: "
            f"video {job.video_id} is now {status.privacy_status!r} with no "
            f"publication time.",
        )
    except ConnectorError as exc:
        job.cancel_remote_outcome = "remote_failed"
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_CANCEL_REMOTE_FAILED,
            message=f"YouTube did not accept the cancellation ({exc}). The local job is "
            f"cancelled, but video {job.video_id} may still publish at its original "
            f"time; check it by hand.",
            payload={"video_id": job.video_id},
        )
        session.flush()
        return CancelOutcome(
            local_cancelled=True,
            remote_attempted=True,
            remote_succeeded=False,
            message=f"Cancelled locally, but YouTube did not accept the change ({exc}). "
            f"Video {job.video_id} may still publish at its original time — please "
            f"check it in Studio.",
        )


# ----------------------------------------------------------------------
# Mention attestation
# ----------------------------------------------------------------------
#
# There is deliberately nothing here. ``confirm_mention`` and
# ``clear_mention_confirmation`` used to live in this module, and the copies in
# :mod:`hexton.api.routers.jobs` used to shadow :mod:`hexton.api.routers.mentions`
# (the jobs router is registered first, so FastAPI matched its paths first). That made
# ``POST /api/jobs/{id}/mention/confirm`` reachable with an empty body, which marked a
# mention VERIFIED with no attestation at all — the exact claim ``DF-19`` says must
# never be made without a human's words on record.
#
# The workflow now has exactly one owner: :mod:`hexton.services.mentions`. A
# duplicate here would be a second route to the one claim the whole design protects,
# so there is a test that scans the package for assignments of a verified state.


def find_job(session: Session, job_id: int) -> ScheduledJob | None:
    return session.get(ScheduledJob, job_id)


# ``list_jobs`` lived here until the jobs list endpoint was rewritten to filter, order
# and page in SQL (``F-20``). Nothing called it after that: it loaded every row, sorted
# them in Python and then sliced, so keeping it around was a standing invitation to
# reintroduce the O(table) list endpoint it had just been replaced by.
