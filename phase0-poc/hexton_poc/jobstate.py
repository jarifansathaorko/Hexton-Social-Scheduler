"""The single canonical job-state model, plus the single internal-state -> UI-label mapping.

Source of truth for the vocabulary: ``02-technical-prd.md`` section 4.

Revision 1.0 of the PRDs carried three incompatible status vocabularies (schedule state
machine, job state machine, feature-PRD status list) with no mapping between them. This
module is the *only* place those vocabularies are defined. The UI renders
:data:`DISPLAY_MAP` verbatim and must not introduce additional states.

Nothing here performs I/O. It exists so the rules are testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    UNKNOWN = "remote.unknown"                # never polled
    NOT_UPLOADED = "remote.not_uploaded"
    UPLOADING = "remote.uploading"
    UPLOADED_PROCESSING = "remote.uploaded_processing"
    SCHEDULED = "remote.scheduled"
    PUBLIC = "remote.public"
    PRIVATE_UNLISTED_OR_OTHER = "remote.other_privacy"
    FAILED = "remote.failed"


class PollResult(str, Enum):
    """Outcome of the most recent PublishConfirmationSweep poll for a video."""

    CONFIRMED = "confirmed"
    NOT_YET = "not_yet"
    UNKNOWN = "unknown"          # could not determine (e.g. expired token, network)
    NEVER_POLLED = "never_polled"


class MentionState(str, Enum):
    """Mention state. ``VERIFIED`` is unreachable without a documented mechanism."""

    NOT_REQUESTED = "not_requested"
    TEXT_ONLY = "text_only"
    MANUAL_REQUIRED = "manual_required"
    VERIFIED = "verified"
    FAILED = "failed"


TERMINAL_STATES = frozenset(
    {JobState.PUBLISHED, JobState.CANCELLED, JobState.FAILED}
)

#: States in which the outcome of a previous `videos.insert` may be indeterminate.
AMBIGUOUS_STATES = frozenset({JobState.NEEDS_REVIEW})


@dataclass(frozen=True)
class Display:
    label: str
    cue: str
    never_implies: str


#: The ONLY internal-state -> UI-label mapping. `05-visual-uiux-prd.md` renders these verbatim.
DISPLAY_MAP: dict[JobState, Display] = {
    JobState.DRAFT: Display("Draft", "grey outline", "validation passed"),
    JobState.READY: Display("Ready to queue", "grey outline", "queued or uploaded"),
    JobState.QUEUED: Display("Queued locally", "blue outline", "YouTube has accepted a schedule"),
    JobState.UPLOADING: Display("Uploading (n%)", "blue progress", "publication is imminent"),
    JobState.UPLOADED: Display("Uploaded — processing", "blue filled", "published"),
    JobState.SCHEDULED: Display("Scheduled on YouTube", "green outline, clock", "already public"),
    JobState.PUBLISHED: Display("Published (verified)", "green filled", "—"),
    JobState.FAILED_RETRY: Display("Retry waiting", "amber", "permanently failed"),
    JobState.BLOCKED: Display("Authorization required / Quota blocked / Policy blocked",
                              "amber, explicit reason", "recoverable automatically"),
    JobState.MISSED_WINDOW: Display("Missed window — action required", "amber",
                                    "silently published late"),
    JobState.NEEDS_REVIEW: Display("Needs review — upload outcome uncertain", "red warning",
                                   "retrying is safe"),
    JobState.CANCELLED: Display("Cancelled", "grey struck", "deleted from YouTube"),
    JobState.FAILED: Display("Failed", "red filled", "—"),
}

#: Awaiting-verification suffix. Layered on top of a state; never replaces one.
AWAITING_VERIFICATION_SUFFIX = "— publication awaiting verification"


def label_for(state: JobState) -> str:
    return DISPLAY_MAP[state].label


def display_for(
    state: JobState,
    *,
    remote: RemoteState = RemoteState.UNKNOWN,
    poll_result: PollResult = PollResult.NEVER_POLLED,
    publish_at_passed: bool = False,
    mention: MentionState = MentionState.NOT_REQUESTED,
) -> str:
    """Produce the final rendered status string.

    Rules encoded here (and only here):

    * ``PUBLISHED`` is only reachable with an observed remote ``public`` status.
    * A scheduled job whose publish time passed without a confirming poll is *awaiting
      verification*, not published and not failed.
    * ``NEEDS_REVIEW`` is never labelled as offering a retry.
    * A mention chip is layered on, independent of job state.
    """
    if state is JobState.PUBLISHED and remote is not RemoteState.PUBLIC:
        # Defence in depth: never claim publication without remote evidence.
        raise ValueError(
            "JOB-PUBLISHED requires an observed remote privacy_status of 'public'"
        )

    text = label_for(state)

    if (
        state is JobState.SCHEDULED
        and publish_at_passed
        and poll_result in (PollResult.UNKNOWN, PollResult.NEVER_POLLED, PollResult.NOT_YET)
    ):
        text = f"{text} {AWAITING_VERIFICATION_SUFFIX}"

    if mention is MentionState.MANUAL_REQUIRED:
        text = f"{text} · mention needs manual action"

    return text


def can_retry(state: JobState) -> bool:
    """`Retry` must never be offered where the previous insert may have succeeded."""
    if state is JobState.NEEDS_REVIEW:
        return False
    if state is JobState.BLOCKED:
        return False
    if state in TERMINAL_STATES:
        return False
    return True


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
