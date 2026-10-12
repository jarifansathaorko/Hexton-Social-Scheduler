"""Content-metadata validation.

Limits and edge cases are taken from the Phase 0 documentation pins
(``phase0-poc/hexton_poc/docfacts.py``), which quote the live YouTube reference:

* ``DF-14`` — title 100 characters; no ``<`` or ``>``; description 5000 **bytes**;
  tags 500 characters with a quoting-aware cost.
* ``DF-02`` — ``privacyStatus`` must always be sent explicitly. Omitting it is the
  exact failure mode the whole product exists to avoid, so it is never defaulted
  here; the caller must supply it.

Pure functions. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
VALID_PRIVACY = ("private", "unlisted", "public")


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity = Severity.ERROR

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR


@dataclass(frozen=True)
class ValidationResult:
    issues: tuple[Issue, ...] = ()

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_title(value: str) -> tuple[Issue, ...]:
    out: list[Issue] = []
    text = value or ""
    if not text.strip():
        out.append(Issue("empty_title", "A title is required."))
    if len(text) > TITLE_MAX_CHARS:
        out.append(
            Issue(
                "title_too_long",
                f"The title is {len(text)} characters; YouTube allows {TITLE_MAX_CHARS}.",
            )
        )
    if "<" in text or ">" in text:
        out.append(
            Issue(
                "title_angle_bracket",
                "The title contains an angle bracket, which YouTube rejects.",
            )
        )
    return tuple(out)


def validate_description(value: str) -> tuple[Issue, ...]:
    """Counted in **bytes**, not characters — a euro sign costs three."""
    raw = (value or "").encode("utf-8")
    if len(raw) > DESCRIPTION_MAX_BYTES:
        return (
            Issue(
                "description_too_long",
                f"The description is {len(raw)} bytes; YouTube allows {DESCRIPTION_MAX_BYTES}.",
            ),
        )
    return ()


def tag_cost(tag: str) -> int:
    """One tag's contribution to the documented 500-character limit.

    A tag containing a space is counted *including* the quotes YouTube wraps around
    it, and commas between tags also count. This quoting-aware accounting is the
    difference between a 500-character budget and a 500-tag budget.
    """
    return len(tag) + (2 if " " in tag else 0)


def tag_list_cost(tags: list[str]) -> int:
    if not tags:
        return 0
    return sum(tag_cost(t) for t in tags) + (len(tags) - 1)  # commas


def validate_tags(tags: list[str] | None) -> tuple[Issue, ...]:
    out: list[Issue] = []
    cleaned = [t.strip() for t in (tags or []) if t and t.strip()]
    for t in cleaned:
        if "," in t:
            out.append(
                Issue(
                    "tag_contains_comma",
                    f"The tag {t!r} contains a comma, which splits it into two tags "
                    f"when submitted.",
                    Severity.WARNING,
                )
            )
    cost = tag_list_cost(cleaned)
    if cost > TAGS_MAX_CHARS:
        out.append(
            Issue(
                "tags_too_long",
                f"The tag list costs {cost} characters of the {TAGS_MAX_CHARS} allowed, "
                f"counting quotes around tags that contain spaces and the commas "
                f"between them.",
            )
        )
    return tuple(out)


def validate_privacy(value: str | None) -> tuple[Issue, ...]:
    if value not in VALID_PRIVACY:
        return (
            Issue(
                "invalid_privacy",
                "An explicit privacy status of private, unlisted or public is required. "
                "It is never defaulted, because a server-side default is exactly the "
                "behaviour this product exists to prevent.",
            ),
        )
    return ()


@dataclass(frozen=True)
class ContentMetadata:
    """A validated, fully-specified set of content fields.

    Built only through :func:`validate_metadata`, so an invalid combination cannot
    reach the connector.
    """

    title: str
    description: str
    tags: tuple[str, ...]
    category_id: str
    privacy: str
    issues: tuple[Issue, ...] = field(default=(), repr=False)

    def to_snippet(self) -> dict[str, object]:
        """The complete snippet. Always re-sends every field.

        ``DF-06``: ``videos.update`` deletes any already-valued property omitted from
        the request. Re-asserting the whole snippet is the only safe shape, so there
        is deliberately no way to build a partial one.
        """
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "categoryId": self.category_id,
        }


def validate_metadata(
    *,
    title: str,
    description: str,
    tags: list[str] | None,
    category_id: str,
    privacy: str,
) -> ValidationResult:
    """Validate every content field. Errors block queueing; warnings do not."""
    issues: list[Issue] = []
    issues += validate_title(title)
    issues += validate_description(description)
    issues += validate_tags(tags)
    issues += validate_privacy(privacy)
    if not (category_id or "").strip().isdigit():
        issues.append(Issue("invalid_category", "A numeric YouTube category ID is required."))
    return ValidationResult(tuple(issues))


def build_metadata(
    *,
    title: str,
    description: str,
    tags: list[str] | None,
    category_id: str,
    privacy: str,
) -> ContentMetadata:
    """Validate and construct. Raises :class:`ValueError` if anything blocks."""
    result = validate_metadata(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy=privacy,
    )
    if not result.ok:
        messages = "; ".join(f"{i.code}: {i.message}" for i in result.errors)
        raise ValueError(f"Content metadata is not valid: {messages}")

    cleaned = tuple(t.strip() for t in (tags or []) if t and t.strip())
    return ContentMetadata(
        title=title,
        description=description,
        tags=cleaned,
        category_id=category_id,
        privacy=privacy,
        issues=result.issues,
    )


# --------------------------------------------------------------------------
# Optional, consent-based transformation
# --------------------------------------------------------------------------


def normalise_hashtags(hashtags: list[str] | None) -> list[str]:
    """Fold hashtags into ``#lowercase`` and drop duplicates, preserving order.

    Purely lexical: this never invents a hashtag the operator did not type, and never
    removes one.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in hashtags or []:
        text = raw.strip().lstrip("#")
        if not text:
            continue
        tag = "#" + text.lower()
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def apply_transforms(
    description: str,
    hashtags: list[str] | None,
    *,
    consent: bool,
) -> tuple[str, list[str], list[str]]:
    """Optionally normalise hashtags and append them to the description.

    Returns ``(description, hashtags, change_log)``.

    With ``consent=False`` the description comes back **byte-identical** and the
    hashtags are left where the operator put them. That is the whole point: ``04``
    §FEAT-042 makes transformations opt-in, and a tool that quietly rewrites the text
    an operator carefully wrote is one they stop trusting. The change log exists so
    that when consent *is* given, every edit is visible and reversible.
    """
    normalised = normalise_hashtags(hashtags)
    if not consent:
        return description, list(hashtags or []), []

    log: list[str] = []
    if normalised != list(hashtags or []):
        log.append(f"Hashtags normalised to {', '.join(normalised) or '(none)'}.")
    if not normalised:
        return description, normalised, log

    marker = " ".join(normalised)
    if marker in description:
        log.append("Hashtags were already present in the description; not appended again.")
        return description, normalised, log

    separator = "" if description.endswith(("\n", " ")) else "\n\n"
    log.append(f"Appended {len(normalised)} hashtag(s) to the description.")
    return f"{description}{separator}{marker}", normalised, log


