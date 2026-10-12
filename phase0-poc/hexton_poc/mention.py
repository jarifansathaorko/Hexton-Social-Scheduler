"""Rules for genuine creator mentions.

The documented `videos` resource exposes ``snippet.title``, ``snippet.description``,
``snippet.tags[]``, ``localizations`` and ``status`` - and **no mention entity**
(``docfacts.DF-19``). YouTube's own help describes a mention as typing ``@`` and picking a
channel from a recommended list, which is a UI affordance with no documented API
counterpart.

Consequence encoded here: the presence of an ``@handle`` string in a caption proves
nothing, and this module refuses to let anything turn such a string into ``VERIFIED``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .jobstate import MentionState

# A bare @token in free text. Deliberately naive: it is evidence of *text*, nothing more.
# The lookbehind stops an email-like `a@b` from being read as a handle.
_HANDLE_IN_TEXT = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{1,64})\b")


@dataclass(frozen=True)
class MentionEvidence:
    """What we can actually assert about a mention."""

    state: MentionState
    reason: str
    handles_found: tuple[str, ...] = ()


def extract_handles(text: str) -> tuple[str, ...]:
    """Return @handle-like tokens present in ``text``.

    This is *text extraction*, not mention detection. Callers must never pass the result
    straight to :func:`assess`.
    """
    return tuple(_HANDLE_IN_TEXT.findall(text or ""))


def assess(
    *,
    mention_requested: bool,
    text: str,
    documented_api_mechanism_demonstrated: bool = False,
    operator_confirmed_in_studio: bool = False,
) -> MentionEvidence:
    """Decide what may be claimed about a mention.

    ``documented_api_mechanism_demonstrated`` defaults to ``False`` and may only be set to
    ``True`` once an actual, documented API field that creates a selected mention has been
    demonstrated. As of ``docfacts.DF-19`` no such field exists, so the only route to
    ``VERIFIED`` in practice is ``operator_confirmed_in_studio`` for a workflow that the
    operator performed themselves.

    Note what this deliberately does *not* do: it never inspects ``text`` for ``@`` and
    upgrades anything to ``VERIFIED``.
    """
    handles = extract_handles(text)

    if not mention_requested:
        return MentionEvidence(MentionState.NOT_REQUESTED, "No mention was requested.", handles)

    if documented_api_mechanism_demonstrated:
        return MentionEvidence(
            MentionState.VERIFIED,
            "A documented API mechanism that creates and verifies the selected mention has "
            "been demonstrated.",
            handles,
        )

    if operator_confirmed_in_studio:
        return MentionEvidence(
            MentionState.VERIFIED,
            "The operator added the mention through YouTube Studio and confirmed it. This is "
            "a human attestation, not an API verification, and the UI must say so.",
            handles,
        )

    if handles:
        return MentionEvidence(
            MentionState.MANUAL_REQUIRED,
            f"Found {len(handles)} @handle token(s) in the text. An @handle in a caption is "
            "plain text: it does not notify anyone and cannot be verified through the "
            "documented API. Hold the job for a guided Studio step before publication.",
            handles,
        )

    return MentionEvidence(
        MentionState.TEXT_ONLY,
        "A mention was requested but no @handle text is present either.",
        handles,
    )


def may_publish(evidence: MentionEvidence) -> bool:
    """A job requiring a real mention must not go public before that mention exists."""
    return evidence.state in (MentionState.NOT_REQUESTED, MentionState.VERIFIED)


def may_issue_videos_update(
    evidence: MentionEvidence,
    *,
    update_includes_full_snippet: bool,
) -> tuple[bool, str]:
    """Whether it is safe to send a `videos.update` for this job.

    ``docfacts.DF-06``: a `videos.update` **deletes** any property that already has a value
    and is not specified in the request. So an update that omits ``snippet.description``
    destroys the description - and with it any Studio-created mention living there.

    This is why the conservative answer for a manual-mention job is "do not update".
    """
    if evidence.state is MentionState.MANUAL_REQUIRED:
        return False, (
            "This job's mention exists only in YouTube Studio. A videos.update could remove "
            "it (DF-06), so no update is issued; edit in Studio instead (FEAS-06, D-6)."
        )
    if evidence.state is MentionState.VERIFIED and not update_includes_full_snippet:
        return False, (
            "Refusing a partial videos.update: unspecified properties with existing values "
            "are deleted (DF-06), which would destroy the description containing the mention."
        )
    if not update_includes_full_snippet:
        return True, "Partial update allowed: no mention lives in the affected metadata."
    return True, "Full snippet supplied; existing values are re-asserted."
