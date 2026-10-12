"""Sheets sync keys and the mention rules.

``DF-19`` is the pin that shapes the mention tests: the ``videos`` resource has no
mention entity, so an ``@handle`` in a caption can never become ``VERIFIED`` by
itself. These tests exist to make sure no future refactor lets text imply proof.
"""

from __future__ import annotations

from datetime import date

import pytest
from hexton.domain.jobstate import MentionState
from hexton.domain.mention import (
    assess,
    extract_handles,
    may_issue_videos_update,
    may_publish,
)
from hexton.domain.sync_key import (
    DEFAULT_SCHEMA_VERSION,
    PREFIX,
    is_valid_key,
    make_sync_key,
    normalise_schedule_id,
    parse_sync_key,
)

REPORT_DATE = date(2026, 10, 11)


# ----------------------------------------------------------------------#
# Sync key
# ----------------------------------------------------------------------#


def test_key_has_the_documented_shape() -> None:
    key = make_sync_key(REPORT_DATE, "SCH-001")
    assert key == "SCH_2026-10-11_SCH-001_v1"
    assert key.startswith(f"{PREFIX}_")


def test_key_is_deterministic_across_runs() -> None:
    """Append is not idempotent, so the key must survive a restart unchanged."""
    assert make_sync_key(REPORT_DATE, "sch-001") == make_sync_key(REPORT_DATE, "sch-001")


def test_id_is_folded_to_one_canonical_form() -> None:
    assert normalise_schedule_id("  sch-001  ") == "SCH-001"
    assert make_sync_key(REPORT_DATE, "sch-001") == make_sync_key(REPORT_DATE, "SCH-001")


def test_a_separate_schema_version_produces_a_distinct_key() -> None:
    assert make_sync_key(REPORT_DATE, "SCH-001", "v2") != make_sync_key(
        REPORT_DATE, "SCH-001", "v1"
    )
    assert DEFAULT_SCHEMA_VERSION == "v1"


@pytest.mark.parametrize("bad", ["has space", "has,comma", "has/slash", "has\ttab", ""])
def test_an_unparseable_id_is_refused_rather_than_encoded(tmp_path, bad) -> None:
    with pytest.raises(ValueError):
        make_sync_key(REPORT_DATE, bad)


def test_a_missing_date_is_refused() -> None:
    with pytest.raises(ValueError, match="report_date"):
        make_sync_key(None, "SCH-001")  # type: ignore[arg-type]


def test_a_key_round_trips_through_its_parser() -> None:
    key = make_sync_key(REPORT_DATE, "SCH-001")
    parsed = parse_sync_key(key)
    assert parsed.report_date == REPORT_DATE
    assert parsed.schedule_id == "SCH-001"
    assert parsed.schema_version == "v1"


def test_malformed_keys_are_rejected_by_both_entry_points() -> None:
    for bad in ("", "SCH_2026-13-45_X_v1", "OTHER_2026-10-11_X_v1", "nonsense"):
        assert is_valid_key(bad) is False
        with pytest.raises(ValueError):
            parse_sync_key(bad)


def test_a_bad_date_is_rejected_even_when_the_shape_is_right() -> None:
    assert is_valid_key("SCH_2026-02-30_SCH-001_v1") is False


# ----------------------------------------------------------------------#
# Mentions — DF-19
# ----------------------------------------------------------------------#


def test_handles_are_extracted_from_text() -> None:
    assert extract_handles("thanks @alice and @bob.channel!") == ("alice", "bob.channel")


def test_an_email_address_is_not_read_as_a_handle() -> None:
    assert extract_handles("write to someone@example.com") == ()


def test_empty_text_yields_no_handles() -> None:
    assert extract_handles("") == ()


def test_when_nothing_was_requested_nothing_is_pending() -> None:
    result = assess(mention_requested=False, text="@alice")
    assert result.state is MentionState.NOT_REQUESTED
    assert not result.requires_operator_action


def test_a_requested_mention_with_no_handle_in_text_is_text_only() -> None:
    result = assess(mention_requested=True, text="plain caption")
    assert result.state is MentionState.TEXT_ONLY


def test_text_alone_never_becomes_verified() -> None:
    """The whole point: an @handle in a caption is evidence of text, nothing more."""
    result = assess(mention_requested=True, text="shout out @alice")
    assert result.state is MentionState.MANUAL_REQUIRED
    assert result.verified is False
    assert result.requires_operator_action
    assert "DF-19" in result.reason


def test_only_a_human_attestation_can_reach_verified() -> None:
    result = assess(mention_requested=True, text="@alice", operator_confirmed_in_studio=True)
    assert result.state is MentionState.VERIFIED
    assert result.verified
    assert "human attestation" in result.reason
    assert "not an API verification" in result.reason


def test_a_documented_api_mechanism_would_be_the_other_route() -> None:
    """Reserved for a future documentation pin; defaults to off and is never inferred."""
    result = assess(
        mention_requested=True,
        text="@alice",
        documented_api_mechanism_demonstrated=True,
    )
    assert result.state is MentionState.VERIFIED
    assert "documented API mechanism" in result.reason


def test_an_unconfirmed_mention_does_not_block_publication() -> None:
    """Blocking would throw away the operator's caption over a verification claim."""
    pending = assess(mention_requested=True, text="@alice")
    assert may_publish(pending) is False  # the *claim* is blocked, not the upload

    assert may_publish(assess(mention_requested=False)) is True
    assert may_publish(assess(mention_requested=True, text="no handle")) is True


def test_a_partial_update_is_refused_for_a_job_whose_caption_may_hold_a_mention() -> None:
    """DF-06: an omitted already-valued property is deleted, destroying the text."""
    allowed, reason = may_issue_videos_update(
        MentionState.MANUAL_REQUIRED, update_includes_full_snippet=True
    )
    assert allowed is False
    assert "DF-06" in reason
    assert "Studio" in reason


def test_an_omitted_field_is_refused_even_without_a_mention() -> None:
    allowed, reason = may_issue_videos_update(
        MentionState.NOT_REQUESTED, update_includes_full_snippet=False
    )
    assert allowed is False
    assert "DF-06" in reason


def test_a_full_snippet_update_is_permitted() -> None:
    for state in (MentionState.NOT_REQUESTED, MentionState.TEXT_ONLY, MentionState.VERIFIED):
        allowed, _reason = may_issue_videos_update(state, update_includes_full_snippet=True)
        assert allowed is True
