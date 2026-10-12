"""Metadata validation and the scheduling preflight.

Everything here is derived from documented limits (see ``docfacts.DF-16``) and from the
safety rules in ``01-standard-prd.md`` section 7. No network I/O.

The single most important rule in this module: **a target publication time in the past is
hard-blocked**, because `status.publishAt` in the past is documented to publish the video
immediately (``docfacts.DF-04``).
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

TITLE_MAX_CHARS = 100                      # DF-16
DESCRIPTION_MAX_BYTES = 5000               # DF-16
TAGS_MAX_CHARS = 500                       # DF-16 (comma-separated, quoting-aware)

#: Conservative configured minimum lead time until FEAS-03 measures a real one.
DEFAULT_MIN_LEAD_MINUTES = 30
#: Rejections above this are treated as surprising and must be classified, not retried.
WARNING_BAND_MINUTES = 120

ALLOWED_PRIVACY = ("private", "unlisted", "public")
DEFAULT_PRIVACY = "private"                # 01 §7: never rely on a server default.

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"}
_MIN_VIDEO_BYTES = 1024


class Severity(str, Enum):
    ERROR = "error"      # blocks queueing
    WARNING = "warning"  # queueable, but surfaced


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    message: str
    field: str = ""


# --------------------------------------------------------------------------
# Metadata limits
# --------------------------------------------------------------------------

def validate_title(title: str) -> list[Issue]:
    issues: list[Issue] = []
    if not title or not title.strip():
        issues.append(Issue("empty_title", Severity.ERROR, "Title is required.", "title"))
        return issues
    if len(title) > TITLE_MAX_CHARS:
        issues.append(
            Issue(
                "title_too_long",
                Severity.ERROR,
                f"Title is {len(title)} characters; the documented limit is {TITLE_MAX_CHARS}. "
                "It will not be truncated automatically - shorten it yourself.",
                "title",
            )
        )
    for ch in "<>":
        if ch in title:
            issues.append(
                Issue("title_angle_bracket", Severity.ERROR,
                      f"Title must not contain '{ch}'.", "title")
            )
    return issues


def validate_description(description: str) -> list[Issue]:
    issues: list[Issue] = []
    size = len(description.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        issues.append(
            Issue(
                "description_too_long",
                Severity.ERROR,
                f"Description is {size} bytes; the documented limit is "
                f"{DESCRIPTION_MAX_BYTES} bytes. It will not be truncated automatically.",
                "description",
            )
        )
    for ch in "<>":
        if ch in description:
            issues.append(
                Issue("description_angle_bracket", Severity.ERROR,
                      f"Description must not contain '{ch}'.", "description")
            )
    return issues


def _tag_list_cost(tags: list[str]) -> int:
    """Documented tag accounting (DF-16): commas count, and a tag containing a space is
    counted as if quotation marks wrapped it (``Foo-Baz`` = 7 chars, ``Foo Baz`` = 9)."""
    if not tags:
        return 0
    total = 0
    for i, tag in enumerate(tags):
        cost = len(tag)
        if " " in tag:
            cost += 2                      # the API treats it as quoted
        total += cost
        if i:                              # comma separators count toward the limit
            total += 1
    return total


def validate_tags(tags: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    if not tags:
        return issues
    cost = _tag_list_cost(tags)
    if cost > TAGS_MAX_CHARS:
        issues.append(
            Issue(
                "tags_too_long",
                Severity.ERROR,
                f"Tags cost {cost} characters against the documented {TAGS_MAX_CHARS} limit "
                "(commas and per-tag quoting included). Remove tags rather than have them "
                "silently dropped.",
                "tags",
            )
        )
    return issues


# --------------------------------------------------------------------------
# Media validation
# --------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def validate_media(path: Path, *, expected_sha256: str | None = None) -> tuple[list[Issue], int, str]:
    """Validate that the selected video exists and is readable.

    Returns ``(issues, size, sha256)``. Never raises for a missing/unreadable file -
    the caller must be able to show a reason rather than crash.
    """
    issues: list[Issue] = []
    if not path.exists():
        issues.append(Issue("missing_file", Severity.ERROR, f"File not found: {path}", "media"))
        return issues, 0, ""
    if not path.is_file():
        issues.append(Issue("not_a_file", Severity.ERROR, f"Not a file: {path}", "media"))
        return issues, 0, ""
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except OSError as exc:
        issues.append(Issue("unreadable_file", Severity.ERROR,
                            f"Cannot read {path}: {exc}", "media"))
        return issues, 0, ""

    if size < _MIN_VIDEO_BYTES:
        issues.append(Issue("file_too_small", Severity.ERROR,
                            f"File is {size} bytes; too small to be a video.", "media"))
    if path.suffix.lower() not in _VIDEO_EXTENSIONS:
        issues.append(Issue(
            "unexpected_extension", Severity.WARNING,
            f"Extension '{path.suffix}' is not a common video extension; the API accepts "
            "video/* or application/octet-stream.", "media"))
    if expected_sha256 and expected_sha256.lower() != digest:
        issues.append(Issue(
            "file_changed", Severity.ERROR,
            "The file's SHA-256 no longer matches the value recorded when it was added; it "
            "has changed on disk. Re-approve it before queueing.", "media"))
    return issues, size, digest


# --------------------------------------------------------------------------
# Scheduling preflight
# --------------------------------------------------------------------------

def validate_privacy(privacy: str) -> list[Issue]:
    """Never rely on a server-side default (DF-02, 01 §7)."""
    issues: list[Issue] = []
    if privacy not in ALLOWED_PRIVACY:
        issues.append(Issue("invalid_privacy", Severity.ERROR,
                            f"privacy must be one of {ALLOWED_PRIVACY}; got {privacy!r}. "
                            "The application always sends it explicitly.", "privacy"))
    return issues


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


def preflight(
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy: str,
    publish_at: datetime | None,
    now: datetime,
    min_lead_minutes: int = DEFAULT_MIN_LEAD_MINUTES,
    never_published: bool = True,
) -> PreflightResult:
    """Gate a job before any network request is issued.

    Hard rules:

    * ``privacy`` must be present and explicit.
    * A ``publish_at`` in the past (or too close to now) is **blocked**, never silently
      accepted - a past ``publishAt`` is documented to publish immediately (DF-04).
    * ``publish_at`` is only valid on a video that has never been published (DF-03).
    """
    issues: list[Issue] = []
    issues += validate_title(title)
    issues += validate_description(description)
    issues += validate_tags(tags)
    issues += validate_privacy(privacy)

    if publish_at is not None:
        if publish_at.tzinfo is None:
            issues.append(Issue("naive_datetime", Severity.ERROR,
                                "publish_at must be timezone-aware; the application stores "
                                "UTC and refuses to guess.", "publish_at"))
        else:
            publish_utc = publish_at.astimezone(timezone.utc)
            now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
            delta = (publish_utc - now_utc).total_seconds()

            if delta <= 0:
                issues.append(Issue(
                    "publish_at_in_past", Severity.ERROR,
                    f"Publish time {publish_utc.isoformat()} is in the past. A past "
                    "status.publishAt publishes the video immediately, so this is blocked "
                    "rather than accepted. Choose a future time or explicitly confirm "
                    "immediate publication through a separate, labelled action.",
                    "publish_at"))
            elif delta < min_lead_minutes * 60:
                issues.append(Issue(
                    "publish_at_too_soon", Severity.ERROR,
                    f"Publish time is only {int(delta // 60)} minutes away; the configured "
                    f"minimum lead time is {min_lead_minutes} minutes. The real minimum is "
                    "not documented and is measured by FEAS-03.",
                    "publish_at"))
            elif delta < WARNING_BAND_MINUTES * 60:
                issues.append(Issue(
                    "publish_at_near_lead", Severity.WARNING,
                    f"Publish time is {int(delta // 60)} minutes away, inside the "
                    f"{WARNING_BAND_MINUTES}-minute warning band. YouTube may still reject it "
                    "with invalidPublishAt.",
                    "publish_at"))

        if not never_published:
            issues.append(Issue(
                "already_published", Severity.ERROR,
                "status.publishAt can only be set on a video that has never been published "
                "(DF-03). This video has already been published; rescheduling is not "
                "available through the documented API.",
                "publish_at"))

    return PreflightResult(ok=not any(i.severity is Severity.ERROR for i in issues),
                           issues=issues)


def build_insert_body(
    *,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
    publish_at: datetime | None,
    self_declared_made_for_kids: bool | None = None,
) -> dict:
    """Assemble the `videos.insert` metadata body.

    ``status.privacyStatus`` is **always** written explicitly (DF-02). ``publishAt`` is
    included only when the caller has already passed :func:`preflight`, which enforces
    the private + future + never-published constraints (DF-03, DF-04).
    """
    if privacy not in ALLOWED_PRIVACY:
        raise ValueError(f"refusing to build a body with privacy={privacy!r}")

    body: dict = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,   # explicit, always
            "embeddable": True,
            "license": "youtube",
        },
    }
    if tags:
        body["snippet"]["tags"] = list(tags)
    if publish_at is not None:
        if privacy != "private":
            raise ValueError("publishAt may only be set when privacyStatus is private (DF-03)")
        if publish_at.tzinfo is None:
            raise ValueError("publishAt must be timezone-aware")
        body["status"]["publishAt"] = publish_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
    if self_declared_made_for_kids is not None:
        body["status"]["selfDeclaredMadeForKids"] = self_declared_made_for_kids
    return body


def normalise_unicode(text: str) -> str:
    """Used only to make validation deterministic across NFC/NFD input."""
    return unicodedata.normalize("NFC", text)
