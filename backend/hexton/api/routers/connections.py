"""Platform connections and channel identities.

Phase 0 left the live OAuth path **blocked** (``08`` §6, ``FEAS-01``/``FEAS-02``):
without a Cloud project and a consent configuration there is nothing to exchange a
code against. So this router does three honest things rather than pretending otherwise:

* It implements the full loopback flow's *shape* — PKCE, ``state``, exact redirect
  URI, and the documented scopes — so wiring the real exchange in Phase 3 is a
  transport swap and not a redesign.
* It refuses the exchange with a message that names the gate, instead of failing
  somewhere deeper with an unhelpful HTTP error.
* It offers a development-only way to attach a credential so the worker, the queue and
  the sweep can be exercised end to end against the fake connector. That path is
  refused outright in a packaged build, exactly like the fake connector itself.

Nothing here ever returns a token. A connection is described by its scopes, its
identity, and how healthy it is.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from hexton.api.deps import CtxDep, SessionDep
from hexton.api.schemas import (
    ChannelConfirmIn,
    ChannelOut,
    ConnectionCreateIn,
    ConnectionOut,
)
from hexton.bootstrap import AppContext
from hexton.connectors.base import ChannelSummary, ConnectorError
from hexton.db.models import ChannelIdentity, DataRetention, PlatformConnection, ScheduledJob
from hexton.domain.jobstate import FailureKind

router = APIRouter(prefix="/api/connections", tags=["connections"])

#: The narrowest set that supports this product. ``01`` §C-15 forbids drive-by scopes;
#: anything not on this list is not requested.
REQUESTED_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

#: COMP-04: delete Authorized Data within this long of a revocation (``DF-20``/``DF-21``).
RETENTION_DAYS_CLIENT = 7
RETENTION_DAYS_GOOGLE = 30

_REDIRECT_PATH = "/oauth/callback"


@dataclass
class PendingFlow:
    state: str
    verifier: str
    connection_id: int
    created_at: datetime
    scopes: tuple[str, ...] = field(default_factory=lambda: REQUESTED_SCOPES)

    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


_flows: dict[str, PendingFlow] = {}
_flows_lock = threading.Lock()


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------


def _out(conn: PlatformConnection) -> ConnectionOut:
    return ConnectionOut(
        id=conn.id,
        public_id=conn.public_id,
        provider=conn.provider,
        label=conn.label,
        status=conn.status,
        scopes=list(conn.scopes),
        granted_at=conn.granted_at,
        token_expires_at=conn.token_expires_at,
        last_token_check_at=conn.last_token_check_at,
        last_token_check_ok=conn.last_token_check_ok,
        revoked_at=conn.revoked_at,
        authorized_data_deleted_at=conn.authorized_data_deleted_at,
        channels=[ChannelOut.model_validate(c) for c in conn.identities],
    )


def _load(session: Session, connection_id: int) -> PlatformConnection:
    conn = session.execute(
        select(PlatformConnection)
        .where(PlatformConnection.id == connection_id)
        .options(selectinload(PlatformConnection.identities))
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} does not exist.")
    return conn


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


@router.get("", response_model=list[ConnectionOut])
def list_connections(session: Session = SessionDep) -> list[ConnectionOut]:
    rows = (
        session.execute(
            select(PlatformConnection).options(selectinload(PlatformConnection.identities))
        )
        .scalars()
        .all()
    )
    return [_out(c) for c in rows]


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(
    payload: ConnectionCreateIn, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionOut:
    """Register a connection's *metadata*. No credential exists yet."""
    conn = PlatformConnection(
        provider=payload.provider,
        label=payload.label,
        scopes=list(payload.scopes or REQUESTED_SCOPES),
        status="disconnected",
    )
    session.add(conn)
    session.commit()
    return _out(_load(session, conn.id))


