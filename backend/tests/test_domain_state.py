"""Canonical state machine tests.

The single most important property in this application is that there is exactly one
state machine. These tests pin the whole transition table, the display mapping, and
the two rules that stop a job being marked published on hope.
"""

from __future__ import annotations

import pytest
from hexton.domain.jobstate import (
    AMBIGUOUS_STATES,
    CLAIMABLE_STATES,
    DISPLAY_MAP,
    TERMINAL_STATES,
    FailureKind,
    InvalidTransition,
    JobState,
    MentionState,
    PollResult,
    RemoteState,
    assert_transition,
    can_retry,
    can_transition,
    display_for,
    label_for,
    primary_action,
    retryable_failure,
    terminal_reason,
)


def test_every_state_has_a_display_entry() -> None:
    assert set(DISPLAY_MAP) == set(JobState)


def test_every_state_is_accounted_for() -> None:
    """No state may be missing from the machine or the terminal sets."""
    covered = (
        TERMINAL_STATES
        | CLAIMABLE_STATES
        | AMBIGUOUS_STATES
        | {
            JobState.DRAFT,
            JobState.READY,
            JobState.SCHEDULED,
            JobState.UPLOADED,
            JobState.BLOCKED,
            JobState.MISSED_WINDOW,
            JobState.FAILED_RETRY,
            JobState.UPLOADING,
        }
    )
    assert set(JobState) - covered == set()


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (JobState.DRAFT, JobState.READY, True),
        (JobState.READY, JobState.QUEUED, True),
        (JobState.QUEUED, JobState.UPLOADING, True),
        (JobState.UPLOADING, JobState.UPLOADED, True),
        (JobState.UPLOADING, JobState.NEEDS_REVIEW, True),
        (JobState.UPLOADED, JobState.SCHEDULED, True),
        (JobState.SCHEDULED, JobState.PUBLISHED, True),
        (JobState.FAILED_RETRY, JobState.QUEUED, True),
        (JobState.BLOCKED, JobState.QUEUED, True),
        (JobState.MISSED_WINDOW, JobState.QUEUED, True),
        (JobState.NEEDS_REVIEW, JobState.CANCELLED, True),
        # Illegal moves.
        (JobState.PUBLISHED, JobState.QUEUED, False),
        (JobState.CANCELLED, JobState.QUEUED, False),
        (JobState.FAILED, JobState.QUEUED, False),
        (JobState.DRAFT, JobState.PUBLISHED, False),
        (JobState.READY, JobState.UPLOADING, False),
    ],
)
def test_transition_table(current: JobState, target: JobState, allowed: bool) -> None:
    assert can_transition(current, target) is allowed


def test_assert_transition_names_the_legal_moves() -> None:
    with pytest.raises(InvalidTransition) as excinfo:
        assert_transition(JobState.PUBLISHED, JobState.QUEUED)
    message = str(excinfo.value)
    assert "JOB-PUBLISHED" in message
    assert "(none)" in message  # published has no successors


def test_terminal_states_have_no_exits() -> None:
    for state in TERMINAL_STATES:
        assert can_transition(state, state) is False
        for target in JobState:
            assert can_transition(state, target) is False
        assert terminal_reason(state) is not None


def test_non_terminal_states_have_a_reason_of_none() -> None:
    for state in set(JobState) - TERMINAL_STATES:
        assert terminal_reason(state) is None


def test_published_requires_an_observed_public_remote_status() -> None:
    """The single rule that stops an optimistic 'published' being shown."""
    with pytest.raises(ValueError, match="public"):
        display_for(JobState.PUBLISHED, remote=RemoteState.SCHEDULED)
    assert display_for(JobState.PUBLISHED, remote=RemoteState.PUBLIC) == "Published (verified)"


def test_scheduled_becomes_awaiting_verification_only_after_the_time_passes() -> None:
    before = display_for(JobState.SCHEDULED, remote=RemoteState.SCHEDULED)
    after = display_for(
        JobState.SCHEDULED,
        remote=RemoteState.SCHEDULED,
        poll_result=PollResult.NOT_YET,
        publish_at_passed=True,
    )
    assert "awaiting verification" not in before
    assert "awaiting verification" in after


def test_awaiting_verification_is_not_shown_once_confirmed() -> None:
    text = display_for(
        JobState.SCHEDULED,
        remote=RemoteState.PUBLIC,
        poll_result=PollResult.CONFIRMED,
        publish_at_passed=True,
    )
    assert "awaiting verification" not in text


def test_needs_review_never_offers_a_retry() -> None:
    assert can_retry(JobState.NEEDS_REVIEW) is False
    assert primary_action(JobState.NEEDS_REVIEW) == "Resolve"


def test_blocked_never_offers_a_retry() -> None:
    assert can_retry(JobState.BLOCKED) is False
    assert primary_action(JobState.BLOCKED) == "Fix authorization"


def test_retryable_failure_rejects_ambiguous_and_terminal() -> None:
    assert retryable_failure(JobState.QUEUED, FailureKind.AMBIGUOUS) is False
    assert retryable_failure(JobState.QUEUED, FailureKind.HUMAN_REQUIRED) is False
    assert retryable_failure(JobState.QUEUED, FailureKind.PERMANENT) is False
    assert retryable_failure(JobState.QUEUED, FailureKind.AUTHORIZATION) is False
    assert retryable_failure(JobState.NEEDS_REVIEW, FailureKind.TRANSIENT) is False
    assert retryable_failure(JobState.QUEUED, FailureKind.TRANSIENT) is True
    assert retryable_failure(JobState.QUEUED, FailureKind.QUOTA) is True


def test_uploading_progress_renders_the_percentage() -> None:
    text = display_for(JobState.UPLOADING, progress_percent=42)
    assert "42%" in text
    assert display_for(JobState.UPLOADING, progress_percent=999).startswith("Uploading (100%")


def test_mention_chips_are_layered_on_top_of_a_state() -> None:
    plain = display_for(JobState.QUEUED)
    with_mention = display_for(JobState.QUEUED, mention=MentionState.MANUAL_REQUIRED)
    assert with_mention.startswith(plain)
    assert "manual action" in with_mention


def test_label_for_matches_the_display_map() -> None:
    for state in JobState:
        assert label_for(state) == DISPLAY_MAP[state].label


def test_all_states_are_reachable_from_the_claimable_set_eventually() -> None:
    """Breadth-first from the claimable states must reach every non-initial state."""
    seen = set(CLAIMABLE_STATES)
    frontier = list(CLAIMABLE_STATES)
    while frontier:
        current = frontier.pop()
        for target in TRANSITIONS_REACHABLE(current):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    # Every state must be reachable from the set the worker can actually claim.
    # A state that is not is a screen the operator could never navigate to.
    assert set(JobState) - seen == set()


def TRANSITIONS_REACHABLE(state: JobState) -> set[JobState]:
    from hexton.domain.jobstate import TRANSITIONS

    return set(TRANSITIONS[state])
