"""The creator-mention workflow the operator actually drives.

``04-feature-prd.md`` §10 FEAT-008, ``01-standard-prd.md`` §8.3, ``AC-MVP-009``,
``DF-19``, ``FEAS-06``.

The API has no mention entity, so this module's entire job is to make a *human* action
trackable without ever pretending it was automated. Three rules it enforces, and they are
the ones most likely to be eroded:

1. **Only an attestation reaches VERIFIED.** :func:`confirm_mention` requires the
   operator to write, in their own words, what they did. That sentence is stored. A
   state can never be flipped to verified by an API response, a sync, a retry, or a
   migration — there is no other code path that writes ``VERIFIED``.
2. **Text is never evidence.** An ``@handle`` in a description is characters. It moves a
   mention *towards* "manual required", never towards "verified", and the label says so.
3. **A rewrite can destroy it, so a rewrite must be decided.** :func:`rewrite_risk` is
   consulted before any metadata update, and its answer is a blocking one.

No private endpoint, no cookie extraction, no browser automation. The operator, in
YouTube Studio, by hand — this module only keeps the ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from hexton.db import events as ev
from hexton.db.models import ScheduledJob
from hexton.domain.jobstate import JobState, MentionState
from hexton.domain.mention import (
    MentionWorkflow,
    RemovalRisk,
    assess,
    extract_handles,
    mention_display,
    mention_is_verified,
    mention_workflow,
    removal_risk,
)
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class MentionError(RuntimeError):
    """A mention operation that is not permitted. Blocking; never advisory."""

    def __init__(self, message: str, *, code: str = "not_permitted") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MentionView:
    """Everything the workflow panel needs, in one payload."""

    job_id: int
    state: str
    label: str
    requested: bool
    handles: tuple[str, ...]
    intended_creator: str
    #: Always true for ``VERIFIED``. The UI must render this, not hide it: an operator
    #: reading "Verified" without the qualifier would reasonably assume an API check.
    attestation_only: bool
    confirmed_at: datetime | None
    reason: str
    steps: tuple[str, ...]
    prohibitions: tuple[str, ...]
    rewrite_warning: str | None
    #: Whether the operator may act on this job at all right now.
    actionable: bool


def _text(job: ScheduledJob) -> str:
    content = job.content
    if content is None:
        return ""
    return f"{content.title}\n{content.description}"


#: The states the stored column is allowed to decide on its own. ``NOT_REQUESTED`` is
#: absent because it is the column's *default* — a job whose mention was requested but
#: whose row has never been through the workflow still reads ``not_requested``, and
#: trusting that would report "nothing to do" for a mention that is very much pending.
#: ``VERIFIED`` is absent for the reason this module exists: it is only ever granted
#: from an attestation timestamp, never from a column.
MENTION_RECORDED_STATES: frozenset[MentionState] = frozenset(
    {
        MentionState.TEXT_ONLY,
        MentionState.MANUAL_REQUIRED,
        MentionState.PENDING_MANUAL,
        MentionState.FAILED,
    }
)


def current_state(job: ScheduledJob) -> MentionState:
    """The mention state of ``job``, reconciled from what is actually stored.

    Three rules, in order, and the order is the point:

    1. ``VERIFIED`` is granted only when an attestation timestamp is on record. A
       stored column that says ``verified`` is a cache, and caches go stale — a bad
       migration, a restored backup, or a hand edit must not be able to reinstate a
       claim whose evidence was withdrawn.
    2. Any other *recorded* state is the operator's own decision and is trusted.
       ``PENDING_MANUAL`` and ``FAILED`` mean a person decided something, and
       recomputing them away from the caption text would silently erase that decision
       every time the panel opened.
    3. Otherwise the caption is assessed. The caption can only ever produce a state
       below ``VERIFIED``, so this fallback can never manufacture a claim.
    """
    content = job.content
    if content is None or not content.mention_requested:
        return MentionState.NOT_REQUESTED

    if content.mention_confirmed_at is not None:
        return MentionState.VERIFIED

    stored = content.mention_state
    if stored:
        try:
            candidate = MentionState(stored)
        except ValueError:  # pragma: no cover - a corrupted column
            candidate = None
        if candidate in MENTION_RECORDED_STATES:
            return candidate

    return assess(mention_requested=True, text=_text(job)).state


def view(session: Session, job: ScheduledJob, *, now: datetime | None = None) -> MentionView:
    """Build the workflow panel for ``job``."""
    content = job.content
    state = current_state(job)
    handles = tuple(content.mention_handles or ()) if content else extract_handles(_text(job))
    workflow: MentionWorkflow = mention_workflow(state, handles=handles)

    creator = handles[0] if handles else ""
    if content is not None and job.channel is not None and job.channel.handle:
        creator = job.channel.handle.lstrip("@")

    risk = removal_risk(
        state,
        # The question the panel answers: what happens if this description is rewritten
        # without an @handle in it? That is the default shape of any metadata update, so
        # it is the case worth warning about.
        description_changing=True,
        new_description_contains_handle=False,
    )
    return MentionView(
        job_id=job.id,
        state=state.value,
        label=mention_display(state),
        requested=bool(content and content.mention_requested),
        handles=handles,
        intended_creator=creator or workflow.intended_creator,
        attestation_only=mention_is_verified(state),
        confirmed_at=content.mention_confirmed_at if content else None,
        reason=(content.mention_reason if content else "")
        or assess(
            mention_requested=bool(content and content.mention_requested),
            text=_text(job),
        ).reason,
        steps=workflow.steps,
        prohibitions=workflow.prohibitions,
        rewrite_warning=risk.reason if risk.requires_decision else None,
        actionable=state is not MentionState.NOT_REQUESTED,
    )


def begin_manual(session: Session, job: ScheduledJob, *, now: datetime) -> None:
    """The operator acknowledged the workflow and is going to do it in Studio.

    Recorded separately from ``MANUAL_REQUIRED`` so a report can distinguish "nobody has
    looked at this" from "this is being worked on" — which are different situations with
    different urgency, and which a single state cannot tell apart.
    """
    content = job.content
    if content is None or not content.mention_requested:
        raise MentionError(
            "This job does not request a mention, so there is nothing to begin.",
            code="no_mention",
        )
    if content.mention_confirmed_at is not None:
        raise MentionError(
            "This mention is already confirmed. Clear the confirmation first if the video changed.",
            code="already_confirmed",
        )
    content.mention_state = MentionState.PENDING_MANUAL.value
    content.mention_reason = (
        "The operator has acknowledged the manual mention workflow and is completing it "
        "in YouTube Studio. Nothing has been verified."
    )
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_VALIDATED,
        message=content.mention_reason,
        payload={"mention_state": MentionState.PENDING_MANUAL.value},
    )
    session.flush()


def confirm_mention(
    session: Session, job: ScheduledJob, *, attestation: str, now: datetime
) -> None:
    """Record the operator's Studio attestation.

    ``DF-19``: there is no mention entity in the API, so this is the only route to a
    verified mention — and it is a **human attestation**, which the reason text always
    says. The attestation sentence is stored verbatim, so "did I actually confirm that?"
    is answerable later from the record rather than from memory.

    There is no code path here, or anywhere, that sets ``VERIFIED`` from an API response.
    """
    content = job.content
    if content is None or not content.mention_requested:
        raise MentionError("This job does not request a mention.", code="no_mention")
    words = (attestation or "").strip()
    if len(words) < 5:
        raise MentionError(
            "Write what you did in YouTube Studio, in a sentence. This is the only "
            "evidence Hexton will ever have that the mention exists, so it is recorded "
            "verbatim rather than as a checkbox.",
            code="attestation_required",
        )
    if job.state == JobState.DRAFT.value:
        raise MentionError(
            "Queue this job before confirming its mention: a draft has not been uploaded, "
            "so there is no video in YouTube Studio to mention anyone in.",
            code="not_uploaded",
        )

    content.mention_confirmed_at = now
    content.mention_state = MentionState.VERIFIED.value
    content.mention_reason = (
        "Marked VERIFIED by human attestation: the operator confirmed in YouTube Studio "
        "that the mention was added there. This is a recorded human decision recorded "
        f"against their account, not an API verification. Their words: {words!r}"
    )
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_VALIDATED,
        message=content.mention_reason,
        payload={"attestation": True, "attestation_text": words},
    )
    session.flush()


def report_removed(session: Session, job: ScheduledJob, *, now: datetime) -> None:
    """The operator says the mention is gone or never landed."""
    content = job.content
    if content is None or not content.mention_requested:
        raise MentionError("This job does not request a mention.", code="no_mention")
    content.mention_confirmed_at = None
    content.mention_state = MentionState.FAILED.value
    content.mention_reason = (
        "The operator reported that this mention was removed or did not take effect. "
        "Hexton cannot detect removal through the API (DF-19), so this state exists "
        "only because a human said so."
    )
    ev.record_event(
        session,
        job_id=job.id,
        kind=ev.KIND_NEEDS_REVIEW,
        message=content.mention_reason,
        payload={"mention_state": MentionState.FAILED.value},
    )
    session.flush()


def clear(session: Session, job: ScheduledJob) -> None:
    """Withdraw an attestation, e.g. when the operator edited the caption afterwards."""
    content = job.content
    if content is None:
        return
    content.mention_confirmed_at = None
    assessment = assess(mention_requested=content.mention_requested, text=_text(job))
    content.mention_state = assessment.state.value
    content.mention_reason = assessment.reason
    session.flush()


# --------------------------------------------------------------------------
# Rewriting a description can remove a mention the operator created by hand
# --------------------------------------------------------------------------


def rewrite_risk(
    job: ScheduledJob,
    *,
    description_changing: bool,
    new_description: str,
) -> RemovalRisk:
    """Whether changing this job's description risks the operator's manual mention.

    Consulted by the API before any metadata update, and by the worker before it
    re-asserts a snippet. Both must respect the answer: ``requires_decision`` means the
    operator has to say, explicitly, that they accept losing the mention — a default of
    "yes" would silently destroy work the API cannot even detect.
    """
    state = current_state(job)
    new_text_handles = extract_handles(new_description or "")
    return removal_risk(
        state,
        description_changing=description_changing,
        new_description_contains_handle=bool(new_text_handles),
    )


__all__ = [
    "MentionError",
    "MentionView",
    "begin_manual",
    "clear",
    "confirm_mention",
    "current_state",
    "removal_risk",
    "report_removed",
    "rewrite_risk",
    "view",
]