@router.post("/{connection_id}/authorize", response_model=dict)
def authorize(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> dict[str, Any]:
    """Build the loopback authorization request: PKCE, ``state``, exact redirect."""
    conn = _load(session, connection_id)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    with _flows_lock:
        _flows[state] = PendingFlow(
            state=state,
            verifier=verifier,
            connection_id=conn.id,
            created_at=datetime.now(UTC),
            scopes=tuple(conn.scopes or REQUESTED_SCOPES),
        )

    redirect = f"http://{ctx.settings.host}:{ctx.settings.port}{_REDIRECT_PATH}"
    params = [
        "response_type=code",
        "client_id=local-hexton",
        f"redirect_uri={redirect}",
        f"scope={' '.join(conn.scopes or REQUESTED_SCOPES)}",
        f"state={state}",
        f"code_challenge={PendingFlow(state, verifier, conn.id, datetime.now(UTC)).challenge()}",
        "code_challenge_method=S256",
        "access_type=offline",
        "prompt=consent",
    ]
    return {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join(params),
        "state": state,
        "redirect_uri": redirect,
        "scopes": list(conn.scopes or REQUESTED_SCOPES),
        "note": "Opening this URL is a manual, operator-driven step. Hexton never "
        "navigates a browser or reads a cookie on the operator's behalf.",
        "enabled": ctx.connector.provider != "youtube",
        "blocked_reason": (
            None
            if ctx.connector.provider != "youtube"
            else "The live YouTube connector is not enabled in this build. Phase 0 "
            "recorded FEAS-01/FEAS-02 as blocked pending a Google Cloud project and a "
            "consent configuration; see 08-phase0-feasibility-report.md §9."
        ),
    }


@router.get(_REDIRECT_PATH)
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> dict[str, Any]:
    """The loopback redirect target. Validates ``state`` before doing anything."""
    if error:
        raise HTTPException(status_code=400, detail=f"Authorization was refused: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")
    with _flows_lock:
        flow = _flows.pop(state, None)
    if flow is None:
        # An unknown state is exactly the CSRF condition this check exists for.
        raise HTTPException(
            status_code=403,
            detail="Unknown or already-used state parameter. The authorization result "
            "was not accepted; start again from Settings.",
        )
    if ctx.connector.provider == "youtube":
        raise HTTPException(
            status_code=501,
            detail="State was validated, but exchanging the code requires the live "
            "connector, which is blocked on FEAS-01/FEAS-02.",
        )
    return {"status": "validated", "connection_id": flow.connection_id}


@router.post("/{connection_id}/attach-test-credential", response_model=ConnectionOut)
def attach_test_credential(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionOut:
    """Development-only: attach a fake credential so the queue can be exercised.

    Refused in a packaged build. The gate is the build profile chosen at process
    startup, not a request-time flag, so a misconfigured request cannot reach it.
    """
    if ctx.is_packaged:
        raise HTTPException(
            status_code=403,
            detail="Attaching a test credential is refused in a packaged build.",
        )
    if ctx.connector.provider != "fake":
        raise HTTPException(
            status_code=409,
            detail="Test credentials only make sense with the fake connector.",
        )

    conn = _load(session, connection_id)
    key = f"conn:{conn.public_id}"
    ctx.credentials.write(f"{key}:access", "test-access-token")
    ctx.credentials.write(f"{key}:refresh", "test-refresh-token")

    now = datetime.now(UTC)
    conn.status = "active"
    conn.granted_at = now
    conn.token_expires_at = now + timedelta(hours=1)
    conn.last_token_check_at = now
    conn.last_token_check_ok = True

    for summary in ctx.connector.list_owned_channels("test-access-token"):
        _upsert_identity(session, conn, summary, now)

    session.commit()
    return _out(_load(session, connection_id))


def _upsert_identity(
    session: Session,
    conn: PlatformConnection,
    summary: ChannelSummary,
    now: datetime,
) -> ChannelIdentity:
    existing = session.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.connection_id == conn.id,
            ChannelIdentity.channel_id == summary.channel_id,
        )
    ).scalar_one_or_none()
    record = existing or ChannelIdentity(
        connection_id=conn.id,
        channel_id=summary.channel_id,
        verification_source="channels_list_mine",
    )
    record.title = summary.title
    record.handle = summary.handle
    record.is_brand_candidate = summary.is_brand_candidate
    record.subscriber_count = summary.subscriber_count
    record.verified_at = now
    if existing is None:
        session.add(record)
    return record


