"""Privacy-policy acceptance, the disable-versus-revoke distinction, and diagnostics.

``02-technical-prd.md`` §10 pins six behaviours here, and they are the ones most likely
to be quietly dropped under schedule pressure, so they live in one auditable module:

``COMP-01``  A privacy-policy acceptance gate sits in front of every API-connected
             feature. Acceptance is recorded against a *version*, so a revised policy
             re-closes the gate rather than silently inheriting an old "yes".
``COMP-02``  The required YouTube and Google policy links are shown, and they are the
             links from the documentation pins, not paraphrases.
``COMP-06``  Optional modification of operator-supplied metadata is opt-in and is
             recorded per job, per owner. There is no global "transform my titles"
             default, because the default must be *no*.
``COMP-07``  Disabling a connection and revoking authorization are different operations
             with different consequences. Disabling stops the worker using a connection
             and erases nothing. Revoking deletes credentials and Authorized Data.
``COMP-10``  A diagnostic distinguishes "there is no credential" from "the credential
             exists and the remote call failed". Conflating them sends the operator to
             the wrong fix.

Also implements ``D-1`` as written: client-initiated revocation deletes Authorized Data
within **7 calendar days**; Google-side revocation or an unrefreshable token within **30
calendar days**. Both numbers are documentation pins (``DF-20``/``DF-21``) and neither
may be lengthened here.

Pure functions. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

#: Bump when the operator-facing policy text changes materially. A recorded acceptance
#: at an older version no longer opens the gate.
PRIVACY_POLICY_VERSION = "1.0"

#: ``D-1``, ``DF-20``: deletion within 7 calendar days of a client-initiated revocation.
CLIENT_REVOCATION_RETENTION_DAYS = 7

#: ``D-1``, ``DF-21``: deletion within 30 calendar days of Google-side revocation, or of
#: a refresh token that can no longer be refreshed.
GOOGLE_REVOCATION_RETENTION_DAYS = 30


class Feature(str, Enum):
    """An API-connected feature that the privacy gate guards."""

    YOUTUBE_CONNECTION = "youtube_connection"
    SHEETS_REPORTING = "sheets_reporting"
    MENTION_WORKFLOW = "mention_workflow"


#: ``COMP-02``. Shown verbatim wherever a connection is offered.
POLICY_LINKS: dict[str, str] = {
    "Google API Services User Data Policy": "https://developers.google.com/terms/api-services-user-data-policy",
    "YouTube Terms of Service": "https://www.youtube.com/t/terms",
    "Google Privacy Policy": "https://policies.google.com/privacy",
    "YouTube Data API terms and quotas": "https://developers.google.com/youtube/terms/api-services-terms-of-service",
    "Google Cloud user data compliance": "https://cloud.google.com/data-privacy-and-compliance",
}

#: What the operator is agreeing to, in the order the UI must present it.
PRIVACY_COMMITMENTS: tuple[str, ...] = (
    "Hexton stores YouTube and Google credentials on this Windows machine only. They are "
    "never sent to a Hexton server, because there is no Hexton server.",
    "Credentials are held in the Windows Credential Manager where available, falling "
    "back to an encrypted DPAPI file. Hexton never writes them in plaintext unless you "
    "set an explicit, documented override.",
    "Hexton uses the YouTube Data API v3 and the Google Sheets API only for the "
    "operations it performs and shows you. It does not read, publish, or modify any "
    "other part of your Google account.",
    "Test uploads are private unless you explicitly authorize a public test on a "
    "designated test channel.",
    "You can revoke authorization at any time. Revoking deletes the credentials Hexton "
    "holds and starts the Authorized-Data deletion clock.",
    "When Google's policies require deletion, Hexton deletes within 7 calendar days of a "
    "revocation you start, and within 30 calendar days when Google revokes the grant or "
    "the grant can no longer be refreshed.",
)


@dataclass(frozen=True)
class PolicyAcceptance:
    """A recorded acceptance. ``version`` is what makes a revised policy re-close the gate."""

    version: str
    accepted_at: datetime | None
    #: Free-text name of the person who accepted, so an audit can attribute the decision.
    accepted_by: str = ""


def has_accepted(acceptance: PolicyAcceptance | None) -> bool:
    """Whether the *current* policy version has been accepted.

    An acceptance at an older version does not count. This is deliberate: a policy that
    changed because the data handling changed must be re-read, and a silently inherited
    "yes" is not consent.
    """
    if acceptance is None or acceptance.accepted_at is None:
        return False
    return acceptance.version == PRIVACY_POLICY_VERSION


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str


def gate(acceptance: PolicyAcceptance | None, feature: Feature) -> GateResult:
    """Whether ``feature`` may be used. The single gate for every API-connected feature."""
    if has_accepted(acceptance):
        return GateResult(True, f"Privacy policy {PRIVACY_POLICY_VERSION} accepted.")
    return GateResult(
        False,
        f"Accept the privacy policy ({PRIVACY_POLICY_VERSION}) before using "
        f"{feature.value.replace('_', ' ')}. Hexton will not touch your Google account "
        "without that agreement on record.",
    )


def require_gate(acceptance: PolicyAcceptance | None, feature: Feature) -> None:
    """:func:`gate`, raising instead of returning. For service-layer entry points."""
    result = gate(acceptance, feature)
    if not result.allowed:
        raise PrivacyPolicyRequired(result.reason)


class PrivacyPolicyRequired(PermissionError):
    """Raised when an API-connected feature is used before the policy is accepted."""


# --------------------------------------------------------------------------
# COMP-06 - optional metadata modification is opt-in, per job, per owner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetadataConsent:
    """Consent to transform operator-supplied metadata. Default is always **no**."""

    #: Applies to titles only. Descriptions are never rewritten by default, because a
    #: description can carry a manually created mention (DF-19) that a rewrite destroys.
    transform_titles: bool = False
    transform_descriptions: bool = False
    #: Recorded per job, per owner, so a blanket setting cannot be smuggled in later.
    granted_by: str = ""
    granted_at: datetime | None = None

    @property
    def any(self) -> bool:
        return self.transform_titles or self.transform_descriptions


def may_transform(consent: MetadataConsent, field_name: str) -> bool:
    """Whether ``field_name`` may be modified. Anything not named is refused."""
    if field_name == "title":
        return consent.transform_titles
    if field_name == "description":
        return consent.transform_descriptions
    return False


#: The metadata fields this application will *never* modify, whatever the consent says.
#: A description can hold a mention created by hand in Studio, and the API cannot read
#: that mention back, so a rewrite is an undetectable destruction.
NEVER_TRANSFORM_FIELDS: frozenset[str] = frozenset({"privacyStatus", "publishAt", "tags"})


# --------------------------------------------------------------------------
# COMP-07 - disabling is not revoking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DisableOutcome:
    """What disabling a connection does. Deliberately small."""

    stops_scheduling: bool = True
    stops_existing_jobs: bool = True
    deletes_credentials: bool = False
    deletes_bookkeeping: bool = False
    revokes_grant: bool = False
    explanation: str = (
        "Disabling stops Hexton using this connection. Nothing is deleted: credentials "
        "stay stored, historical bookkeeping stays intact, and the authorization is "
        "left alone. Re-enable at any time without reauthorizing."
    )


@dataclass(frozen=True)
class RevokeOutcome:
    """What revoking authorization does. The opposite shape on purpose."""

    stops_scheduling: bool = True
    stops_existing_jobs: bool = True
    deletes_credentials: bool = True
    deletes_bookkeeping: bool = False
    revokes_grant: bool = True
    #: ``D-1``: how long Authorized Data may survive, in calendar days.
    authorized_data_retention_days: int = CLIENT_REVOCATION_RETENTION_DAYS
    explanation: str = (
        "Revoking removes the authorization. Hexton deletes the stored credentials, "
        "asks Google to invalidate the grant, and deletes the Authorized Data it holds "
        "within 7 calendar days (DF-20). Historical bookkeeping — what ran, when, and "
        "what it reported — is kept, because it is your record and contains no "
        "Authorized Data."
    )


def disable_outcome() -> DisableOutcome:
    return DisableOutcome()


def revoke_outcome(*, initiated_by: str = "operator") -> RevokeOutcome:
    """The outcome of a revocation, with the retention deadline that follows from it.

    ``initiated_by`` matters: a Google-initiated revocation or an unrefreshable token is
    the 30-calendar-day case (``DF-21``), not the 7-day one. Getting this wrong is how a
    retention window silently doubles.
    """
    days = (
        GOOGLE_REVOCATION_RETENTION_DAYS
        if initiated_by in ("google", "unrefreshable")
        else CLIENT_REVOCATION_RETENTION_DAYS
    )
    return RevokeOutcome(authorized_data_retention_days=days)


# --------------------------------------------------------------------------
# COMP-10 - diagnostics that distinguish the two failure families
# --------------------------------------------------------------------------


class DiagnosticCode(str, Enum):
    NOT_CONNECTED = "not_connected"  # no credential exists at all
    DISABLED = "disabled"  # connected, but switched off by the operator
    POLICY_NOT_ACCEPTED = "policy_not_accepted"  # the gate is closed
    CREDENTIAL_UNUSABLE = "credential_unusable"  # stored, but expired/revoked/invalid
    REMOTE_FAILED = "remote_failed"  # the credential was fine; the request failed
    OK = "ok"


@dataclass(frozen=True)
class Diagnostic:
    """One classified answer to "why did this not work?".

    ``NOT_CONNECTED`` and ``REMOTE_FAILED`` send the operator to completely different
    fixes, and Phase 2's review showed the two were being conflated in user-facing
    text. Keeping them apart here is the fix, and it is the only place that classification
    is made.
    """

    code: DiagnosticCode
    #: What the operator should do next, in one sentence.
    remedy: str
    #: Whether retrying the same request could plausibly succeed without operator action.
    retry_worthwhile: bool = False
    detail: str = ""


def diagnose(
    *,
    connected: bool,
    policy_accepted: bool,
    enabled: bool = True,
    credential_present: bool = True,
    credential_expired: bool = False,
    remote_error: str = "",
) -> Diagnostic:
    """Classify the state of a connection. Order matters: earliest cause wins."""
    if not policy_accepted:
        return Diagnostic(
            DiagnosticCode.POLICY_NOT_ACCEPTED,
            "Accept the privacy policy in Settings, then try again.",
        )
    if not connected or not credential_present:
        return Diagnostic(
            DiagnosticCode.NOT_CONNECTED,
            "No credential is stored for this connection. Connect Google in Settings; "
            "nothing has been attempted remotely.",
        )
    if not enabled:
        return Diagnostic(
            DiagnosticCode.DISABLED,
            "This connection is disabled. Re-enable it in Settings; no authorization is "
            "needed because disabling does not revoke.",
        )
    if credential_expired:
        return Diagnostic(
            DiagnosticCode.CREDENTIAL_UNUSABLE,
            "The stored credential can no longer authorize a request. Reauthorize in "
            "Settings; the remote service was not asked to do anything.",
        )
    if remote_error:
        return Diagnostic(
            DiagnosticCode.REMOTE_FAILED,
            "The credential was accepted but the remote request failed. This is a "
            "service or network problem, not an authorization problem — reauthorizing "
            "will not help.",
            retry_worthwhile=True,
            detail=remote_error,
        )
    return Diagnostic(DiagnosticCode.OK, "Connected and reachable.")


# --------------------------------------------------------------------------
# The written policy, kept beside the code that enforces it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionPolicy:
    """What is kept, for how long, and why. ``02-technical-prd.md`` §10."""

    client_revocation_days: int = CLIENT_REVOCATION_RETENTION_DAYS
    google_revocation_days: int = GOOGLE_REVOCATION_RETENTION_DAYS
    #: Rows this application writes about its own operation. Kept: they are the
    #: operator's record and contain no Authorized Data.
    operational_bookkeeping: str = "retained until the operator deletes it"
    #: Credentials and channel identities obtained under authorization.
    authorized_data: str = "deleted within the applicable D-1 deadline"
    #: Backups inherit the same limits. A backup that outlives the deletion deadline is
    #: itself a retention breach, so the sweep note is part of the policy.
    backups: str = (
        "a backup taken before a revocation must be deleted on the same deadline as the "
        "live data; the retention sweep records the deadline against the backup set"
    )
    notes: tuple[str, ...] = field(
        default_factory=lambda: (
            "A report already delivered to a Google Sheet is outside Hexton's control "
            "once written. Deleting it from the sheet is an operator action, and Hexton "
            "will not edit or delete rows it did not create.",
            "Sync Keys are derived from the report date, the schedule ID and the schema "
            "version. They carry no personal data.",
        )
    )


def retention_policy() -> RetentionPolicy:
    return RetentionPolicy()


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CLIENT_REVOCATION_RETENTION_DAYS",
    "GOOGLE_REVOCATION_RETENTION_DAYS",
    "NEVER_TRANSFORM_FIELDS",
    "POLICY_LINKS",
    "PRIVACY_COMMITMENTS",
    "PRIVACY_POLICY_VERSION",
    "Diagnostic",
    "DiagnosticCode",
    "DisableOutcome",
    "Feature",
    "GateResult",
    "MetadataConsent",
    "PolicyAcceptance",
    "PrivacyPolicyRequired",
    "RetentionPolicy",
    "RevokeOutcome",
    "diagnose",
    "disable_outcome",
    "gate",
    "has_accepted",
    "may_transform",
    "require_gate",
    "retention_policy",
    "revoke_outcome",
    "utcnow",
]
