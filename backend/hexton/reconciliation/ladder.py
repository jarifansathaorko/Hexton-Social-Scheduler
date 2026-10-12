"""The reconciliation ladder for an uncertain ``videos.insert``.

Phase 0 established three things this module encodes, and each one changes a decision
rather than being decorative (``08`` §7.2):

* **R0 is the only step that can ever authorise a second insert.** If no session URI
  was persisted and no request reached the provider, nothing was created, so retrying
  is genuinely safe.
* **R1 — a resumable-session probe — is conclusive about the body**, and therefore
  about whether to resume. It is *not* conclusive about which video was created: a
  completed session does not hand back a video ID.
* **R2 and R3 are evidence, never proof.** A video that matches our title, privacy
  and publication time might equally be a manual upload the operator made from
  another machine in the same minute. ``DF-08`` makes an activity-feed or
  uploads-playlist match "evidence that a video exists", and nothing more.

So the ladder has exactly one automatic outcome — *resume the same session* — and one
human outcome: *an operator looks at the candidate and says whether it is theirs*. It
never adopts a guessed video ID, because adopting the wrong ID would mark someone
else's upload as this schedule's publication and stop the job from ever running.

No I/O here: the caller performs the provider calls and passes the results in, so the
decision table is exhaustively testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from hexton.connectors.base import ProbeResult, VideoStatus


class Evidence(str, Enum):
    """How strongly a step's result supports a conclusion."""

    #: The provider answered directly about *this* request. May act without a human.
    CONCLUSIVE = "conclusive"
    #: Consistent with our request, and also consistent with an unrelated upload.
    CORROBORATING = "corroborating"
    #: Nothing could be determined.
    NONE = "none"


class StepOutcome(str, Enum):
    #: Continue the existing session. This is the only automatic action.
    RESUME = "resume"
    #: The upload finished and we have a video ID.
    RESOLVED = "resolved"
    #: The upload finished but we do not know which video it produced.
    UNRESOLVED = "unresolved"
    #: Nothing was ever sent, so a retry is genuinely safe.
    NOTHING_SENT = "nothing_sent"
    #: The provider could not be reached or gave a non-answer.
    INCONCLUSIVE = "inconclusive"
    #: A human must look at the candidates.
    NEEDS_OPERATOR = "needs_operator"


@dataclass(frozen=True)
class ReconcileResult:
    step: str
    outcome: StepOutcome
    evidence: Evidence
    video_id: str | None = None
    bytes_received: int | None = None
    #: True only when a *second* ``videos.insert`` provably cannot duplicate anything.
    safe_to_retry_insert: bool = False
    #: Candidate videos the operator must choose between. Never adopted automatically.
    candidates: tuple[str, ...] = ()
    reason: str = ""
    #: Extra facts worth persisting alongside the decision.
    facts: dict[str, object] = field(default_factory=dict)

    @property
    def requires_operator(self) -> bool:
        return self.outcome is StepOutcome.NEEDS_OPERATOR or bool(self.candidates)


