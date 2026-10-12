"""Metadata construction: no silent truncation, consent-based transforms only.

The property under test throughout is the one the product exists for: the operator's
text is either accepted in full or reported as a problem. It is never quietly cut to
fit, and it is never rewritten unless the operator asked for a transformation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hexton.domain.validation import (
    DESCRIPTION_MAX_BYTES,
    TITLE_MAX_CHARS,
    ContentMetadata,
    Severity,
    apply_transforms,
    build_insert_body,
    build_metadata,
    normalise_hashtags,
    tag_cost,
    tag_list_cost,
    validate_description,
    validate_metadata,
    validate_privacy,
    validate_tags,
    validate_title,
)

# ----------------------------------------------------------------------#
# Title
# ----------------------------------------------------------------------#


def test_a_title_of_exactly_the_maximum_is_accepted() -> None:
    assert validate_title("x" * TITLE_MAX_CHARS) == ()


def test_a_title_one_character_over_is_an_error_not_a_truncation() -> None:
    """The core rule: report the problem, never silently cut the text."""
    issues = validate_title("x" * (TITLE_MAX_CHARS + 1))
    assert len(issues) == 1
    assert issues[0].code == "title_too_long"
    assert issues[0].blocking
    assert f"{TITLE_MAX_CHARS}" in issues[0].message


def test_an_empty_title_is_rejected() -> None:
    assert [i.code for i in validate_title("   ")] == ["empty_title"]


def test_angle_brackets_are_rejected_with_a_reason() -> None:
    issues = validate_title("a < b > c")
    assert [i.code for i in issues] == ["title_angle_bracket"]


def test_title_is_not_trimmed_or_normalised() -> None:
    """Validation reports; it does not edit. ``build_metadata`` must pass it through."""
    spaced = "   leading and trailing   "
    assert validate_title(spaced) == ()
    built = build_metadata(
        title=spaced, description="d", tags=[], category_id="22", privacy="private"
    )
    assert built.title == spaced


# ----------------------------------------------------------------------#
# Description — counted in bytes, not characters
# ----------------------------------------------------------------------#


def test_description_limit_is_measured_in_utf8_bytes() -> None:
    ascii_text = "a" * DESCRIPTION_MAX_BYTES
    assert validate_description(ascii_text) == ()
    # Two bytes per character: half as many characters, same byte budget.
    accented = "é" * (DESCRIPTION_MAX_BYTES // 2)
    assert validate_description(accented) == ()
    assert validate_description("é" * (DESCRIPTION_MAX_BYTES // 2 + 1)) != ()


def test_an_overlong_description_reports_the_byte_count() -> None:
    issues = validate_description("x" * (DESCRIPTION_MAX_BYTES + 1))
    assert [i.code for i in issues] == ["description_too_long"]
    assert f"{DESCRIPTION_MAX_BYTES}" in issues[0].message


def test_description_line_endings_are_preserved_verbatim() -> None:
    text = "one\r\ntwo\r\n"
    built = build_metadata(
        title="t", description=text, tags=[], category_id="22", privacy="private"
    )
    assert built.description == text


# ----------------------------------------------------------------------#
# Tags
# ----------------------------------------------------------------------#


def test_tag_cost_counts_quotes_only_for_tags_that_need_them() -> None:
    assert tag_cost("shorts") == 6
    assert tag_cost("two words") == 11  # 9 + 2 quote characters


def test_tag_list_cost_includes_the_commas() -> None:
    assert tag_list_cost([]) == 0
    assert tag_list_cost(["a", "b"]) == 3  # 1 + 1 + 1 comma
    assert tag_list_cost(["a"]) == 1


def test_tag_budget_is_the_documented_500_characters() -> None:
    # 100 tags of one character = 100 characters + 99 commas = 199: fine.
    assert validate_tags(["a"] * 100) == ()
    # Deliberately overspend the budget.
    over = ["x" * 10] * 60  # 600 characters plus commas
    codes = [i.code for i in validate_tags(over)]
    assert "tags_too_long" in codes


def test_a_tag_containing_a_comma_is_a_warning_not_a_block() -> None:
    issues = validate_tags(["one,two"])
    assert len(issues) == 1
    assert issues[0].code == "tag_contains_comma"
    assert issues[0].severity is Severity.WARNING
    assert not issues[0].blocking


def test_blank_tags_are_dropped_before_costing() -> None:
    assert validate_tags(["", "   ", "ok"]) == ()
    assert tag_list_cost([t for t in ["", "   ", "ok"] if t.strip()]) == 2


# ----------------------------------------------------------------------#
# Privacy
# ----------------------------------------------------------------------#


def test_privacy_is_never_defaulted() -> None:
    """DF-02: a server-side default is the exact failure this product prevents."""
    assert [i.code for i in validate_privacy(None)] == ["invalid_privacy"]
    assert [i.code for i in validate_privacy("private")] == []


def test_every_documented_visibility_is_accepted() -> None:
    for value in ("private", "unlisted", "public"):
        assert validate_privacy(value) == ()


def test_an_unknown_visibility_is_rejected() -> None:
    assert [i.code for i in validate_privacy("friends")][:1] == ["invalid_privacy"]


# ----------------------------------------------------------------------#
# Whole-metadata validation
# ----------------------------------------------------------------------#


def _validate(**overrides):
    fields = {
        "title": "A title",
        "description": "A description",
        "tags": ["a"],
        "category_id": "22",
        "privacy": "private",
    }
    fields.update(overrides)
    return validate_metadata(**fields)


def test_a_complete_valid_set_passes() -> None:
    assert _validate().ok


def test_every_problem_is_reported_in_one_pass() -> None:
    report = _validate(title="", description="", tags=["x" * 600], category_id="", privacy="nope")
    assert not report.ok
    codes = {i.code for i in report.errors}
    assert {"empty_title", "tags_too_long", "invalid_privacy", "invalid_category"} <= codes


def test_warnings_do_not_block() -> None:
    report = _validate(tags=["one,two"])
    assert report.ok
    assert len(report.warnings) == 1
    assert len(report.errors) == 0


def test_a_non_numeric_category_is_rejected() -> None:
    assert [i.code for i in _validate(category_id="People & Blogs").errors] == ["invalid_category"]


def test_build_metadata_refuses_to_construct_an_invalid_record() -> None:
    with pytest.raises(ValueError, match="not valid"):
        build_metadata(title="", description="", tags=[], category_id="22", privacy="private")


def test_build_metadata_returns_the_operator_text_unchanged() -> None:
    built = build_metadata(
        title="  keep me  ",
        description="keep\r\nme",
        tags=["  b  ", "a"],
        category_id="22",
        privacy="unlisted",
    )
    assert isinstance(built, ContentMetadata)
    assert built.title == "  keep me  "
    assert built.description == "keep\r\nme"
    assert built.tags == ("b", "a")  # tags are the one field that is cleaned
    assert built.privacy == "unlisted"


def test_to_snippet_always_carries_every_field() -> None:
    """DF-06: an omitted already-valued property is deleted, so none may be omitted."""
    built = build_metadata(
        title="t", description="d", tags=["a"], category_id="22", privacy="private"
    )
    assert built.to_snippet() == {
        "title": "t",
        "description": "d",
        "tags": ["a"],
        "categoryId": "22",
    }


# ----------------------------------------------------------------------#
# Consent-based transformation
# ----------------------------------------------------------------------#


def test_normalise_hashtags_lowercases_and_deduplicates() -> None:
    assert normalise_hashtags(["Shorts", "#SHORTS", "Music"]) == ["#shorts", "#music"]


def test_normalise_hashtags_never_invents_or_drops_a_tag() -> None:
    assert normalise_hashtags(["  spaced  ", "#", ""]) == ["#spaced"]


def test_without_consent_the_description_is_byte_identical() -> None:
    """FEAT-042: transformations are opt-in; a tool that rewrites your text is one you
    stop trusting."""
    original = "Body text\r\nwith CRLF."
    out, tags, log = apply_transforms(original, ["Shorts"], consent=False)
    assert out == original
    assert out.encode() == original.encode()
    assert tags == ["Shorts"]  # left exactly where the operator put them
    assert log == []


def test_with_consent_hashtags_are_appended_and_the_change_is_logged() -> None:
    out, tags, log = apply_transforms("Body", ["Shorts"], consent=True)
    assert out == "Body\n\n#shorts"
    assert tags == ["#shorts"]
    assert any("Appended" in entry for entry in log)


def test_consent_does_not_append_hashtags_twice() -> None:
    out, _tags, log = apply_transforms("Body\n\n#shorts", ["Shorts"], consent=True)
    assert out == "Body\n\n#shorts"
    assert any("already present" in entry for entry in log)


# ----------------------------------------------------------------------#
# insert body
# ----------------------------------------------------------------------#


def test_insert_body_always_carries_an_explicit_privacy_status() -> None:
    body = build_insert_body(
        title="t",
        description="d",
        tags=["a"],
        category_id="22",
        privacy="private",
        publish_at=None,
    )
    assert body["status"]["privacyStatus"] == "private"
    assert "publishAt" not in body["status"]
    assert body["snippet"] == {
        "title": "t",
        "description": "d",
        "tags": ["a"],
        "categoryId": "22",
    }


def test_publish_at_is_emitted_as_rfc3339_utc() -> None:
    when = datetime(2030, 1, 1, 18, 0, tzinfo=timezone(timedelta(hours=6)))
    body = build_insert_body(
        title="t",
        description="d",
        tags=[],
        category_id="22",
        privacy="private",
        publish_at=when,
    )
    assert body["status"]["publishAt"] == "2030-01-01T12:00:00Z"


def test_publish_at_refuses_anything_but_private() -> None:
    with pytest.raises(ValueError, match="privacyStatus='private'"):
        build_insert_body(
            title="t",
            description="d",
            tags=[],
            category_id="22",
            privacy="public",
            publish_at=datetime(2030, 1, 1, tzinfo=UTC),
        )


def test_an_unknown_privacy_cannot_reach_the_body() -> None:
    with pytest.raises(ValueError, match="privacy must be one of"):
        build_insert_body(
            title="t",
            description="d",
            tags=[],
            category_id="22",
            privacy="secret",
            publish_at=None,
        )
