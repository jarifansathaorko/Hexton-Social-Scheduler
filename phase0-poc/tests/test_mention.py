"""Mention behaviour.

The `videos` resource has no mention entity (DF-19). The whole point of this file is to
prove that the presence of `@something` in a caption can never become `VERIFIED` by
accident.
"""

from __future__ import annotations

from hexton_poc.jobstate import MentionState
from hexton_poc.mention import (
    assess,
    extract_handles,
    may_issue_videos_update,
    may_publish,
)


def test_extract_handles_finds_tokens_but_is_only_text_extraction():
    assert extract_handles("thanks to @somecreator and @another.one!") == ("somecreator",
                                                                          "another.one")
    # The lookbehind means an email-like a@b is not read as a handle.
    assert extract_handles("email me at a@b") == ()
    assert extract_handles("no handles here") == ()
    assert extract_handles("") == ()


def test_not_requested_wins_over_everything():
    ev = assess(mention_requested=False, text="@anyone",
                documented_api_mechanism_demonstrated=True)
    assert ev.state is MentionState.NOT_REQUESTED


def test_an_at_handle_in_text_can_never_become_verified():
    """The core guard: no API mechanism, no operator confirmation -> no VERIFIED."""
    ev = assess(mention_requested=True, text="shout out to @somecreator")
    assert ev.state is MentionState.MANUAL_REQUIRED
    assert ev.state is not MentionState.VERIFIED
    assert ev.handles_found == ("somecreator",)
    assert "cannot be verified through the documented API" in ev.reason


def test_a_request_with_no_handle_at_all_is_text_only_not_verified():
    ev = assess(mention_requested=True, text="no handle at all")
    assert ev.state is MentionState.TEXT_ONLY


def test_operator_confirmation_reaches_verified_but_is_labelled_a_human_attestation():
    ev = assess(mention_requested=True, text="thanks @x",
                operator_confirmed_in_studio=True)
    assert ev.state is MentionState.VERIFIED
    assert "human attestation" in ev.reason
    assert "not an API verification" in ev.reason


def test_a_demonstrated_documented_api_mechanism_is_the_other_route():
    ev = assess(mention_requested=True, text="thanks @x",
                documented_api_mechanism_demonstrated=True)
    assert ev.state is MentionState.VERIFIED


def test_may_publish_blocks_an_unconfirmed_mention():
    assert may_publish(assess(mention_requested=True, text="@x")) is False
    assert may_publish(assess(mention_requested=True, text="none")) is False
    assert may_publish(assess(mention_requested=False, text="")) is True


def test_partial_video_update_is_refused_when_a_manual_mention_is_involved():
    ev = assess(mention_requested=True, text="@somecreator")
    allowed, why = may_issue_videos_update(ev, update_includes_full_snippet=True)
    assert allowed is False
    assert "DF-06" in why


def test_partial_video_update_is_refused_when_it_would_destroy_the_description():
    """DF-06: an update that omits a property deletes its existing value."""
    ev = assess(mention_requested=True, text="@x",
                operator_confirmed_in_studio=True)
    allowed, why = may_issue_videos_update(ev, update_includes_full_snippet=False)
    assert allowed is False
    assert "delete" in why and "DF-06" in why


def test_a_full_snippet_update_is_allowed_because_values_are_reasserted():
    ev = assess(mention_requested=True, text="@x",
                operator_confirmed_in_studio=True)
    allowed, _ = may_issue_videos_update(ev, update_includes_full_snippet=True)
    assert allowed is True


def test_a_partial_update_is_allowed_when_no_mention_lives_in_the_metadata():
    ev = assess(mention_requested=False, text="")
    allowed, why = may_issue_videos_update(ev, update_includes_full_snippet=False)
    assert allowed is True
    assert "no mention" in why
