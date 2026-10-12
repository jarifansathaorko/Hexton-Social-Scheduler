"""The single canonical job-state model, its transition rules, and its UI mapping.

Revision 1.0 of the PRDs carried three incompatible status vocabularies with no mapping
between them. This module is the *only* place those vocabularies are defined. The UI
renders :data:`DISPLAY_MAP` verbatim and must not introduce additional states.

Nothing here performs I/O. :mod:`hexton.domain` is importable with no dependencies
beyond the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class JobState(str, Enum):
    """Canonical LOCAL job state. One enum, no exceptions."""

    DRAFT = "JOB-DRAFT"
    READY = "JOB-READY"
    QUEUED = "JOB-QUEUED"
    UPLOADING = "JOB-UPLOADING"
    UPLOADED = "JOB-UPLOADED"
    SCHEDULED = "JOB-SCHEDULED"
    PUBLISHED = "JOB-PUBLISHED"
    FAILED_RETRY = "JOB-FAILED_RETRY"
    BLOCKED = "JOB-BLOCKED"
    MISSED_WINDOW = "JOB-MISSED_WINDOW"
    NEEDS_REVIEW = "JOB-NEEDS_REVIEW"
    CANCELLED = "JOB-CANCELLED"
    FAILED = "JOB-FAILED"


class RemoteState(str, Enum):
    """REMOTE YouTube state, kept as a separate record and never merged into JobState."""

    UNKNOWN = "remote.unknown"  # never polled
    NOT_UPLOADED = "remote.not_uploaded"
    UPLOADING = "remote.uploading"
    UPLOADED_PROCESSING = "remote.uploaded_processing"
    SCHEDULED = "remote.scheduled"
    PUBLIC = "remote.public"
    PRIVATE_UNLISTED_OR_OTHER = "remote.other_privacy"
    FAILED = "remote.failed"


class PollResult(str, Enum):
    """Outcome of the most recent publish-confirmation poll for a video."""

    CONFIRMED = "confirmed"
    NOT_YET = "not_yet"
    UNKNOWN = "unknown"  # could not determine (expired token, network, ...)
    NEVER_POLLED = "never_polled"


class MentionState(str, Enum):
    """Creator-mention state. ``VERIFIED`` is unreachable without a documented mechanism.

    ``DF-19``: the ``videos`` resource exposes no mention entity, so there is no API call
    that creates one. Every member below therefore describes a *human* situation, and the
    only route to ``VERIFIED`` is an operator attesting in YouTube Studio.
    """

    #: No mention was requested for this job.
    NOT_REQUESTED = "not_requested"
    #: A mention was requested and an ``@handle`` appears in the caption text. That text
    #: is evidence of characters, nothing more - it is explicitly **not** a mention.
    TEXT_ONLY = "text_only"
    #: A mention is required and the operator has not yet confirmed it in Studio.
    MANUAL_REQUIRED = "manual_required"
    #: The operator has been shown the manual workflow and acknowledged it, but has not
    #: yet confirmed completion. Distinct from ``MANUAL_REQUIRED`` so that the report can
    #: show "being worked on" separately from "nobody has looked at it".
    PENDING_MANUAL = "pending_manual"
    #: Confirmed **by human attestation only**. Never produced by an API response.
    VERIFIED = "verified"
    #: The mention was removed, or the operator reported that it failed.
    FAILED = "failed"


class FailureKind(str, Enum):
    """Classification of why a job cannot proceed. Drives retry policy."""

    NONE = "none"
    #: Transient and safe to retry automatically (network, 5xx, rate limit).
    TRANSIENT = "transient"
    #: Authorization is gone or invalid; only the operator can act.
    AUTHORIZATION = "authorization"
    #: Quota exhausted; retries are pointless until the window resets.
    QUOTA = "quota"
    #: A permanent rejection that will not succeed on retry.
    PERMANENT = "permanent"
    #: The remote outcome of the previous attempt is unknown; must be reconciled.
    AMBIGUOUS = "ambiguous"
    #: Local validation failed before any remote call.
    VALIDATION = "validation"
    #: A human must look at it (uncertain outcome, conflicting evidence).
    HUMAN_REQUIRED = "human_required"


#: States from which no further automated progress is possible.
TERMINAL_STATES = frozenset({JobState.PUBLISHED, JobState.CANCELLED, JobState.FAILED})

#: States in which the outcome of a previous ``videos.insert`` may be indeterminate.
AMBIGUOUS_STATES = frozenset({JobState.NEEDS_REVIEW})

#: The worker may claim a job only in one of these states.
CLAIMABLE_STATES = frozenset(
    {JobState.READY, JobState.QUEUED, JobState.FAILED_RETRY, JobState.UPLOADING}
)


@dataclass(frozen=True)
class Display:
    label: str
    cue: str
    never_implies: str


#: The ONLY internal-state -> UI-label mapping. ``05-visual-uiux-prd.md`` renders these
#: verbatim. ``{n}`` in a label is replaced with the completion percentage.
DISPLAY_MAP: dict[JobState, Display] = {
    JobState.DRAFT: Display("Draft", "grey outline", "validation passed"),
    JobState.READY: Display("Ready to queue", "grey outline", "queued or uploaded"),
    JobState.QUEUED: Display("Queued locally", "blue outline", "YouTube has accepted a schedule"),
    JobState.UPLOADING: Display("Uploading (n%)", "blue progress", "publication is imminent"),
    JobState.UPLOADED: Display("Uploaded — processing", "blue filled", "published"),
    JobState.SCHEDULED: Display("Scheduled on YouTube", "green outline, clock", "already public"),
    JobState.PUBLISHED: Display("Published (verified)", "green filled", "—"),
    JobState.FAILED_RETRY: Display("Retry waiting", "amber", "permanently failed"),
    JobState.BLOCKED: Display(
        "Authorization required / Quota blocked / Policy blocked",
        "amber, explicit reason",
        "recoverable automatically",
    ),
    JobState.MISSED_WINDOW: Display(
        "Missed window — action required", "amber", "silently published late"
    ),
    JobState.NEEDS_REVIEW: Display(
        "Needs review — upload outcome uncertain", "red warning", "retrying is safe"
    ),
    JobState.CANCELLED: Display("Cancelled", "grey struck", "deleted from YouTube"),
    JobState.FAILED: Display("Failed", "red filled", "—"),
}

#: Awaiting-verification suffix. Layered on top of a state; never replaces one.
AWAITING_VERIFICATION_SUFFIX = "— publication awaiting verification"


# --------------------------------------------------------------------------
# The one legal state machine.
# --------------------------------------------------------------------------

#: ``from -> {to, ...}``. Any transition not listed here is illegal and must be
#: rejected by the API and by the worker alike. Keeping this in one table is what
#: stops a fourth state machine from appearing in a new module.
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.DRAFT: frozenset({JobState.READY, JobState.CANCELLED, JobState.BLOCKED}),
    JobState.READY: frozenset(
        {JobState.QUEUED, JobState.DRAFT, JobState.CANCELLED, JobState.BLOCKED}
    ),
    JobState.QUEUED: frozenset(
        {
            JobState.UPLOADING,
            JobState.CANCELLED,
            JobState.BLOCKED,
            JobState.MISSED_WINDOW,
            JobState.FAILED_RETRY,
            # Exhausting the retry budget can be discovered on the very first
            # attempt, before anything has been uploaded at all.
            JobState.FAILED,
        }
    ),
    JobState.UPLOADING: frozenset(
        {
            # An upload that carries a ``publishAt`` lands here directly: the bytes
            # are on YouTube and the publication time is set, so there is no
            # intermediate "uploaded" moment worth modelling. Going via UPLOADED
            # first would be a state the job never actually occupies.
            JobState.SCHEDULED,
            JobState.UPLOADED,
            JobState.FAILED_RETRY,
            JobState.NEEDS_REVIEW,
            JobState.BLOCKED,
            JobState.FAILED,
        }
    ),
    JobState.UPLOADED: frozenset(
        {JobState.SCHEDULED, JobState.PUBLISHED, JobState.NEEDS_REVIEW, JobState.BLOCKED}
    ),
    JobState.SCHEDULED: frozenset(
        {JobState.PUBLISHED, JobState.NEEDS_REVIEW, JobState.MISSED_WINDOW, JobState.BLOCKED}
    ),
    JobState.PUBLISHED: frozenset(),
    JobState.FAILED_RETRY: frozenset(
        {JobState.QUEUED, JobState.UPLOADING, JobState.CANCELLED, JobState.BLOCKED, JobState.FAILED}
    ),
    JobState.BLOCKED: frozenset({JobState.QUEUED, JobState.READY, JobState.CANCELLED}),
    JobState.MISSED_WINDOW: frozenset({JobState.QUEUED, JobState.CANCELLED, JobState.NEEDS_REVIEW}),
    #: The only escape from an uncertain outcome is an operator resolving it.
    #:
    #: ``QUEUED`` is included and is *not* the same act as adopting a video. Adopting
    #: says "that upload was mine"; requeueing says "I checked and there was no upload
    #: at all", which is the only reading under which a second ``videos.insert`` cannot
    #: duplicate anything. It is deliberately absent from the automatic ladder —
    #: ``NEEDS_REVIEW`` is reachable only from an ambiguous outcome, so no code path may
    #: ever pick it — and the API records it as an explicit operator decision.
    JobState.NEEDS_REVIEW: frozenset(
        {
            JobState.SCHEDULED,
            JobState.PUBLISHED,
            JobState.CANCELLED,
            JobState.FAILED,
            JobState.QUEUED,
        }
    ),
    JobState.CANCELLED: frozenset(),
    JobState.FAILED: frozenset(),
}


def can_transition(current: JobState, target: JobState) -> bool:
    """True when ``current -> target`` is a legal transition."""
    return target in TRANSITIONS[current]


def assert_transition(current: JobState, target: JobState) -> None:
    """Raise :class:`InvalidTransition` when the move is not in :data:`TRANSITIONS`."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


