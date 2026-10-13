"""Media file inspection: existence, readability, digest, and container metadata.

This is the only module in the application that reads a source clip. It does so
without ever modifying it, and it reports what it actually found rather than what the
file name claimed.

Change detection (``02`` §6.3, FEAS-adjacent; ``04`` §FEAT-041) is
:func:`verify_unchanged`: a clip is hashed when it is added, and re-hashed at every
preflight. A digest mismatch means the file was replaced *after* the operator approved
it, and the upload is refused rather than silently shipping different content under an
approved title.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hexton.domain.media import (
    ALLOWED_EXTENSIONS,
    MAX_SIZE_BYTES,
    MIN_SIZE_BYTES,
    check_extension,
    check_size,
    read_container,
)

CHUNK = 1024 * 1024
#: Head and tail windows scanned for container boxes.
HEAD_WINDOW = 512 * 1024
TAIL_WINDOW = 256 * 1024


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    blocking: bool = True


@dataclass
class MediaReport:
    path: str
    exists: bool = False
    readable: bool = False
    size_bytes: int = 0
    sha256: str | None = None
    container: str | None = None
    brand: str | None = None
    duration_seconds: float | None = None
    content_type: str = "application/octet-stream"
    issues: list[Issue] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return not any(i.blocking for i in self.issues)

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.blocking]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "readable": self.readable,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "container": self.container,
            "brand": self.brand,
            "duration_seconds": self.duration_seconds,
            "content_type": self.content_type,
            "issues": [
                {"code": i.code, "message": i.message, "blocking": i.blocking} for i in self.issues
            ],
            "checked_at": self.checked_at.isoformat(),
        }


def _digest(path: Path, size: int) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        read = 0
        while read < size:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            read += len(block)
    return h.hexdigest()


def _sniff(path: Path, size: int) -> tuple[str | None, str | None, float | None, str | None]:
    try:
        with path.open("rb") as fh:
            head = fh.read(HEAD_WINDOW)
            tail = b""
            if size > HEAD_WINDOW:
                fh.seek(max(0, size - TAIL_WINDOW))
                tail = fh.read(TAIL_WINDOW)
    except OSError as exc:
        return None, None, None, f"Could not read the container header: {exc}"

    info = read_container(head, tail)
    if info is None:
        return (
            None,
            None,
            None,
            (
                "The file does not present an ISO-BMFF or EBML container header, so it is "
                "not recognised as a video clip regardless of its extension."
            ),
        )
    return info.container, info.brand, info.duration_seconds, info.note


def validate_file(path: str | Path) -> MediaReport:
    """Full inspection of a source clip. Never raises for a bad file — it reports."""
    p = Path(path)
    report = MediaReport(path=str(p))

    if not p.exists():
        report.issues.append(Issue("missing_file", f"The file does not exist: {p}"))
        return report
    report.exists = True

    if p.is_dir():
        report.issues.append(Issue("not_a_file", f"{p} is a directory, not a video file."))
        return report

    ext = check_extension(str(p))
    if ext:
        report.issues.append(Issue("bad_extension", ext))

    try:
        size = p.stat().st_size
    except OSError as exc:
        report.issues.append(Issue("stat_failed", f"Could not stat the file: {exc}"))
        return report
    report.size_bytes = size

    size_problem = check_size(size)
    if size_problem:
        report.issues.append(Issue("bad_size", size_problem))

    # Readability: actually open it. A file can exist and stat fine while being
    # locked exclusively by another process or denied by ACL.
    try:
        with p.open("rb") as fh:
            fh.read(1)
    except OSError as exc:
        report.issues.append(
            Issue("unreadable", f"The file could not be opened for reading: {exc}")
        )
        return report
    report.readable = True

    try:
        report.sha256 = _digest(p, size)
    except OSError as exc:
        report.issues.append(Issue("hash_failed", f"Could not compute the SHA-256 digest: {exc}"))
        return report

    container, brand, duration, note = _sniff(p, size)
    if container is None:
        report.issues.append(Issue("bad_container", note or "Unrecognised container."))
    else:
        report.container = container
        report.brand = brand
        report.duration_seconds = duration
        report.content_type = ALLOWED_EXTENSIONS.get(p.suffix.lower(), "application/octet-stream")
        if duration is None and note:
            report.issues.append(Issue("duration_unknown", note, blocking=False))

    return report


def verify_unchanged(path: str | Path, recorded_sha256: str) -> tuple[bool, Issue | None]:
    """Re-hash a clip and compare with what was recorded when it was added.

    Returns ``(True, None)`` when the bytes are identical. Otherwise a blocking issue
    explaining exactly what changed — a *different* file is not the same condition as a
    *missing* one, and an operator debugging a blocked job needs to know which.
    """
    p = Path(path)
    if not p.exists():
        return False, Issue(
            "file_missing_since_scheduling",
            f"The media file is no longer present at {p}. The upload is blocked rather "
            f"than skipped, because publishing without the approved clip is worse than "
            f"not publishing.",
        )
    try:
        size = p.stat().st_size
        current = _digest(p, size)
    except OSError as exc:
        return False, Issue("file_unreadable", f"The media file could not be re-read: {exc}")

    if current != recorded_sha256:
        return False, Issue(
            "file_changed_since_scheduling",
            f"The media file at {p} no longer matches the SHA-256 recorded when it was "
            f"added. Expected {recorded_sha256[:16]}…, found {current[:16]}…. The file "
            f"was replaced or modified after it was approved, so the upload is blocked "
            f"and the job needs review rather than silently publishing different "
            f"content under an approved title.",
        )
    return True, None


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def is_allowed_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ALLOWED_EXTENSIONS


__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_SIZE_BYTES",
    "MIN_SIZE_BYTES",
    "Issue",
    "MediaReport",
    "format_bytes",
    "is_allowed_path",
    "validate_file",
    "verify_unchanged",
]
