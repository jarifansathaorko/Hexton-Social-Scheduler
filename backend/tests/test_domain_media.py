"""Container sniffing, duration parsing, and the file-integrity rules.

Every test here works on real bytes. A placeholder payload would make the media tests
pass for the wrong reason, since what is being verified is that the parser reads the
file's own header rather than trusting its name.
"""

from __future__ import annotations

import struct

from hexton.domain.media import (
    ALLOWED_EXTENSIONS,
    MAX_SIZE_BYTES,
    check_extension,
    check_size,
    content_type_for,
    parse_duration,
    read_container,
    sniff_container,
)
from hexton.media.validate import format_bytes, is_allowed_path, validate_file, verify_unchanged


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mvhd(duration: float, timescale: int = 600) -> bytes:
    payload = bytes([0, 0, 0, 0]) + struct.pack(">III", 0, 0, timescale)
    payload += struct.pack(">I", int(duration * timescale))
    payload += b"\x00" * 80
    return _box(b"mvhd", payload)


def _clip(*, duration: float = 12.5, timescale: int = 600, filler: int = 128) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom" + b"iso2")
    moov = _box(b"moov", _mvhd(duration, timescale))
    return ftyp + moov + _box(b"free", b"\x00" * filler)


# ----------------------------------------------------------------------#
# Sniffing
# ----------------------------------------------------------------------#


def test_isom_brand_is_a_video_container() -> None:
    info = sniff_container(_clip())
    assert info is not None
    assert info.container == "mp4"
    assert info.brand == "isom"


def test_quicktime_brand_is_identified_as_quicktime() -> None:
    head = b"\x00" * 4 + b"ftyp" + b"qt  " + b"\x00" * 8
    info = sniff_container(head)
    assert info is not None
    assert info.container == "quicktime"


def test_ebml_header_distinguishes_webm_from_matroska() -> None:
    webm = sniff_container(b"\x1a\x45\xdf\xa3" + b"\x1a\x45" + b"webm" + b"\x00" * 32)
    mkv = sniff_container(b"\x1a\x45\xdf\xa3" + b"matroska" + b"\x00" * 32)
    assert webm is not None and webm.container == "webm"
    assert mkv is not None and mkv.container == "matroska"


def test_an_extension_is_never_used_as_a_fallback() -> None:
    """The file must speak for itself: no header means no container."""
    assert sniff_container(b"RIFFxxxxWAVEfmt ") is None
    assert sniff_container(b"short") is None  # under the 12-byte minimum


def test_an_unrecognised_brand_is_reported_but_accepted() -> None:
    head = b"\x00" * 4 + b"ftyp" + b"zzzz" + b"\x00" * 8
    info = sniff_container(head)
    assert info is not None
    assert info.container == "mp4"
    assert info.note is not None and "Unrecognised major brand" in info.note


# ----------------------------------------------------------------------#
# Duration
# ----------------------------------------------------------------------#


def test_duration_comes_from_the_movie_header() -> None:
    assert parse_duration(_clip(duration=12.5, timescale=600)) == 12.5


def test_version_one_movie_header_is_supported() -> None:
    payload = bytes([1, 0, 0, 0]) + struct.pack(">QQ", 0, 0)
    payload += struct.pack(">I", 1000) + struct.pack(">Q", 30_000)
    payload += b"\x00" * 80
    assert parse_duration(_box(b"mvhd", payload)) == 30.0


def test_no_movie_header_is_none_and_not_a_fabricated_zero() -> None:
    """A missing duration is an honest answer; it never blocks the upload."""
    assert parse_duration(b"\x00" * 64) is None


def test_a_zero_duration_is_rejected_rather_than_reported() -> None:
    assert parse_duration(_clip(duration=0)) is None


def test_a_truncated_movie_header_does_not_raise() -> None:
    # 'mvhd' present but the timescale/duration fields are cut off.
    chunk = _box(b"mvhd", bytes([0, 0, 0, 0]) + b"\x00" * 8)
    assert parse_duration(chunk) is None


def test_read_container_uses_head_then_tail() -> None:
    info = read_container(_clip())
    assert info is not None
    assert info.duration_parsed
    assert info.duration_seconds == 12.5


def test_read_container_finds_a_tail_placed_movie_header() -> None:
    """A recorded or fragmented stream often puts ``moov`` last."""
    head = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")
    tail = _box(b"moov", _mvhd(7.0))
    info = read_container(head, tail)
    assert info is not None and info.duration_seconds == 7.0


def test_read_container_reports_an_absent_duration_without_blocking() -> None:
    head = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")
    info = read_container(head)
    assert info is not None
    assert info.duration_seconds is None
    assert not info.duration_parsed
    assert info.note is not None


