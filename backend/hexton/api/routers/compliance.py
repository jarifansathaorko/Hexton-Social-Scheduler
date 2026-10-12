"""Compliance surfaces: the privacy gate, disable-versus-revoke, diagnostics, retention.

``02-technical-prd.md`` §10 (COMP-01..COMP-10), ``DF-20``/``DF-21``.

This router exists so that the compliance behaviour is reachable and testable rather than
being prose in a document. Three things it deliberately does *not* do:

* It never returns a credential, a token, or anything derived from one.
* It never makes "disable" and "revoke" available under one endpoint. They are different
  operations with different consequences, and the Phase 2 review was explicit that
  conflating them is how an operator loses a working authorization by accident.
* It never claims data was deleted that it has only *scheduled* for deletion. Recording
  the deadline and confirming the purge are separate steps, on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hexton.api.deps import CtxDep, SessionDep
from hexton.api.schemas_phase3 import (
    ConnectionActionOut,
    DiagnosticOut,
    PolicyAcceptIn,
    PolicyStatusOut,
)
from hexton.bootstrap import AppContext
from hexton.compliance.policy import (
    CLIENT_REVOCATION_RETENTION_DAYS,
    GOOGLE_REVOCATION_RETENTION_DAYS,
    POLICY_LINKS,
    PRIVACY_COMMITMENTS,
    PRIVACY_POLICY_VERSION,
    DiagnosticCode,
    DisableOutcome,
    Feature,
    PolicyAcceptance,
    RevokeOutcome,
    diagnose,
    disable_outcome,
    gate,
    retention_policy,
    revoke_outcome,
)
from hexton.db.models import DataRetention, PlatformConnection, PolicyAcceptanceRecord

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


def _latest_acceptance(session: Session) -> PolicyAcceptance | None:
    record = session.scalars(
        select(PolicyAcceptanceRecord)
        .where(PolicyAcceptanceRecord.superseded_at.is_(None))
        .order_by(PolicyAcceptanceRecord.accepted_at.desc())
        .limit(1)
    ).one_or_none()
    if record is None:
        return None
    return PolicyAcceptance(
        version=record.version,
        accepted_at=record.accepted_at,
        accepted_by=record.accepted_by,
    )


# --------------------------------------------------------------------------
# COMP-01 / COMP-02 - the gate and the policy
# --------------------------------------------------------------------------


@router.get("/policy", response_model=PolicyStatusOut)
def policy_status(session: Session = SessionDep) -> PolicyStatusOut:
    acceptance = _latest_acceptance(session)
    return PolicyStatusOut(
        accepted=bool(acceptance and acceptance.version == PRIVACY_POLICY_VERSION),
        version=PRIVACY_POLICY_VERSION,
        accepted_at=acceptance.accepted_at if acceptance else None,
        accepted_by=acceptance.accepted_by if acceptance else "",
    )


@router.post("/policy/accept", response_model=PolicyStatusOut)
def accept_policy(payload: PolicyAcceptIn, session: Session = SessionDep) -> PolicyStatusOut:
    """Record the acceptance, superseding any earlier version.

    Superseding rather than overwriting matters: an audit trail that shows only the
    latest "yes" cannot answer "did they agree to the old text, and when did that stop
    applying?".
    """
    now = datetime.now(UTC)
    for existing in session.scalars(
        select(PolicyAcceptanceRecord).where(PolicyAcceptanceRecord.superseded_at.is_(None))
    ):
        existing.superseded_at = now
    session.add(
        PolicyAcceptanceRecord(
            owner_id="local-operator",
            version=PRIVACY_POLICY_VERSION,
            accepted_by=payload.accepted_by.strip(),
            accepted_at=now,
        )
    )
    session.commit()
    return policy_status(session)


@router.get("/policy/detail", response_model=dict)
def policy_detail() -> dict:
    """The commitments and the required policy links (``COMP-02``)."""
    return {
        "version": PRIVACY_POLICY_VERSION,
        "commitments": list(PRIVACY_COMMITMENTS),
        "links": dict(POLICY_LINKS),
        "retention": {
            "client_revocation_days": CLIENT_REVOCATION_RETENTION_DAYS,
            "google_revocation_days": GOOGLE_REVOCATION_RETENTION_DAYS,
            "note": retention_policy().backups,
            "policy": retention_policy(),
        },
    }


@router.post("/policy/check/{feature}", response_model=PolicyStatusOut)
def check_feature(feature: str, session: Session = SessionDep) -> PolicyStatusOut:
    """Whether ``feature`` may be used right now, and why not when it may not."""
    try:
        named = Feature(feature)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown feature {feature!r}.") from exc
    status = policy_status(session)
    result = gate(
        PolicyAcceptance(
            version=status.version,
            accepted_at=status.accepted_at,
            accepted_by=status.accepted_by,
        )
        if status.accepted
        else None,
        named,
    )
    if not result.allowed:
        raise HTTPException(status_code=403, detail=result.reason)
    return status


# --------------------------------------------------------------------------
# COMP-07 - disabling is not revoking
# --------------------------------------------------------------------------


def _load(session: Session, connection_id: int) -> PlatformConnection:
    conn = session.execute(
        select(PlatformConnection)
        .where(PlatformConnection.id == connection_id)
        .options(selectinload(PlatformConnection.identities))
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} does not exist.")
    return conn


def _outcome_payload(conn: PlatformConnection, outcome: DisableOutcome | RevokeOutcome) -> dict:
    did: list[str] = []
    did_not: list[str] = []
    if outcome.stops_scheduling:
        did.append("Stopped Hexton scheduling or delivering anything new for this connection.")
    if outcome.stops_existing_jobs:
        did.append("Stopped in-flight work on this connection at its next safe point.")
    if outcome.deletes_credentials:
        did.append("Deleted the stored credentials from this machine.")
    if outcome.revokes_grant:
        did.append("Asked Google to invalidate the authorization.")
    if not outcome.deletes_credentials:
        did_not.append("Did not delete any credential.")
    if not outcome.revokes_grant:
        did_not.append("Did not revoke the Google authorization — it is still valid.")
    if not outcome.deletes_bookkeeping:
        did_not.append("Did not erase historical bookkeeping; your record is intact.")
    return {
        "id": conn.id,
        "status": conn.status,
        "did": did,
        "did_not": did_not,
        "explanation": outcome.explanation,
        "authorized_data_retention_days": getattr(outcome, "authorized_data_retention_days", None),
    }


@router.post("/connections/{connection_id}/disable", response_model=ConnectionActionOut)
def disable(connection_id: int, session: Session = SessionDep) -> ConnectionActionOut:
    """``COMP-07``: stop using a connection, erase nothing, talk to nobody."""
    conn = _load(session, connection_id)
    if conn.status == "revoked":
        raise HTTPException(
            status_code=409,
            detail="This connection was revoked. Disabling does not apply to a revoked "
            "connection — reauthorize it instead.",
        )
    outcome = disable_outcome()
    conn.status = "disabled"
    conn.disabled_at = datetime.now(UTC)
    session.commit()
    return ConnectionActionOut(**_outcome_payload(conn, outcome))


@router.post("/connections/{connection_id}/enable", response_model=ConnectionActionOut)
def enable(connection_id: int, session: Session = SessionDep) -> ConnectionActionOut:
    conn = _load(session, connection_id)
    if conn.status == "revoked" or conn.authorized_data_deleted_at is not None:
        # ``status`` alone is not enough. The retention sweep leaves a purged connection
        # as ``deleted``, and a connection whose Authorized Data has been confirmed gone
        # has nothing left to re-enable — letting it back in would look like a successful
        # reauthorization of a grant that no longer exists.
        raise HTTPException(
            status_code=409,
            detail="A revoked connection cannot be re-enabled; reauthorize it instead.",
        )
    if conn.granted_at is None:
        # Same rule as :func:`hexton.api.routers.connections.enable_connection`. The
        # two endpoints used to disagree here: this one would flip a never-authorized
        # connection straight to ``active``, presenting it as connected when no grant
        # and no credential ever existed.
        raise HTTPException(
            status_code=409,
            detail="This connection has never been authorized. Authorize it first.",
        )
    conn.status = "active"
    conn.disabled_at = None
    session.commit()
    return ConnectionActionOut(
        id=conn.id,
        status=conn.status,
        did=["Re-enabled the connection."],
        did_not=[
            "Did not reauthorize anything, because disabling never revoked the grant.",
        ],
        explanation="Disabling is reversible precisely because it never touched the "
        "authorization. Nothing needed re-authorizing.",
    )


@router.post("/connections/{connection_id}/revoke", response_model=ConnectionActionOut)
def revoke(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionActionOut:
    """``COMP-07`` + ``D-1``: revoke, delete credentials, start the deletion clock."""
    conn = _load(session, connection_id)
    outcome = revoke_outcome(initiated_by="operator")
    now = datetime.now(UTC)

    key = f"conn:{conn.public_id}"
    from contextlib import suppress

    from hexton.connectors.base import ConnectorError

    for name in ("access", "refresh"):
        token = ctx.credentials.read(f"{key}:{name}")
        if token:
            # A failed remote revoke does not cancel the local obligation; the deletion
            # clock runs either way, so this is best-effort and never blocking.
            with suppress(ConnectorError):
                ctx.connector.revoke(token)
        ctx.credentials.delete(f"{key}:{name}")

    conn.status = "revoked"
    conn.revoked_at = now
    conn.revoked_by = "client"
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=now + timedelta(days=outcome.authorized_data_retention_days),
        )
    )
    session.commit()
    return ConnectionActionOut(**_outcome_payload(conn, outcome))


# --------------------------------------------------------------------------
# COMP-10 - diagnostics
# --------------------------------------------------------------------------


@router.get("/connections/{connection_id}/diagnostic", response_model=DiagnosticOut)
def diagnostic(
    connection_id: int,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> DiagnosticOut:
    """Distinguish "no credential" from "the request failed" (``COMP-10``).

    These send the operator to completely different fixes. Conflating them — which the
    Phase 2 review found happening in user-facing text — sends them to reauthorize when
    the real problem is a network blip, and they come away believing they fixed it.

    ``credential_present`` is read from the **credential store**, not inferred from the
    row's status. A connection can be marked active while its token has already been
    deleted — by a partial revoke, by a purge, or by a manual clear of the store — and
    reporting "Connected and reachable." in that state is exactly the misdiagnosis
    ``COMP-10`` exists to prevent.
    """
    conn = _load(session, connection_id)
    acceptance = _latest_acceptance(session)
    key = f"conn:{conn.public_id}"
    credential_present = any(
        ctx.credentials.read(f"{key}:{name}") for name in ("access", "refresh")
    )
    result = diagnose(
        connected=conn.granted_at is not None,
        policy_accepted=bool(acceptance and acceptance.version == PRIVACY_POLICY_VERSION),
        enabled=conn.status == "active",
        credential_present=credential_present,
        credential_expired=conn.status == "expired",
        remote_error="" if conn.last_token_check_ok is not False else "the last token check failed",
    )
    return DiagnosticOut(
        code=result.code.value,
        remedy=result.remedy,
        retry_worthwhile=result.retry_worthwhile,
        detail=result.detail,
    )


@router.get("/diagnostics/codes", response_model=list[str])
def diagnostic_codes() -> list[str]:
    """Every diagnostic code, so the UI can render a stable label per code."""
    return [code.value for code in DiagnosticCode]


__all__ = ["router"]
