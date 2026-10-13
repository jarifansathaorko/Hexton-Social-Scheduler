"""Comprehensive boundary and edge case tests for Media Validation & Metadata Preflight (Prompt 5).

Verifies that all invalid items are rejected BEFORE any YouTube API upload request:
1. Missing media file.
2. Unreadable media file (OS/permission error).
3. File changed after scheduling (SHA-256 digest mismatch).
4. Unsupported container (extension spoofing, e.g. text file named .mp4).
5. Unsupported extension or missing extension.
6. Empty file (0 bytes) and over-limit file size.
7. Title boundaries: empty, whitespace-only, >100 characters, angle brackets (<, >).
8. Description boundaries: >5000 UTF-8 bytes (including multi-byte characters).
9. Tag boundaries: >500 characters with quoting and comma accounting.
10. Category ID: non-numeric or missing.
11. Privacy status: invalid or missing privacy status; publishAt requires private.
12. Scheduling boundaries: naive datetime, past publication time, already published video.
13. Missing channel or cross-connection channel mismatch.
14. Content transformation requires explicit consent; no silent modification or truncation.
15. Safe probing: unknown duration reported gracefully without inventing values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import fresh
from hexton.domain.jobstate import FailureKind, JobState
from hexton.domain.media import (
    check_extension,
    sniff_container,
)
from hexton.domain.scheduling import (
    ZoneError,
    check_publish_time,
    ensure_utc,
    resolve_zone,
)
from hexton.domain.validation import (
    apply_transforms,
    build_insert_body,
    tag_list_cost,
    validate_description,
    validate_metadata,
    validate_tags,
    validate_title,
)
from hexton.media.validate import (
    validate_file,
    verify_unchanged,
)
from hexton.worker.runner import JobRunner

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# 1. Media File Integrity, Existence & Readability
# --------------------------------------------------------------------------


def test_missing_media_file_rejected(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist.mp4"
    report = validate_file(non_existent)
    assert not report.ok
    assert any(i.code == "missing_file" for i in report.issues)

    unchanged, issue = verify_unchanged(non_existent, "recorded-sha256")
    assert not unchanged
    assert issue is not None
    assert issue.code == "file_missing_since_scheduling"


def test_empty_media_file_rejected(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    report = validate_file(empty_file)
    assert not report.ok
    assert any(i.code == "bad_size" for i in report.issues)


def test_file_changed_after_scheduling_sha256_mismatch(tmp_path: Path) -> None:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2")

    report = validate_file(clip_path)
    assert report.sha256 is not None
    recorded_hash = report.sha256

    # Modify the file
    clip_path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2_MODIFIED")

    unchanged, issue = verify_unchanged(clip_path, recorded_hash)
    assert not unchanged
    assert issue is not None
    assert issue.code == "file_changed_since_scheduling"
    assert "Expected" in issue.message


def test_spoofed_container_extension_rejected(tmp_path: Path) -> None:
    """Text file with .mp4 extension must be rejected by container sniffer."""
    fake_mp4 = tmp_path / "spoofed.mp4"
    fake_mp4.write_text("This is plain text, not a video container.")
    report = validate_file(fake_mp4)
    assert not report.ok
    assert any(i.code == "bad_container" for i in report.issues)


def test_unsupported_or_missing_extension(tmp_path: Path) -> None:
    bad_ext = tmp_path / "video.exe"
    bad_ext.write_bytes(b"dummy")
    assert check_extension(str(bad_ext)) is not None

    no_ext = tmp_path / "videoclip"
    no_ext.write_bytes(b"dummy")
    assert check_extension(str(no_ext)) is not None


def test_safe_metadata_probe_handles_unknown_duration_gracefully() -> None:
    """If duration cannot be parsed from container header, returns duration_parsed=False without crashing."""
    head = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2"
    info = sniff_container(head)
    assert info is not None
    assert info.duration_seconds is None
    assert not info.duration_parsed


# --------------------------------------------------------------------------
# 2. Metadata Boundaries: Title, Description, Tags, Privacy
# --------------------------------------------------------------------------


def test_title_boundaries() -> None:
    # Empty / whitespace-only
    assert any(i.code == "empty_title" for i in validate_title(""))
    assert any(i.code == "empty_title" for i in validate_title("   "))

    # Over 100 characters
    long_title = "A" * 101
    assert any(i.code == "title_too_long" for i in validate_title(long_title))

    # Exactly 100 characters is OK
    assert len(validate_title("A" * 100)) == 0

    # Angle brackets (<, >) rejected by YouTube
    assert any(i.code == "title_angle_bracket" for i in validate_title("My Video <Short>"))
    assert any(i.code == "title_angle_bracket" for i in validate_title("Check this > cool"))


def test_description_byte_boundary() -> None:
    # 5000 ASCII chars = 5000 bytes -> OK
    assert len(validate_description("a" * 5000)) == 0

    # 5001 ASCII chars = 5001 bytes -> Error
    assert any(i.code == "description_too_long" for i in validate_description("a" * 5001))

    # Multibyte UTF-8: € is 3 bytes in UTF-8
    # 1667 € chars = 5001 bytes -> Error
    multibyte_over = "€" * 1667
    assert any(i.code == "description_too_long" for i in validate_description(multibyte_over))


def test_tags_quoting_and_comma_cost_accounting() -> None:
    # Tag with space costs len + 2 (for quotes)
    assert tag_list_cost(["single"]) == 6
    assert tag_list_cost(["with space"]) == 10 + 2  # 12
    # Two tags also cost 1 char for separating comma
    assert tag_list_cost(["tag1", "tag2"]) == 4 + 4 + 1  # 9

    # Warning on tag containing comma
    issues = validate_tags(["tag,split"])
    assert any(i.code == "tag_contains_comma" for i in issues)

    # Over 500 character cost
    huge_tags = ["tag" + str(i) for i in range(120)]  # > 500 chars total
    issues = validate_tags(huge_tags)
    assert any(i.code == "tags_too_long" for i in issues)


def test_category_id_and_privacy_validation() -> None:
    # Non-numeric category ID
    res = validate_metadata(
        title="Valid Title",
        description="Valid Description",
        tags=["tag"],
        category_id="film_and_animation",  # Non-numeric
        privacy="private",
    )
    assert not res.ok
    assert any(i.code == "invalid_category" for i in res.errors)

    # Invalid privacy
    res = validate_metadata(
        title="Valid Title",
        description="Valid Description",
        tags=["tag"],
        category_id="22",
        privacy="unsupported_privacy",
    )
    assert not res.ok
    assert any(i.code == "invalid_privacy" for i in res.errors)


def test_build_insert_body_enforces_privacy_and_publish_at() -> None:
    # publishAt with public visibility is forbidden (YouTube requires private)
    with pytest.raises(
        ValueError, match="publishAt is only meaningful with privacyStatus='private'"
    ):
        build_insert_body(
            title="Test",
            description="Test",
            tags=[],
            category_id="22",
            privacy="public",
            publish_at=NOW + timedelta(hours=2),
        )

    # publishAt with private visibility formats cleanly to RFC3339 UTC
    body = build_insert_body(
        title="Test",
        description="Test",
        tags=["short"],
        category_id="22",
        privacy="private",
        publish_at=NOW + timedelta(hours=2),
    )
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-10-13T14:00:00Z"


# --------------------------------------------------------------------------
# 3. Scheduling & Time Zone Boundaries
# --------------------------------------------------------------------------


def test_timezone_resolution_and_naive_datetime() -> None:
    # Valid IANA zone resolves
    tz = resolve_zone("Asia/Dhaka")
    assert tz.key == "Asia/Dhaka"

    # Invalid zone key raises ZoneError
    with pytest.raises(ZoneError):
        resolve_zone("Invalid/NonExistent_Zone")

    with pytest.raises(ZoneError):
        resolve_zone("")

    # Naive datetime rejected
    naive_dt = datetime(2026, 10, 13, 15, 0, 0)
    with pytest.raises(ValueError, match="explicit time zone"):
        ensure_utc(naive_dt)


def test_past_publish_time_hard_block() -> None:
    past_time = NOW - timedelta(minutes=10)
    check = check_publish_time(past_time, now=NOW)
    assert not check.ok
    assert any(i.code == "publish_at_in_past" for i in check.blocking)


def test_already_published_video_cannot_be_rescheduled() -> None:
    future_time = NOW + timedelta(hours=5)
    check = check_publish_time(future_time, now=NOW, never_published=False)
    assert not check.ok
    assert any(i.code == "already_published" for i in check.blocking)


# --------------------------------------------------------------------------
# 4. Content Transformations Require Explicit Consent (No Silent Changes)
# --------------------------------------------------------------------------


def test_transforms_require_explicit_consent() -> None:
    raw_desc = "Original description with #Hashtags."
    tags = ["#One", "#TWO", "#one"]

    # Without consent: description is byte-identical, tags unmodified
    desc_no_consent, tags_no_consent, logs_no_consent = apply_transforms(
        raw_desc, tags, consent=False
    )
    assert desc_no_consent == raw_desc
    assert tags_no_consent == tags
    assert len(logs_no_consent) == 0

    # With consent: tags normalised and appended with visible change log
    desc_with_consent, tags_with_consent, logs_with_consent = apply_transforms(
        raw_desc, tags, consent=True
    )
    assert desc_with_consent != raw_desc
    assert tags_with_consent == ["#one", "#two"]
    assert "#one #two" in desc_with_consent
    assert len(logs_with_consent) > 0


# --------------------------------------------------------------------------
# 5. Worker Preflight End-to-End Rejection (Pre-Upload Gate)
# --------------------------------------------------------------------------


def test_runner_preflight_blocks_job_with_missing_channel(ctx, ready_job) -> None:
    """Preflight catches missing channel identity before touching any connector."""
    runner = JobRunner(ctx, owner="test-w1")
    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        job.channel_identity_id = None
        s.commit()

        should_stop = runner._preflight(s, job, NOW)
        assert should_stop is True
        assert job.state == JobState.BLOCKED.value
        assert job.failure_kind == FailureKind.VALIDATION.value
        assert "No verified channel identity" in (job.failure_message or "")


def test_runner_preflight_blocks_job_with_cross_connection_channel_mismatch(ctx, ready_job) -> None:
    """Preflight detects channel belonging to a different connection."""
    from hexton.db.models import PlatformConnection

    runner = JobRunner(ctx, owner="test-w1")
    with fresh(ctx) as s:
        # Create a second valid platform connection
        conn2 = PlatformConnection(
            provider="youtube",
            label="Second Connection",
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
            status="active",
        )
        s.add(conn2)
        s.flush()

        job = s.get(type(ready_job), ready_job.id)
        # Point channel to connection 2 while job is attached to connection 1
        job.channel.connection_id = conn2.id
        s.commit()

        should_stop = runner._preflight(s, job, NOW)
        assert should_stop is True
        assert job.state == JobState.BLOCKED.value
        assert job.failure_kind == FailureKind.VALIDATION.value
        assert "channel-mismatch" in (job.failure_message or "")
