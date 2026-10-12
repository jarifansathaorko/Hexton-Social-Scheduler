"""The reconciliation ladder — the decision table behind duplicate prevention.

``08`` §7.2: R0 is the only step that can ever authorise a second ``videos.insert``;
R1 is conclusive about the body but not about which video it produced; R2 and R3 are
evidence for a human, never proof. These tests pin each of those claims, because a
regression here is a duplicate video on someone's channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hexton.connectors.base import ProbeResult, VideoStatus
from hexton.reconciliation.ladder import (
    Evidence,
    ReconcileResult,
    StepOutcome,
    classify_candidate,
    operator_decision,
    reconcile,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _status(video_id: str = "vid-1", **kwargs) -> VideoStatus:
    defaults = {
        "video_id": video_id,
        "privacy_status": "private",
        "upload_status": "uploaded",
        "processing_status": None,
    }
    defaults.update(kwargs)
    return VideoStatus(**defaults)


# ----------------------------------------------------------------------#
# R2 — a persisted video ID
# ----------------------------------------------------------------------#


def test_a_persisted_id_that_resolves_is_conclusive() -> None:
    result = reconcile(has_session_uri=True, has_video_id=True, known_video=_status("vid-9"))
    assert result.step == "R2"
    assert result.outcome is StepOutcome.RESOLVED
    assert result.evidence is Evidence.CONCLUSIVE
    assert result.video_id == "vid-9"
    assert result.safe_to_retry_insert is False


def test_a_video_id_that_cannot_be_read_is_inconclusive_not_absent() -> None:
    """An expired token produces exactly the same symptom as a deleted video."""
    result = reconcile(
        has_session_uri=True, has_video_id=True, known_video_error="401 unauthorized"
    )
    assert result.step == "R2"
    assert result.outcome is StepOutcome.INCONCLUSIVE
    assert result.evidence is Evidence.NONE
    assert result.safe_to_retry_insert is False
    assert result.video_id is None
    assert "expired or revoked token" in result.reason


# ----------------------------------------------------------------------#
# R1 — the session probe
# ----------------------------------------------------------------------#


def test_a_partially_received_body_resumes_automatically() -> None:
    result = reconcile(
        has_session_uri=True, has_video_id=False, probe=ProbeResult(bytes_received=4096)
    )
    assert result.step == "R1"
    assert result.outcome is StepOutcome.RESUME
    assert result.bytes_received == 4096
    assert result.safe_to_retry_insert is False
    assert result.requires_operator is False


def test_a_completed_body_without_an_id_is_unresolved_and_never_retried() -> None:
    """A completed session does not hand back a video ID — the classic ambiguity."""
    result = reconcile(
        has_session_uri=True,
        has_video_id=False,
        probe=ProbeResult(bytes_received=100, completed=True),
    )
    assert result.outcome is StepOutcome.UNRESOLVED
    assert result.video_id is None
    assert result.safe_to_retry_insert is False
    assert "does not return a video ID" in result.reason


def test_a_failed_probe_never_authorises_a_restart_from_zero() -> None:
    result = reconcile(
        has_session_uri=True, has_video_id=False, probe_error="session expired (404)"
    )
    assert result.outcome is StepOutcome.INCONCLUSIVE
    assert result.safe_to_retry_insert is False
    assert "needs a human" in result.reason


def test_a_session_with_no_probe_result_is_inconclusive() -> None:
    result = reconcile(has_session_uri=True, has_video_id=False)
    assert result.step == "R1"
    assert result.outcome is StepOutcome.INCONCLUSIVE
    assert result.safe_to_retry_insert is False


# ----------------------------------------------------------------------#
# R0 — nothing was ever sent
# ----------------------------------------------------------------------#


def test_r0_is_the_only_step_that_authorises_a_second_insert() -> None:
    result = reconcile(has_session_uri=False, has_video_id=False)
    assert result.step == "R0"
    assert result.outcome is StepOutcome.NOTHING_SENT
    assert result.evidence is Evidence.CONCLUSIVE
    assert result.safe_to_retry_insert is True


def test_an_id_without_a_session_is_an_inconsistent_state_not_a_retry() -> None:
    result = reconcile(has_session_uri=False, has_video_id=True)
    assert result.outcome is StepOutcome.INCONCLUSIVE
    assert result.safe_to_retry_insert is False


def test_only_r0_can_ever_be_safe_to_retry() -> None:
    """A single assertion standing in for the whole duplicate-prevention argument."""
    combinations = [
        {"has_session_uri": s, "has_video_id": v, "probe": p, "known_video": k}
        for s in (False, True)
        for v in (False, True)
        for p in (None, ProbeResult(bytes_received=1), ProbeResult(5, completed=True))
        for k in (None, _status())
    ]
    for combo in combinations:
        result = reconcile(**combo)
        if result.safe_to_retry_insert:
            assert result.step == "R0"


# ----------------------------------------------------------------------#
# R3 — candidates are evidence, never proof
# ----------------------------------------------------------------------#


def test_a_title_mismatch_rules_a_candidate_out() -> None:
    assert (
        classify_candidate(
            _status(title="Something else"),
            title="My title",
            privacy="private",
            publish_at=None,
            started_at=NOW,
        )
        is False
    )


def test_a_privacy_mismatch_rules_a_candidate_out() -> None:
    assert (
        classify_candidate(
            _status(privacy_status="public"),
            title="t",
            privacy="private",
            publish_at=None,
            started_at=NOW,
        )
        is False
    )


def test_a_publish_time_outside_the_tolerance_rules_a_candidate_out() -> None:
    near = _status(publish_at=NOW + timedelta(minutes=1))
    far = _status(publish_at=NOW + timedelta(minutes=30))
    kwargs = {"title": "t", "privacy": "private", "publish_at": NOW, "started_at": NOW}
    assert classify_candidate(near, **kwargs) is True
    assert classify_candidate(far, **kwargs) is False


def test_a_matching_candidate_is_still_only_evidence() -> None:
    """Two matching videos prove nothing about which request created which."""
    assert (
        classify_candidate(
            _status(title="t"),
            title="t",
            privacy="private",
            publish_at=None,
            started_at=NOW,
        )
        is True
    )


# ----------------------------------------------------------------------#
# Operator decisions
# ----------------------------------------------------------------------#


def test_an_operator_may_confirm_a_surfaced_candidate() -> None:
    result = ReconcileResult(
        step="R3",
        outcome=StepOutcome.NEEDS_OPERATOR,
        evidence=Evidence.CORROBORATING,
        candidates=("vid-a", "vid-b"),
        reason="two matches",
    )
    resolved = operator_decision(result, chosen_video_id="vid-b")
    assert resolved.outcome is StepOutcome.RESOLVED
    assert resolved.video_id == "vid-b"
    assert resolved.facts["operator_confirmed"] is True
    assert "human decision" in resolved.reason


def test_an_id_that_was_not_surfaced_is_refused() -> None:
    """Otherwise a UI could smuggle in any ID and mark an unrelated video as ours."""
    result = ReconcileResult(
        step="R3",
        outcome=StepOutcome.NEEDS_OPERATOR,
        evidence=Evidence.CORROBORATING,
        candidates=("vid-a",),
        reason="one match",
    )
    with pytest.raises(ValueError, match="not one of the candidates"):
        operator_decision(result, chosen_video_id="vid-z")


def test_declining_leaves_the_result_untouched() -> None:
    result = ReconcileResult(
        step="R3",
        outcome=StepOutcome.NEEDS_OPERATOR,
        evidence=Evidence.CORROBORATING,
        candidates=("vid-a",),
        reason="one match",
    )
    assert operator_decision(result, chosen_video_id=None) is result


def test_requires_operator_is_true_whenever_candidates_exist() -> None:
    result = ReconcileResult(
        step="R3",
        outcome=StepOutcome.UNRESOLVED,
        evidence=Evidence.CORROBORATING,
        candidates=("vid-a",),
        reason="",
    )
    assert result.requires_operator is True
