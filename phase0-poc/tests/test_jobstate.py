"""The single canonical state model and its one display mapping.

Revision 1.0 of the PRDs carried three incompatible status vocabularies. These tests
exist so that a fourth one cannot quietly reappear.
"""

from __future__ import annotations

import pytest

from hexton_poc.jobstate import (
    AWAITING_VERIFICATION_SUFFIX,
    DISPLAY_MAP,
    JobState,
    MentionState,
    PollResult,
    RemoteState,
    can_retry,
    display_for,
    label_for,
    primary_action,
)


def test_every_state_has_exactly_one_display_entry():
    assert set(DISPLAY_MAP) == set(JobState), "a state exists without a display mapping"
    assert len(DISPLAY_MAP) == len(JobState)


def test_labels_are_unique():
    labels = [d.label for d in DISPLAY_MAP.values()]
    assert len(labels) == len(set(labels)), "two states share a UI label"


def test_published_is_reachable_only_with_remote_public_evidence():
    with pytest.raises(ValueError, match="observed remote"):
        display_for(JobState.PUBLISHED, remote=RemoteState.UNKNOWN)
    with pytest.raises(ValueError):
        display_for(JobState.PUBLISHED, remote=RemoteState.SCHEDULED)

    assert display_for(JobState.PUBLISHED, remote=RemoteState.PUBLIC) == "Published (verified)"


def test_a_scheduled_job_whose_time_passed_without_a_poll_says_awaiting_verification():
    text = display_for(
        JobState.SCHEDULED,
        remote=RemoteState.SCHEDULED,
        poll_result=PollResult.NEVER_POLLED,
        publish_at_passed=True,
    )
    assert AWAITING_VERIFICATION_SUFFIX in text
    # Crucially it must NOT claim publication, and must not claim failure.
    assert "Published" not in text
    assert "Failed" not in text


def test_a_scheduled_job_before_its_time_does_not_carry_the_suffix():
    text = display_for(JobState.SCHEDULED, remote=RemoteState.SCHEDULED,
                       poll_result=PollResult.NOT_YET, publish_at_passed=False)
    assert AWAITING_VERIFICATION_SUFFIX not in text


def test_a_scheduled_job_confirmed_public_drops_the_suffix():
    """Once the sweep has observed public, the state is PUBLISHED, not SCHEDULED."""
    text = display_for(JobState.PUBLISHED, remote=RemoteState.PUBLIC,
                       poll_result=PollResult.CONFIRMED, publish_at_passed=True)
    assert text == "Published (verified)"


def test_needs_review_and_blocked_never_offer_a_retry():
    for state in (JobState.NEEDS_REVIEW, JobState.BLOCKED):
        assert can_retry(state) is False, f"{state} must not offer Retry"


def test_retry_is_offered_on_transient_failures_but_not_terminal_states():
    assert can_retry(JobState.FAILED_RETRY) is True
    assert can_retry(JobState.MISSED_WINDOW) is True
    for state in (JobState.PUBLISHED, JobState.CANCELLED, JobState.FAILED):
        assert can_retry(state) is False


def test_the_primary_action_for_needs_review_is_resolve_not_retry():
    assert primary_action(JobState.NEEDS_REVIEW) == "Resolve"
    assert primary_action(JobState.BLOCKED) == "Fix authorization"


def test_an_unconfirmed_mention_is_surfaced_in_the_status_text():
    text = display_for(JobState.UPLOADED, remote=RemoteState.UPLOADED_PROCESSING,
                       mention=MentionState.MANUAL_REQUIRED)
    assert "mention needs manual action" in text


def test_a_neutral_mention_state_adds_nothing_to_the_status():
    text = display_for(JobState.QUEUED, mention=MentionState.NOT_REQUESTED)
    assert text == "Queued locally"


def test_display_cues_exist_for_every_state_and_state_nothing_false():
    for state, disp in DISPLAY_MAP.items():
        assert disp.cue.strip(), f"{state} has no colour cue"
        assert disp.never_implies.strip(), f"{state} does not say what its label must not imply"