def reconcile(
    *,
    has_session_uri: bool,
    has_video_id: bool,
    probe: ProbeResult | None = None,
    probe_error: str | None = None,
    known_video: VideoStatus | None = None,
    known_video_error: str | None = None,
    candidates: tuple[VideoStatus, ...] = (),
) -> ReconcileResult:
    """Run the ladder and return the single defensible next action.

    Order matters. R2 is checked before R3 because a persisted video ID is far
    stronger evidence than a recent-upload match, and R1 is checked before both when
    the body's fate is still unknown — resuming an existing session is the only
    outcome that neither creates a duplicate nor asks a human to decide.
    """
    # ------------------------------------------------------------------ R2
    if has_video_id and known_video is not None:
        return ReconcileResult(
            step="R2",
            outcome=StepOutcome.RESOLVED,
            evidence=Evidence.CONCLUSIVE,
            video_id=known_video.video_id,
            safe_to_retry_insert=False,
            reason=(
                f"The persisted video ID {known_video.video_id} resolves, so the upload "
                f"produced this video. Publication status is "
                f"{known_video.privacy_status!r}."
            ),
            facts={"privacy_status": known_video.privacy_status},
        )

    if has_video_id and known_video_error:
        # We believe we have an ID but cannot read it. That is not evidence of
        # absence: an expired token looks exactly like a deleted video.
        return ReconcileResult(
            step="R2",
            outcome=StepOutcome.INCONCLUSIVE,
            evidence=Evidence.NONE,
            safe_to_retry_insert=False,
            reason=(
                "A video ID is recorded but its status could not be read "
                f"({known_video_error}). An expired or revoked token produces exactly "
                f"the same symptom as a deleted video, so nothing is concluded and "
                f"nothing is retried automatically."
            ),
        )

    # ------------------------------------------------------------------ R1
    if has_session_uri and probe is not None:
        if not probe.completed and probe.bytes_received >= 0:
            return ReconcileResult(
                step="R1",
                outcome=StepOutcome.RESUME,
                evidence=Evidence.CONCLUSIVE,
                bytes_received=probe.bytes_received,
                safe_to_retry_insert=False,
                reason=(
                    f"The provider reports {probe.bytes_received} bytes received. "
                    f"Resuming the same session from that offset cannot create a "
                    f"duplicate, so this continues automatically."
                ),
            )
        if probe.completed:
            return ReconcileResult(
                step="R1",
                outcome=StepOutcome.UNRESOLVED,
                evidence=Evidence.CONCLUSIVE,
                bytes_received=probe.bytes_received,
                safe_to_retry_insert=False,
                reason=(
                    "The provider reports the body fully received, but a completed "
                    "session does not return a video ID. The upload finished; which "
                    "video it produced is not yet known."
                ),
            )

    if has_session_uri and probe_error:
        return ReconcileResult(
            step="R1",
            outcome=StepOutcome.INCONCLUSIVE,
            evidence=Evidence.NONE,
            safe_to_retry_insert=False,
            reason=(
                f"The session could not be probed ({probe_error}). An expired session "
                f"looks identical to a network fault from here, and restarting from "
                f"byte zero can create a duplicate, so this needs a human."
            ),
        )

    if has_session_uri:
        return ReconcileResult(
            step="R1",
            outcome=StepOutcome.INCONCLUSIVE,
            evidence=Evidence.NONE,
            safe_to_retry_insert=False,
            reason="A session URI is recorded but no probe result is available.",
        )

    # ------------------------------------------------------------------ R0
    if not has_video_id:
        return ReconcileResult(
            step="R0",
            outcome=StepOutcome.NOTHING_SENT,
            evidence=Evidence.CONCLUSIVE,
            safe_to_retry_insert=True,
            reason=(
                "No session URI and no video ID were ever recorded, so no "
                "videos.insert could have reached the provider. Retrying cannot "
                "duplicate anything."
            ),
        )

    return ReconcileResult(
        step="R0",
        outcome=StepOutcome.INCONCLUSIVE,
        evidence=Evidence.NONE,
        safe_to_retry_insert=False,
        reason="Inconsistent state: no session URI, but a video ID is recorded.",
    )


def classify_candidate(
    candidate: VideoStatus,
    *,
    title: str,
    privacy: str,
    publish_at: datetime | None,
    started_at: datetime,
    tolerance: timedelta = timedelta(minutes=5),
) -> bool:
    """Whether a remote video *could* be the one our request created.

    Used by R3 to narrow a list of recent uploads down to a handful for the operator.
    Deliberately strict, and deliberately not sufficient on its own: two matching
    videos prove nothing about which request created which.
    """
    if candidate.title is not None and title and candidate.title.strip() != title.strip():
        return False
    if privacy and candidate.privacy_status != privacy:
        return False
    return not (
        publish_at is not None
        and candidate.publish_at is not None
        and abs(candidate.publish_at - publish_at) > tolerance
    )


def operator_decision(result: ReconcileResult, *, chosen_video_id: str | None) -> ReconcileResult:
    """Fold an operator's confirmation back into a decision.

    ``chosen_video_id`` is only accepted when it is one of the candidates the ladder
    surfaced, so a UI cannot smuggle in an arbitrary ID and mark an unrelated video as
    this schedule's publication.
    """
    if chosen_video_id is None:
        return result
    # Membership is required, not merely checked when the list happens to be
    # non-empty. With no candidates at all the old test let *any* ID through, which
    # is exactly the DF-08 hole the docstring says this closes.
    if chosen_video_id not in result.candidates:
        raise ValueError(
            f"{chosen_video_id!r} is not one of the candidates the reconciliation "
            f"ladder found. Choosing an ID that was not surfaced would attribute an "
            f"unrelated upload to this schedule."
        )
    return ReconcileResult(
        step=result.step,
        outcome=StepOutcome.RESOLVED,
        evidence=Evidence.CONCLUSIVE,
        video_id=chosen_video_id,
        safe_to_retry_insert=False,
        candidates=result.candidates,
        reason=(
            f"The operator confirmed {chosen_video_id} as this schedule's video. This "
            f"is a recorded human decision, not an API verification."
        ),
        facts={**result.facts, "operator_confirmed": True},
    )