# --------------------------------------------------------------------------
# Request body construction
# --------------------------------------------------------------------------


def build_insert_body(
    *,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
    publish_at: datetime | None,
) -> dict[str, Any]:
    """The complete ``videos.insert`` body.

    Three documented constraints are structurally enforced here rather than left to
    the caller's discipline:

    * ``DF-02`` — ``privacyStatus`` is always present, because a server-side default
      is the exact failure this product exists to prevent.
    * ``DF-03`` — ``publishAt`` is only emitted for a private video, and only in
      ``status`` alongside that privacy.
    * ``DF-06`` — every snippet field is sent every time, because a request that
      omits an already-valued property deletes it.
    """
    if privacy not in VALID_PRIVACY:
        raise ValueError(f"privacy must be one of {VALID_PRIVACY}; got {privacy!r}")
    if publish_at is not None and privacy != "private":
        raise ValueError(
            "publishAt is only meaningful with privacyStatus='private'; sending both "
            "for another visibility would have YouTube publish immediately or ignore "
            "the time entirely."
        )

    body: dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": list(tags),
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    if publish_at is not None:
        # RFC3339 UTC. The provider is documented to read this in UTC, so an offset
        # is never carried across; the operator's zone is a display concern only.
        body["status"]["publishAt"] = publish_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return body
