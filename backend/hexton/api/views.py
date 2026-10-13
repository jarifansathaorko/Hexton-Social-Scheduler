"""Database rows to API contracts.

The presentation rules live here and nowhere else. In particular the *rendered status
string* comes from :func:`hexton.domain.jobstate.display_for`, which is the single
internal-state-to-UI-label mapping ``02`` §4 and ``05`` §C-21 require. The frontend
receives a finished label and must not derive one of its own, because a second mapping
is how three incompatible vocabularies got into the original PRDs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from hexton.api.schemas import JobOut
from hexton.db.models import RemoteObservation, ScheduledJob
from hexton.domain.jobstate import (
    DISPLAY_MAP,
    TERMINAL_STATES,
    JobState,
    MentionState,
    PollResult,
    RemoteState,
    can_retry,
    display_for,
    label_for,
    primary_action,
)
from hexton.domain.scheduling import ensure_utc


def _fmt(value: datetime | None, zone: ZoneInfo) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def latest_observation(job: ScheduledJob) -> RemoteObservation | None:
    observations = sorted(job.observations, key=lambda o: o.id)
    return observations[-1] if observations else None


def job_to_out(job: ScheduledJob, zone: ZoneInfo, *, now: datetime | None = None) -> JobOut:
    now = now or datetime.now(UTC)
    state = JobState(job.state)
    remote = latest_observation(job)

    remote_state = RemoteState.UNKNOWN.value
    privacy = processing = None
    if remote is not None:
        remote_state = remote.remote_state
        privacy = remote.privacy_status
        processing = remote.processing_status
    elif job.video_id is not None:
        # We have a video but have never polled it. That is "uploaded, not yet
        # observed" — not "scheduled", and certainly not "published".
        remote_state = RemoteState.UPLOADED_PROCESSING.value

    if state is JobState.PUBLISHED and remote_state != RemoteState.PUBLIC.value:
        # ``RemoteObservation`` rows are pruned after ``retention_days``
        # (``compliance.retention._prune_evidence``), so ninety days after a video
        # published the confirming observation is gone while the job row remains.
        # ``published_at_remote`` is written at the same instant as the PUBLISHED
        # transition and is *not* pruned, so it is the surviving record of the same
        # evidence.
        if job.published_at_remote is not None:
            remote_state = RemoteState.PUBLIC.value
            privacy = privacy or "public"
        else:
            # No observation and no persisted publication time. The state column says
            # PUBLISHED but nothing backs it, so this row may not be rendered as
            # "Published (verified)" — that is precisely the false publication claim
            # ``03`` §7.2 forbids. Render it as the unresolved record it is instead of
            # raising, because one unreadable row must not take the whole list
            # endpoint down.
            state = JobState.NEEDS_REVIEW
            remote_state = RemoteState.UNKNOWN.value

    publish_at = job.schedule.publish_at_utc
    poll = PollResult(job.poll_result) if job.poll_result else PollResult.NEVER_POLLED
    publish_passed = bool(publish_at and ensure_utc(publish_at) <= now)

    awaiting = (
        state in (JobState.SCHEDULED, JobState.UPLOADED)
        and publish_passed
        and poll is not PollResult.CONFIRMED
    )

    label = display_for(
        state,
        remote=RemoteState(remote_state),
        poll_result=poll,
        publish_at_passed=publish_passed,
        mention=(
            MentionState(job.content.mention_state)
            if job.content and job.content.mention_state
            else MentionState.NOT_REQUESTED
        ),
        progress_percent=job.progress_percent,
    )

    channel = job.channel
    media = job.media

    return JobOut(
        id=job.id,
        public_id=job.public_id,
        state=state.value,
        display_label=label,
        display_cue=DISPLAY_MAP[state].cue,
        primary_action=primary_action(state),
        can_retry=can_retry(state),
        can_cancel=state not in TERMINAL_STATES and state is not JobState.CANCELLED,
        title=job.content.title,
        description=job.content.description,
        tags=list(job.content.tags),
        hashtags=list(job.content.hashtags),
        category_id=job.content.category_id,
        privacy=job.content.privacy,
        mention_requested=job.content.mention_requested,
        mention_state=job.content.mention_state,
        mention_reason=job.content.mention_reason,
        mention_handles=list(job.content.mention_handles),
        mention_confirmed=job.content.mention_confirmed_at is not None,
        media_id=media.id,
        media_path=media.path,
        media_name=media.original_name or media.path.rsplit("/", 1)[-1],
        media_size_bytes=media.size_bytes,
        media_duration_seconds=media.duration_seconds,
        media_container=media.container,
        connection_id=job.connection_id,
        channel_identity_id=job.channel_identity_id,
        channel_title=channel.title if channel else None,
        channel_id=channel.channel_id if channel else None,
        publish_at_utc=publish_at,
        display_timezone=job.schedule.display_timezone,
        publish_at_display=_fmt(publish_at, zone),
        uploaded_at=job.uploaded_at,
        uploaded_at_display=_fmt(job.uploaded_at, zone),
        remote_state=remote_state,
        remote_privacy_status=privacy,
        remote_processing_status=processing,
        poll_result=job.poll_result,
        last_polled_at=job.last_polled_at,
        awaiting_verification=awaiting,
        progress_percent=job.progress_percent,
        video_id=job.video_id,
        video_url=job.video_url,
        failure_kind=job.failure_kind,
        failure_message=job.failure_message,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at,
        next_attempt_display=_fmt(job.next_attempt_at, zone),
        insert_may_have_succeeded=job.insert_may_have_succeeded,
        created_at=job.created_at,
        updated_at=job.updated_at,
        cancelled_at=job.cancelled_at,
    )


def display_label(state: JobState) -> str:
    """The short label for a state, for list filters and legends."""
    return label_for(state)