class InvalidTransition(ValueError):
    """Raised when a state change is not in the canonical transition table."""

    def __init__(self, current: JobState, target: JobState) -> None:
        allowed = ", ".join(sorted(s.value for s in TRANSITIONS[current])) or "(none)"
        super().__init__(
            f"Illegal job transition {current.value} -> {target.value}. "
            f"Permitted from {current.value}: {allowed}."
        )
        self.current = current
        self.target = target


def terminal_reason(state: JobState) -> str | None:
    """Why the worker must stop touching this job, or ``None`` when it may continue."""
    if state in TERMINAL_STATES:
        return f"{state.value} is terminal; no further automated action is permitted."
    return None


def video_has_been_public(
    state: JobState,
    published_at_remote: datetime | None,
    observed_privacy: str | None,
) -> bool:
    """Whether ``publishAt`` has stopped being honoured for this video.

    ``DF-03``: ``publishAt`` applies only to a video that has **never been
    published**. Both callers that need this used to write
    ``has_been_public = job.video_id is None``, which is wrong in the direction that
    matters: a video created *with* a ``publishAt`` has a video ID while remaining
    private, so the wrong test refused to reschedule precisely the scheduled jobs it
    should have been allowed to move. A persisted ID says the upload happened; it says
    nothing about whether the video was ever public.

    Evidence considered, in order: the canonical state, the remote publication
    timestamp recorded by the confirmation sweep, and the most recent observation.
    """
    if state is JobState.PUBLISHED or published_at_remote is not None:
        return True
    return observed_privacy == "public"


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