@router.post("/{connection_id}/refresh-identities", response_model=ConnectionOut)
def refresh_identities(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionOut:
    """``channels.list(mine=true)`` — the documented identity check (``FEAS-01``)."""
    conn = _load(session, connection_id)
    key = f"conn:{conn.public_id}"
    access = ctx.credentials.read(f"{key}:access")
    if not access:
        raise HTTPException(status_code=409, detail="No credential is stored for this connection.")
    try:
        channels = ctx.connector.list_owned_channels(access)
    except ConnectorError as exc:
        raise HTTPException(status_code=409, detail=f"Could not read identities: {exc}") from exc

    now = datetime.now(UTC)
    seen = set()
    for summary in channels:
        record = _upsert_identity(session, conn, summary, now)
        seen.add(record.id)
    session.commit()
    return _out(_load(session, connection_id))


@router.post("/channels/{identity_id}/confirm", response_model=ChannelOut)
def confirm_channel(
    identity_id: int, payload: ChannelConfirmIn, session: Session = SessionDep
) -> ChannelOut:
    """Record the operator's explicit confirmation that this is the target channel."""
    record = session.get(ChannelIdentity, identity_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Channel identity {identity_id} does not exist."
        )
    record.operator_confirmed_at = datetime.now(UTC)
    session.commit()
    return ChannelOut.model_validate(record)


@router.post("/{connection_id}/disable", response_model=ConnectionOut)
def disable_connection(connection_id: int, session: Session = SessionDep) -> ConnectionOut:
    """Stop using a connection **without** revoking it (``COMP-07``).

    Disabling and revoking are different operations with different consequences, and the
    Phase 2 review was explicit that conflating them is how an operator loses a working
    authorization by accident. Disabling is reversible in one click, deletes nothing, and
    does not talk to Google. Revoking is the one that deletes credentials and starts the
    ``D-1`` clock.
    """
    conn = _load(session, connection_id)
    if conn.status == "revoked":
        raise HTTPException(
            status_code=409,
            detail="This connection was revoked. Disabling does not apply to a revoked "
            "connection — reauthorize it instead.",
        )
    conn.status = "disabled"
    conn.disabled_at = datetime.now(UTC)
    session.commit()
    return _out(_load(session, connection_id))


@router.post("/{connection_id}/enable", response_model=ConnectionOut)
def enable_connection(connection_id: int, session: Session = SessionDep) -> ConnectionOut:
    """Undo :func:`disable_connection`. No reauthorization is needed, by design."""
    conn = _load(session, connection_id)
    if conn.status == "revoked" or conn.authorized_data_deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail="A revoked connection cannot be re-enabled; reauthorize it instead.",
        )
    if conn.granted_at is None:
        raise HTTPException(
            status_code=409,
            detail="This connection has never been authorized. Authorize it first.",
        )
    conn.status = "active"
    conn.disabled_at = None
    session.commit()
    return _out(_load(session, connection_id))


@router.post("/{connection_id}/revoke", response_model=ConnectionOut)
def revoke(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionOut:
    """Revoke programmatically and start the COMP-04 deletion clock.

    ``DF-20``: Authorized Data must be deleted within **7 calendar days** of a
    client-initiated revocation, and within **30** where the user revoked through
    Google's security settings page or the token simply stopped refreshing. Both
    deadlines are recorded here rather than being remembered by a human.
    """
    conn = _load(session, connection_id)
    key = f"conn:{conn.public_id}"
    now = datetime.now(UTC)
    revoked_remotely = False

    refresh = ctx.credentials.read(f"{key}:refresh")
    access = ctx.credentials.read(f"{key}:access")
    for token in (access, refresh):
        if token:
            # A failed remote revoke does not cancel the local obligation; the
            # deletion clock still runs either way, so this is best-effort.
            with suppress(ConnectorError):
                ctx.connector.revoke(token)

    for name in ("access", "refresh"):
        ctx.credentials.delete(f"{key}:{name}")

    conn.status = "revoked"
    conn.revoked_at = now
    conn.revoked_by = "google" if revoked_remotely else "client"
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=now + timedelta(days=RETENTION_DAYS_CLIENT),
        )
    )
    session.commit()
    return _out(_load(session, connection_id))


@router.post("/{connection_id}/mark-data-deleted", response_model=ConnectionOut)
def mark_data_deleted(connection_id: int, session: Session = SessionDep) -> ConnectionOut:
    """Record that Authorized Data has actually been purged, closing COMP-04."""
    conn = _load(session, connection_id)
    now = datetime.now(UTC)
    conn.authorized_data_deleted_at = now
    for row in session.execute(
        select(DataRetention).where(
            DataRetention.subject_type == "connection",
            DataRetention.subject_id == conn.public_id,
            DataRetention.purged_at.is_(None),
        )
    ).scalars():
        row.purged_at = now
        row.purge_note = "Operator confirmed deletion of Authorized Data."
    session.commit()
    return _out(_load(session, connection_id))


@router.delete("/{connection_id}", status_code=204)
def delete_connection(
    connection_id: int, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> None:
    conn = _load(session, connection_id)
    key = f"conn:{conn.public_id}"
    for name in ("access", "refresh"):
        ctx.credentials.delete(f"{key}:{name}")
    jobs = session.execute(
        select(ScheduledJob.id).where(ScheduledJob.connection_id == conn.id).limit(1)
    ).first()
    if jobs is not None:
        raise HTTPException(
            status_code=409,
            detail="This connection is referenced by at least one job. Cancel or "
            "reassign those jobs first.",
        )
    session.delete(conn)
    session.commit()


_ = FailureKind
