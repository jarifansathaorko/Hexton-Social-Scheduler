"""Mention assessment.

``DF-19``: the ``videos`` resource exposes **no** mention entity. An ``@handle`` in a
caption is plain text to the API. The only documented route to ``VERIFIED`` is the
operator attesting in YouTube Studio that they performed it there.

The whole point of this module is that the presence of ``@something`` in a caption can
never become ``VERIFIED`` by accident, and that a ``videos.update`` cannot be issued on
a job whose mention lives in the description.

Pure functions. No I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .jobstate import MentionState

#: A bare ``@token`` in free text. Deliberately naive: it is evidence of *text*, and
#: nothing more. The lookbehind stops an email-like ``a@b`` being read as a handle.
_HANDLE_IN_TEXT = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{1,64})\b")


def extract_handles(text: str) -> tuple[str, ...]:
    """Naive text extraction. Never evidence that a mention was performed."""
    if not text:
        return ()
    return tuple(m.group(1) for m in _HANDLE_IN_TEXT.finditer(text))


@dataclass(frozen=True)
class MentionAssessment:
    state: MentionState
    handles_found: tuple[str, ...]
    reason: str

    @property
    def verified(self) -> bool:
        return self.state is MentionState.VERIFIED

    @property
    def requires_operator_action(self) -> bool:
        return self.state in (MentionState.MANUAL_REQUIRED, MentionState.PENDING_MANUAL)


def assess(
    *,
    mention_requested: bool,
    text: str = "",
    operator_confirmed_in_studio: bool = False,
    documented_api_mechanism_demonstrated: bool = False,
) -> MentionAssessment:
    """Classify the mention state of a job.

    ``documented_api_mechanism_demonstrated`` exists so that if YouTube ever documents
    a real mechanism it can be turned on in one place, with a link to the
    documentation pin that justifies it. It defaults to ``False`` and must never be
    set from the presence of text.
    """
    handles = extract_handles(text)

    if not mention_requested:
        return MentionAssessment(
            MentionState.NOT_REQUESTED,
            handles,
            "No mention was requested for this job.",
        )

    if documented_api_mechanism_demonstrated:
        return MentionAssessment(
            MentionState.VERIFIED,
            handles,
            "Verified by a documented API mechanism.",
        )

    if operator_confirmed_in_studio:
        return MentionAssessment(
            MentionState.VERIFIED,
            handles,
            "Marked VERIFIED by human attestation: the operator confirmed in YouTube "
            "Studio that the mention was added there. This is a human attestation "
            "recorded against the operator's account, not an API verification.",
        )

    if not handles:
        return MentionAssessment(
            MentionState.TEXT_ONLY,
            (),
            "A mention was requested but no @handle appears in the text, so nothing "
            "was sent anywhere.",
        )

    return MentionAssessment(
        MentionState.MANUAL_REQUIRED,
        handles,
        f"The @handle(s) {', '.join('@' + h for h in handles)} appear in the text, but "
        f"the mention cannot be verified through the documented API, because the videos "
        f"resource has no mention entity (DF-19). It must be added in YouTube Studio.",
    )


def may_publish(assessment: MentionAssessment) -> bool:
    """Whether a job with this assessment may proceed to publication.

    A requested-but-unconfirmed mention does **not** block the upload: the caption text
    is still worth uploading, and blocking would lose the operator's work. It only
    blocks the claim that the mention was performed.
    """
    return assessment.state in (MentionState.NOT_REQUESTED, MentionState.TEXT_ONLY)


# --------------------------------------------------------------------------
# The operator-facing workflow vocabulary
# --------------------------------------------------------------------------
#
# ``01-standard-prd.md`` §8.3 and ``02-technical-prd.md`` §6.5 ask for an explicit,
# operator-readable state. This map is the single translation from the canonical enum to
# those words. It is deliberately NOT a second enum: there is one state machine, and this
# is its label table, exactly as ``DISPLAY_MAP`` is the label table for ``JobState``.

MENTION_DISPLAY_MAP: dict[MentionState, str] = {
    MentionState.NOT_REQUESTED: "Not required",
    MentionState.TEXT_ONLY: "Not verified — @handle text only",
    MentionState.MANUAL_REQUIRED: "Manual confirmation required",
    MentionState.PENDING_MANUAL: "Pending manual action",
    MentionState.VERIFIED: "Confirmed by operator (human attestation)",
    MentionState.FAILED: "Failed or removed",
}

#: The ONLY states from which a report may claim a mention exists.
#:
#: ``DF-19`` makes this a one-element set and it must stay that way until Google
#: documents a mention entity. ``documented_api_mechanism_demonstrated`` is the switch
#: that would widen it, and it may only be flipped alongside the documentation pin that
#: justifies it.
MENTION_VERIFIED_STATES: frozenset[MentionState] = frozenset({MentionState.VERIFIED})

#: States that require the operator to do something in YouTube Studio.
MENTION_ACTIONABLE_STATES: frozenset[MentionState] = frozenset(
    {MentionState.MANUAL_REQUIRED, MentionState.PENDING_MANUAL, MentionState.TEXT_ONLY}
)


def mention_is_verified(state: MentionState) -> bool:
    """The single guard. A report, a UI chip, and a job may only claim a mention here."""
    return state in MENTION_VERIFIED_STATES


def mention_display(state: MentionState) -> str:
    """The operator-facing label for a mention state."""
    return MENTION_DISPLAY_MAP[state]


@dataclass(frozen=True)
class MentionWorkflow:
    """What the operator is being asked to do, in their words.

    ``04-feature-prd.md`` §10 FEAT-008 asks the app to *help* rather than to perform: show
    the intended creator identity, show the steps, and let the operator confirm. There is
    no private endpoint, no cookie extraction, and no browser automation anywhere in this
    workflow — the only actor is the operator, in YouTube Studio, by hand.
    """

    state: MentionState
    label: str
    #: The creator the operator should look for. Empty when nothing was extracted.
    intended_creator: str
    #: Ordered, plain instructions. Rendered verbatim.
    steps: tuple[str, ...]
    #: What the app will NOT do, stated plainly so the UI can show it.
    prohibitions: tuple[str, ...]


#: The app's own boundaries, shown wherever a mention workflow is shown.
_PROHIBITIONS = (
    "Hexton cannot create a creator mention through the API: the videos resource has no "
    "mention entity (DF-19).",
    "Hexton will not use private endpoints, browser-cookie extraction, or unapproved "
    "browser automation to create one.",
    "Hexton will not treat @handle text in a description as a created mention.",
)


def mention_workflow(
    state: MentionState, *, intended_creator: str = "", handles: tuple[str, ...] = ()
) -> MentionWorkflow:
    """Build the operator-facing instructions for ``state``."""
    creator = intended_creator or (f"@{handles[0]}" if handles else "")
    label = MENTION_DISPLAY_MAP[state]

    if state is MentionState.NOT_REQUESTED:
        steps: tuple[str, ...] = ()
    elif state is MentionState.TEXT_ONLY:
        steps = (
            "Open the video in YouTube Studio." + (f" Look for {creator}." if creator else ""),
            "Use the description or title editor's mention picker to select the creator; "
            "typing @handle as plain text does not create a mention.",
            "Return here and confirm.",
        )
    elif state is MentionState.MANUAL_REQUIRED:
        steps = (
            "Open the video in YouTube Studio." + (f" Look for {creator}." if creator else ""),
            "Select the creator with the mention picker in the description or title.",
            "Confirm below. Hexton records your confirmation as a human attestation, "
            "not as an API verification.",
        )
    elif state is MentionState.PENDING_MANUAL:
        steps = (
            "You have acknowledged this mention. Complete it in YouTube Studio.",
            "Use the mention picker to select "
            + (creator if creator else "the intended creator")
            + ".",
            "Confirm below when it is done.",
        )
    elif state is MentionState.VERIFIED:
        steps = (
            "Confirmed by your attestation. Hexton did not verify this through the API "
            "and cannot re-verify it later.",
            "If the video's description is rewritten, the mention may be removed — see "
            "the metadata-rewrite warning.",
        )
    else:  # FAILED
        steps = (
            "This mention was reported as removed or as having failed.",
            "Re-add it in YouTube Studio with the mention picker, then confirm again.",
        )

    return MentionWorkflow(
        state=state,
        label=label,
        intended_creator=creator,
        steps=steps,
        prohibitions=_PROHIBITIONS,
    )


# --------------------------------------------------------------------------
# Rewriting a description can destroy a mention the operator created by hand
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RemovalRisk:
    #: Whether the operator must make an explicit decision before the update runs.
    requires_decision: bool
    reason: str


#: The states in which a mention may physically exist on the video, having been created
#: by the operator in Studio. ``TEXT_ONLY`` is included because a rewrite would silently
#: destroy the text the operator wrote, even though that text was never a mention.
MENTION_DESTRUCTIBLE_STATES: frozenset[MentionState] = frozenset(
    {
        MentionState.MANUAL_REQUIRED,
        MentionState.PENDING_MANUAL,
        MentionState.VERIFIED,
        MentionState.TEXT_ONLY,
    }
)


def removal_risk(
    state: MentionState,
    *,
    description_changing: bool,
    new_description_contains_handle: bool,
) -> RemovalRisk:
    """Whether a metadata update risks destroying a mention the operator created.

    ``02-technical-prd.md`` §6.5 and ``AC-MVP-009``: a later ``videos.update`` can remove
    a manually created mention. The app must warn and must not rewrite the description
    without an explicit decision. This function is the warning; the API and the worker
    both consult it, and neither of them is permitted to skip it.
    """
    if not description_changing:
        return RemovalRisk(False, "The description is not being changed.")

    if state in (MentionState.VERIFIED, MentionState.PENDING_MANUAL, MentionState.MANUAL_REQUIRED):
        if new_description_contains_handle:
            return RemovalRisk(
                True,
                "This video's mention was created by hand in YouTube Studio and the API "
                "cannot see it (DF-19). Rewriting the description can remove it, and "
                "Hexton cannot detect that it is gone. Decide explicitly: either keep "
                "the current description, or confirm you accept losing the mention.",
            )
        return RemovalRisk(
            True,
            "This video's mention was created by hand in YouTube Studio and the API "
            "cannot see it (DF-19). A new description with no @handle would very likely "
            "remove it. Decide explicitly before continuing.",
        )

    if state is MentionState.TEXT_ONLY and not new_description_contains_handle:
        return RemovalRisk(
            True,
            "The operator's @handle text would be removed by this description rewrite. "
            "It was never a structured mention, but it is the operator's work.",
        )

    return RemovalRisk(False, "No mention is involved, so no description can be destroyed.")


def may_issue_videos_update(
    assessment: MentionState,
    *,
    update_includes_full_snippet: bool,
    description_changing: bool = False,
    new_description_contains_handle: bool = False,
    operator_accepted_mention_loss: bool = False,
) -> tuple[bool, str]:
    """Whether a ``videos.update`` is safe for this job.

    ``DF-06``: a request that omits a property which already has a value **deletes**
    that value. So a partial update on a job whose caption may carry a mention does
    not merely fail to preserve the mention — it destroys the description that holds
    it.

    ``AC-MVP-009``: even a *full-snippet* update can remove a mention the operator
    created by hand, because the API cannot read that mention back. That is why
    ``operator_accepted_mention_loss`` exists and why it may only be set by a human
    decision — never inferred, never defaulted to true.
    """
    risk = removal_risk(
        assessment,
        description_changing=description_changing,
        new_description_contains_handle=new_description_contains_handle,
    )
    if risk.requires_decision and not operator_accepted_mention_loss:
        return False, risk.reason

    if assessment is MentionState.MANUAL_REQUIRED and not operator_accepted_mention_loss:
        return (
            False,
            "This job's mention exists only in YouTube Studio. A videos.update could "
            "remove it (DF-06), so no update is issued; edit in Studio instead "
            "(FEAS-06, D-6).",
        )
    if not update_includes_full_snippet:
        return (
            False,
            "A videos.update that omits a property deletes its existing value (DF-06). "
            "Only a full-snippet update is permitted.",
        )
    if assessment in (MentionState.NOT_REQUESTED, MentionState.TEXT_ONLY):
        return True, "No mention is involved, so no description can be destroyed."
    return True, "A full-snippet update re-asserts every field, so no value is lost."