def label_for(state: JobState) -> str:
    return DISPLAY_MAP[state].label


def display_for(
    state: JobState,
    *,
    remote: RemoteState = RemoteState.UNKNOWN,
    poll_result: PollResult = PollResult.NEVER_POLLED,
    publish_at_passed: bool = False,
    mention: MentionState = MentionState.NOT_REQUESTED,
    progress_percent: int | None = None,
) -> str:
    """Produce the final rendered status string.

    Rules encoded here, and only here:

    * ``PUBLISHED`` is only reachable with an observed remote ``public`` status.
    * A scheduled job whose publish time passed without a confirming poll is *awaiting
      verification* — not published, and not failed.
    * ``NEEDS_REVIEW`` is never labelled as offering a retry.
    * A mention chip is layered on, independent of job state.
    """
    if state is JobState.PUBLISHED and remote is not RemoteState.PUBLIC:
        raise ValueError("JOB-PUBLISHED requires an observed remote privacy_status of 'public'")

    text = label_for(state)
    if state is JobState.UPLOADING and progress_percent is not None:
        text = text.replace("n%", f"{max(0, min(100, progress_percent))}%")

    if (
        state is JobState.SCHEDULED
        and publish_at_passed
        and poll_result in (PollResult.UNKNOWN, PollResult.NEVER_POLLED, PollResult.NOT_YET)
    ):
        text = f"{text} {AWAITING_VERIFICATION_SUFFIX}"

    if mention in (MentionState.MANUAL_REQUIRED, MentionState.PENDING_MANUAL):
        text = f"{text} · mention needs manual action"

    return text


def can_retry(state: JobState) -> bool:
    """``Retry`` must never be offered where the previous insert may have succeeded."""
    if state in (JobState.NEEDS_REVIEW, JobState.BLOCKED):
        return False
    return state not in TERMINAL_STATES


def primary_action(state: JobState) -> str:
    if state is JobState.NEEDS_REVIEW:
        return "Resolve"
    if state is JobState.BLOCKED:
        return "Fix authorization"
    if state in (JobState.FAILED_RETRY, JobState.MISSED_WINDOW):
        return "Retry"
    if state is JobState.QUEUED:
        return "Cancel"
    return "View"


def retryable_failure(state: JobState, kind: FailureKind) -> bool:
    """Whether a failure of ``kind`` may be retried automatically from ``state``.

    This is the single gate the worker consults. It exists so that a second
    ``videos.insert`` can never be issued for a job whose first attempt may already
    have been accepted remotely.
    """
    if state in AMBIGUOUS_STATES or kind in (FailureKind.AMBIGUOUS, FailureKind.HUMAN_REQUIRED):
        return False
    if state in TERMINAL_STATES:
        return False
    if kind is FailureKind.PERMANENT:
        return False
    if kind is FailureKind.AUTHORIZATION:
        return False
    return kind in (FailureKind.TRANSIENT, FailureKind.QUOTA)
