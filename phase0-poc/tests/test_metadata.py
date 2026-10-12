"""Metadata limits and the scheduling preflight.

The single most important assertion in this file is that a past publication time is
*blocked*, because `status.publishAt` in the past is documented to publish the video
immediately (DF-04).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hexton_poc import metadata as m

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _codes(issues):
    return {i.code for i in issues}


# -- title -------------------------------------------------------------------

def test_empty_title_is_an_error():
    assert "empty_title" in _codes(m.validate_title("   "))


def test_title_of_exactly_100_chars_is_accepted_and_101_rejected():
    assert m.validate_title("x" * 100) == []
    assert "title_too_long" in _codes(m.validate_title("x" * 101))


def test_title_angle_brackets_rejected_in_both_directions():
    assert "title_angle_bracket" in _codes(m.validate_title("a<b"))
    assert "title_angle_bracket" in _codes(m.validate_title("a>b"))


# -- description -------------------------------------------------------------

def test_description_limit_is_counted_in_bytes_not_characters():
    # 2500 x 'e' = 2500 bytes, fine. 2500 x '€' = 7500 bytes, not.
    assert m.validate_description("e" * 2500) == []
    assert "description_too_long" in _codes(m.validate_description("€" * 2500))


def test_description_at_exactly_the_limit_is_accepted():
    assert m.validate_description("e" * m.DESCRIPTION_MAX_BYTES) == []


# -- tags (quoting-aware accounting, DF-16) -----------------------------------

def test_tag_without_spaces_counts_its_own_length():
    # "Foo-Baz" costs 7 chars, as documented.
    assert m._tag_list_cost(["Foo-Baz"]) == 7


def test_tag_with_a_space_counts_its_quotes():
    # "Foo Baz" is treated as `"Foo Baz"` -> 9 chars, as documented.
    assert m._tag_list_cost(["Foo Baz"]) == 9


def test_commas_between_tags_count_toward_the_limit():
    # ["ab","cd"] = 2 + 1 (comma) + 2 = 5
    assert m._tag_list_cost(["ab", "cd"]) == 5


def test_tag_limit_is_enforced():
    tags = ["wordy tag " + "z" * 400, "another tag " + "y" * 400]
    assert "tags_too_long" in _codes(m.validate_tags(tags))


def test_empty_tag_list_is_not_an_error():
    assert m.validate_tags([]) == []


# -- privacy -----------------------------------------------------------------

def test_privacy_must_be_explicit_and_valid():
    assert "invalid_privacy" in _codes(m.validate_privacy(""))
    assert "invalid_privacy" in _codes(m.validate_privacy("Public "))
    assert m.validate_privacy("private") == []


# -- preflight ----------------------------------------------------------------

def _preflight(**kw):
    base = dict(
        title="A perfectly ordinary title",
        description="A perfectly ordinary description",
        tags=["shorts"],
        privacy="private",
        publish_at=NOW + timedelta(hours=6),
        now=NOW,
    )
    base.update(kw)
    return m.preflight(**base)


def test_preflight_passes_for_a_sane_future_private_job():
    res = _preflight()
    assert res.ok, res.issues
    assert res.errors == []


def test_preflight_blocks_a_publish_time_in_the_past():
    """DF-04: a past publishAt publishes immediately. Block it."""
    res = _preflight(publish_at=NOW - timedelta(seconds=1))
    assert not res.ok
    assert "publish_at_in_past" in _codes(res.errors)
    # The message must explain WHY, not just refuse.
    msg = next(i for i in res.issues if i.code == "publish_at_in_past")
    assert "immediately" in msg.message


def test_preflight_blocks_a_publish_time_inside_the_minimum_lead_time():
    res = _preflight(publish_at=NOW + timedelta(minutes=5))
    assert not res.ok
    assert "publish_at_too_soon" in _codes(res.errors)


def test_preflight_warns_inside_the_warning_band_but_still_allows_queueing():
    res = _preflight(publish_at=NOW + timedelta(minutes=60))
    assert res.ok, "60 min is outside the configured minimum and must be queueable"
    assert "publish_at_near_lead" in _codes(res.warnings)


def test_preflight_blocks_a_naive_datetime_rather_than_guessing_a_zone():
    res = _preflight(publish_at=datetime(2026, 10, 12, 18, 0, 0))
    assert not res.ok
    assert "naive_datetime" in _codes(res.errors)


def test_preflight_blocks_publish_at_on_an_already_published_video():
    """DF-03: publishAt requires a never-published video."""
    res = _preflight(never_published=False)
    assert not res.ok
    assert "already_published" in _codes(res.errors)


def test_preflight_without_publish_at_never_concerns_itself_with_time():
    res = _preflight(publish_at=None)
    assert res.ok


# -- insert body --------------------------------------------------------------

def test_build_insert_body_always_writes_privacy_status_explicitly():
    body = m.build_insert_body(title="t", description="d", tags=[], category_id="22",
                               privacy="private", publish_at=None)
    assert body["status"]["privacyStatus"] == "private"


def test_build_insert_body_omits_publish_at_when_not_scheduling():
    body = m.build_insert_body(title="t", description="d", tags=[], category_id="22",
                               privacy="private", publish_at=None)
    assert "publishAt" not in body["status"]


def test_build_insert_body_formats_publish_at_as_utc_z():
    body = m.build_insert_body(
        title="t", description="d", tags=[], category_id="22", privacy="private",
        publish_at=NOW + timedelta(days=1))
    assert body["status"]["publishAt"] == "2026-10-12T12:00:00.000Z"


def test_build_insert_body_refuses_publish_at_unless_private():
    """DF-03 enforced at construction, not just in preflight."""
    with pytest.raises(ValueError, match="DF-03"):
        m.build_insert_body(title="t", description="d", tags=[], category_id="22",
                            privacy="unlisted", publish_at=NOW + timedelta(days=1))


def test_build_insert_body_refuses_an_unknown_privacy_value():
    with pytest.raises(ValueError, match="refusing"):
        m.build_insert_body(title="t", description="d", tags=[], category_id="22",
                            privacy="scheduled", publish_at=None)


# -- media ---------------------------------------------------------------------

def test_missing_media_file_is_reported_not_raised(tmp_path):
    issues, size, digest = m.validate_media(tmp_path / "nope.mp4")
    assert "missing_file" in _codes(issues)
    assert size == 0 and digest == ""


def test_media_sha256_is_computed_and_detected_when_the_file_changes(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"a" * 4096)
    issues, size, digest = m.validate_media(p)
    assert "file_changed" not in _codes(issues)
    assert size == 4096 and len(digest) == 64

    p.write_bytes(b"b" * 4096)
    issues2, _, _ = m.validate_media(p, expected_sha256=digest)
    assert "file_changed" in _codes(issues2)


def test_a_tiny_file_is_rejected(tmp_path):
    p = tmp_path / "tiny.mp4"
    p.write_bytes(b"abc")
    issues, _, _ = m.validate_media(p)
    assert "file_too_small" in _codes(issues)


def test_an_unusual_extension_is_a_warning_not_a_block(tmp_path):
    p = tmp_path / "clip.bin"
    p.write_bytes(b"x" * 4096)
    issues, _, _ = m.validate_media(p)
    assert "unexpected_extension" in _codes(issues)
    assert all(i.severity is m.Severity.WARNING for i in issues)