# ----------------------------------------------------------------------#
# Extension and size gates
# ----------------------------------------------------------------------#


def test_only_documented_clip_containers_are_accepted() -> None:
    for suffix in (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"):
        assert check_extension(f"clip{suffix}") is None
    assert check_extension("clip.exe") is not None
    assert check_extension("clip.zip") is not None


def test_a_file_with_no_extension_is_refused_with_a_reason() -> None:
    reason = check_extension("clip")
    assert reason is not None and "no extension" in reason


def test_the_allowed_set_matches_the_documented_mime_map() -> None:
    assert set(ALLOWED_EXTENSIONS) >= {".mp4", ".mov", ".webm", ".mkv"}
    assert content_type_for("a.mp4") == "video/mp4"
    assert content_type_for("a.xyz") == "application/octet-stream"


def test_an_empty_file_is_refused() -> None:
    assert check_size(0) is not None
    assert check_size(1) is None


def test_an_oversized_file_states_the_ceiling() -> None:
    reason = check_size(MAX_SIZE_BYTES + 1)
    assert reason is not None and "GB" in reason


def test_path_filtering_is_extension_based() -> None:
    assert is_allowed_path("x.mp4")
    assert not is_allowed_path("x.txt")


# ----------------------------------------------------------------------#
# Whole-file validation and change detection
# ----------------------------------------------------------------------#


def test_a_valid_clip_validates_and_records_a_digest(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(_clip())
    report = validate_file(path)
    assert report.ok
    assert report.exists and report.readable
    assert report.container == "mp4"
    assert report.duration_seconds == 12.5
    assert report.sha256 and len(report.sha256) == 64
    assert report.content_type == "video/mp4"


def test_a_missing_file_is_reported_not_raised(tmp_path) -> None:
    report = validate_file(tmp_path / "nope.mp4")
    assert not report.ok
    assert [i.code for i in report.blocking_issues] == ["missing_file"]
    assert report.sha256 is None


def test_a_directory_is_not_a_video(tmp_path) -> None:
    report = validate_file(tmp_path)
    assert not report.ok
    assert "not_a_file" in [i.code for i in report.blocking_issues]


def test_a_file_with_the_right_extension_but_no_video_header_is_refused(tmp_path) -> None:
    """The classic mismatch: a renamed non-video must not reach the upload path."""
    path = tmp_path / "fake.mp4"
    path.write_bytes(b"RIFFxxxxWAVEfmt " + b"\x00" * 64)
    report = validate_file(path)
    assert not report.ok
    assert "bad_container" in [i.code for i in report.blocking_issues]
    assert report.container is None


def test_an_unknown_duration_is_a_warning_not_a_block(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")
    path.write_bytes(ftyp + _box(b"free", b"\x00" * 256))
    report = validate_file(path)
    assert report.ok  # non-blocking
    assert report.duration_seconds is None
    assert "duration_unknown" in [i.code for i in report.issues]


def test_a_moov_after_the_head_window_still_yields_a_duration(tmp_path) -> None:
    """The tail window exists precisely so a recorder-written clip is not misreported."""
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")
    body = ftyp + _box(b"free", b"\x00" * 600_000) + _box(b"moov", _mvhd(9.0))
    path = tmp_path / "tail.mp4"
    path.write_bytes(body)
    report = validate_file(path)
    assert report.ok
    assert report.container == "mp4"
    assert report.duration_seconds == 9.0


def test_a_changed_file_is_blocked_with_an_explanation(tmp_path) -> None:
    """§1: a file replaced after scheduling must never be uploaded silently."""
    path = tmp_path / "clip.mp4"
    original = _clip()
    path.write_bytes(original)
    report = validate_file(path)
    assert report.sha256 is not None

    ok, issue = verify_unchanged(path, report.sha256)
    assert ok and issue is None

    # Same length, different bytes: only the digest can catch this.
    path.write_bytes(original[:-1] + b"\x01")
    ok, issue = verify_unchanged(path, report.sha256)
    assert not ok and issue is not None
    assert issue.code == "file_changed_since_scheduling"
    assert issue.blocking
    assert "review" in issue.message


def test_a_missing_file_is_distinguished_from_a_changed_one(tmp_path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(_clip())
    digest = validate_file(path).sha256
    assert digest is not None
    path.unlink()
    ok, issue = verify_unchanged(path, digest)
    assert not ok and issue is not None
    assert issue.code == "file_missing_since_scheduling"
    assert "no longer present" in issue.message


def test_format_bytes_is_readable_at_every_magnitude() -> None:
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MB"
