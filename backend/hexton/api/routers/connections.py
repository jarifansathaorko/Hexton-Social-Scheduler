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
import logging
import secrets
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
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

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections", tags=["connections"])

#: The complete scope set required to upload, manage, publish, and delete YouTube videos
#: and track multi-channel analytics for client proof-of-work.
#: NOTE: Never mix Google Sheets / Drive scopes into YouTube connections, as Google Brand Accounts
#: (common when linking multiple channels on one Gmail) do not support Drive/Sheets and will fail with
#: "Service unavailable / servicerestricted".
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

SHEETS_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
)

REQUESTED_SCOPES = YOUTUBE_SCOPES

#: COMP-04: delete Authorized Data within this long of a revocation (``DF-20``/``DF-21``).
RETENTION_DAYS_CLIENT = 7
RETENTION_DAYS_GOOGLE = 30

_REDIRECT_PATH = "/oauth/callback"

#: The loopback redirect target lives *outside* ``/api`` on purpose and on its own
#: router so the ``/api/connections`` prefix cannot be applied to it.
#:
#: Two independent reasons it must be exactly this path:
#:
#: * ``authorize`` advertises ``redirect_uri`` to Google as ``{_REDIRECT_PATH}``. The
#:   route and the advertised URI have to be the same string, or the browser lands on
#:   the single-page fallback after consent and the callback handler never runs.
#: * ``SameSite=Strict`` means the session cookie is *not* sent on the cross-site
#:   top-level navigation back from accounts.google.com, so a route under ``/api``
#:   would be answered with 401 before it could validate ``state``.
def _oauth_error_html(title: str, message: str, redirect_url: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="5;url={redirect_url}">
  <title>Hexton - {title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #1e293b;
      padding: 2.5rem;
      border-radius: 12px;
      border: 1px solid #ef4444;
      max-width: 460px;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .badge {{
      display: inline-block;
      background: #ef4444;
      color: white;
      width: 48px;
      height: 48px;
      line-height: 48px;
      border-radius: 50%;
      font-size: 24px;
      margin-bottom: 1rem;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    p {{ font-size: 0.875rem; color: #94a3b8; margin: 0 0 1.5rem; line-height: 1.5; }}
    a {{
      display: inline-block;
      background: #3b82f6;
      color: white;
      padding: 0.6rem 1.2rem;
      border-radius: 6px;
      text-decoration: none;
      font-size: 0.875rem;
      font-weight: 500;
    }}
    a:hover {{ background: #2563eb; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">&#10007;</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <a href="{redirect_url}">Return to Channels</a>
  </div>
</body>
</html>"""

callback_router = APIRouter(tags=["connections"])


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

#: How long an authorization request stays valid. The flow is one browser round trip,
#: so anything older was abandoned; keeping it around only lengthens the window in
#: which a captured ``state`` would still be accepted, and an unbounded ``_flows``
#: dict is a slow leak in a process that is meant to run for weeks.
FLOW_TTL = timedelta(minutes=10)


def _prune_flows(now: datetime) -> None:
    """Drop expired pending flows. Caller holds ``_flows_lock``."""
    cutoff = now - FLOW_TTL
    for stale in [key for key, flow in _flows.items() if flow.created_at < cutoff]:
        del _flows[stale]


def _put_flow(flow: PendingFlow) -> None:
    now = datetime.now(UTC)
    with _flows_lock:
        _prune_flows(now)
        _flows[flow.state] = flow


def _take_flow(state: str) -> PendingFlow | None:
    """Pop ``state``, expiring anything stale first.

    Expiry happens *before* the lookup so an abandoned flow from yesterday answers
    "unknown or already-used state" rather than validating.
    """
    now = datetime.now(UTC)
    with _flows_lock:
        _prune_flows(now)
        return _flows.pop(state, None)


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
def list_connections(
    include_pending: bool = False,
    session: Session = SessionDep,
) -> list[ConnectionOut]:
    rows = list(
        session.execute(
            select(PlatformConnection).options(selectinload(PlatformConnection.identities))
        )
        .scalars()
        .all()
    )
    if include_pending:
        return [_out(c) for c in rows]
    # Prune any empty ghost connections that have 0 identities and no granted tokens
    valid_rows = [
        c
        for c in rows
        if len(c.identities) > 0 or c.granted_at is not None or c.status == "revoked"
    ]
    return [_out(c) for c in valid_rows]


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(
    payload: ConnectionCreateIn, session: Session = SessionDep, ctx: AppContext = CtxDep
) -> ConnectionOut:
    default_scopes = (
        list(SHEETS_SCOPES)
        if payload.provider == "sheets"
        else list(YOUTUBE_SCOPES)
    )
    # Check if there is an existing empty connection with no identities to reuse or clean up
    empty_conns = list(
        session.execute(
            select(PlatformConnection)
            .where(
                PlatformConnection.provider == payload.provider,
            )
            .options(selectinload(PlatformConnection.identities))
        )
        .scalars()
        .all()
    )
    reusable: PlatformConnection | None = None
    for candidate in empty_conns:
        if len(candidate.identities) == 0:
            if reusable is None:
                reusable = candidate
            else:
                # Delete duplicate empty connection so they never pile up
                key = f"conn:{candidate.public_id}"
                ctx.credentials.delete(f"{key}:access")
                ctx.credentials.delete(f"{key}:refresh")
                session.delete(candidate)

    if reusable is not None:
        reusable.label = payload.label
        reusable.status = "disconnected"
        reusable.granted_at = None
        reusable.token_expires_at = None
        reusable.scopes = list(payload.scopes or default_scopes)
        session.commit()
        return _out(_load(session, reusable.id))

    conn = PlatformConnection(
        provider=payload.provider,
        label=payload.label,
        scopes=list(payload.scopes or default_scopes),
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
    scopes = (
        list(SHEETS_SCOPES)
        if conn.provider == "sheets"
        else list(YOUTUBE_SCOPES)
    )
    conn.scopes = scopes
    session.flush()

    _put_flow(
        PendingFlow(
            state=state,
            verifier=verifier,
            connection_id=conn.id,
            created_at=datetime.now(UTC),
            scopes=tuple(scopes),
        )
    )

    client_id = ctx.settings.google_client_id or "local-hexton"
    redirect = f"http://{ctx.settings.host}:{ctx.settings.port}{_REDIRECT_PATH}"
    params = [
        "response_type=code",
        f"client_id={client_id}",
        f"redirect_uri={redirect}",
        f"scope={' '.join(scopes)}",
        f"state={state}",
        f"code_challenge={PendingFlow(state, verifier, conn.id, datetime.now(UTC)).challenge()}",
        "code_challenge_method=S256",
        "access_type=offline",
        "prompt=consent select_account",
    ]
    is_live = ctx.connector.provider == "youtube"
    has_client_id = bool(ctx.settings.google_client_id)
    enabled = not is_live or has_client_id
    blocked_reason = None
    if is_live and not has_client_id:
        blocked_reason = (
            "Google Client ID is not configured. Add HEXTON_GOOGLE_CLIENT_ID to .env "
            "or your environment to enable live authorization."
        )

    return {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join(params),
        "state": state,
        "redirect_uri": redirect,
        "scopes": scopes,
        "note": "Opening this URL is a manual, operator-driven step. Hexton never "
        "navigates a browser or reads a cookie on the operator's behalf.",
        "enabled": enabled,
        "blocked_reason": blocked_reason,
    }


@callback_router.get(_REDIRECT_PATH)
def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> Any:
    """The loopback redirect target. Validates ``state`` before doing anything."""
    if error:
        flow = _take_flow(state) if state else None
        if flow:
            conn = session.get(PlatformConnection, flow.connection_id)
            if conn and len(conn.identities) == 0:
                session.delete(conn)
                session.commit()
        if request and "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(
                content=_oauth_error_html(
                    "Authorization Cancelled",
                    f"Google authorization was cancelled or refused ({error}). No channel was connected.",
                    "/channels?error=cancelled",
                ),
                status_code=200,
            )
        raise HTTPException(status_code=400, detail=f"Authorization was refused: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")
    flow = _take_flow(state)
    if flow is None:
        # An unknown state is exactly the CSRF condition this check exists for.
        raise HTTPException(
            status_code=403,
            detail="Unknown or already-used state parameter. The authorization result "
            "was not accepted; start again from Settings.",
        )
    if ctx.connector.provider == "youtube":
        client_id = ctx.settings.google_client_id
        client_secret = ctx.settings.google_client_secret
        if not client_id:
            raise HTTPException(
                status_code=400,
                detail="Google Client ID is not configured on this server.",
            )
        redirect = f"http://{ctx.settings.host}:{ctx.settings.port}{_REDIRECT_PATH}"
        token_data: dict[str, str] = {
            "client_id": client_id,
            "code": code,
            "code_verifier": flow.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        }
        if client_secret:
            token_data["client_secret"] = client_secret

        token_url = (
            f"{getattr(ctx.connector, 'oauth_base_url', 'https://oauth2.googleapis.com')}/token"
        )
        try:
            with httpx.Client(timeout=30.0) as http_client:
                token_resp = http_client.post(token_url, data=token_data)
        except Exception as exc:
            conn = session.get(PlatformConnection, flow.connection_id)
            if conn and len(conn.identities) == 0:
                session.delete(conn)
                session.commit()
            if request and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(
                    content=_oauth_error_html(
                        "Connection Failed",
                        f"Network error contacting Google OAuth token endpoint: {exc}",
                        "/channels?error=network_error",
                    ),
                    status_code=200,
                )
            raise HTTPException(
                status_code=502,
                detail=f"Network error contacting Google OAuth token endpoint: {exc}",
            ) from exc

        if token_resp.status_code != 200:
            conn = session.get(PlatformConnection, flow.connection_id)
            if conn and len(conn.identities) == 0:
                session.delete(conn)
                session.commit()
            if request and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(
                    content=_oauth_error_html(
                        "Token Exchange Failed",
                        f"Google token exchange failed (HTTP {token_resp.status_code}). Please try again.",
                        "/channels?error=token_failed",
                    ),
                    status_code=200,
                )
            raise HTTPException(
                status_code=400,
                detail=f"Google token exchange failed (HTTP {token_resp.status_code}): {token_resp.text[:300]}",
            )

        resp_payload = token_resp.json() or {}
        access_token = resp_payload.get("access_token")
        refresh_token = resp_payload.get("refresh_token")
        expires_in = resp_payload.get("expires_in", 3600)

        if not access_token:
            raise HTTPException(
                status_code=400, detail="Token response did not contain an access_token."
            )

        conn = _load(session, flow.connection_id)
        key = f"conn:{conn.public_id}"
        ctx.credentials.write(f"{key}:access", access_token)
        if refresh_token:
            ctx.credentials.write(f"{key}:refresh", refresh_token)

        now = datetime.now(UTC)
        conn.status = "active"
        conn.granted_at = now
        conn.token_expires_at = now + timedelta(seconds=expires_in)
        conn.last_token_check_at = now
        conn.last_token_check_ok = True

        # Discover channels for this credential
        channels: list[ChannelSummary] = []
        try:
            channels = ctx.connector.list_owned_channels(access_token)
        except Exception as exc:
            log.warning("Channel identity discovery failed during OAuth callback: %s", exc)

        if conn.provider == "youtube" and not channels:
            # The chosen Google account has no YouTube channel!
            # Revoke credential and delete the empty connection so no ghost account remains.
            key = f"conn:{conn.public_id}"
            for name in ("access", "refresh"):
                token = ctx.credentials.read(f"{key}:{name}")
                if token and name == "refresh":
                    with suppress(Exception):
                        ctx.connector.revoke(token)
                ctx.credentials.delete(f"{key}:{name}")
            session.delete(conn)
            session.commit()

            if request and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(
                    content=_oauth_error_html(
                        "No YouTube Channel Found",
                        "The selected Google account has no YouTube channel associated with it. Please ensure the Google account has a YouTube channel created or choose another account.",
                        "/channels?error=no_channel",
                    ),
                    status_code=200,
                )
            raise HTTPException(
                status_code=400,
                detail="No YouTube channel was found for this Google account.",
            )

        owned_ids = {ch.channel_id for ch in channels}
        if channels and conn.provider == "youtube":
            conn.label = f"YouTube · {channels[0].title}"
        for ch in channels:
            _upsert_identity(session, conn, ch, now)
        for ident in conn.identities:
            if ident.channel_id not in owned_ids:
                ident.operator_confirmed_at = None

        session.commit()
        if request and "text/html" in request.headers.get("accept", ""):
            target_path = "/reports" if conn.provider == "sheets" else "/channels"
            html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="2;url={target_path}?authorized=true">
  <title>Hexton - Authorization Successful</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #1e293b;
      padding: 2.5rem;
      border-radius: 12px;
      border: 1px solid #334155;
      max-width: 440px;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .badge {{
      display: inline-block;
      background: #10b981;
      color: white;
      width: 48px;
      height: 48px;
      line-height: 48px;
      border-radius: 50%;
      font-size: 24px;
      margin-bottom: 1rem;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    p {{ font-size: 0.875rem; color: #94a3b8; margin: 0 0 1.5rem; line-height: 1.5; }}
    a {{
      display: inline-block;
      background: #3b82f6;
      color: white;
      padding: 0.6rem 1.2rem;
      border-radius: 6px;
      text-decoration: none;
      font-size: 0.875rem;
      font-weight: 500;
    }}
    a:hover {{ background: #2563eb; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">&#10003;</div>
    <h1>Authorization Successful</h1>
    <p>Google has authorized <strong>{conn.label}</strong>. Returning to Hexton in 2 seconds...</p>
    <a href="{target_path}">Return to Hexton</a>
  </div>
</body>
</html>"""
            return HTMLResponse(content=html, status_code=200)

        return {
            "status": "authorized",
            "connection_id": flow.connection_id,
            "message": "Authorization successful. Return to Channels to confirm your channel.",
        }

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
    owned_ids = {ch.channel_id for ch in channels}
    if channels and conn.provider == "youtube":
        conn.label = f"YouTube · {channels[0].title}"
    seen = set()
    for summary in channels:
        record = _upsert_identity(session, conn, summary, now)
        seen.add(record.id)
    for ident in conn.identities:
        if ident.channel_id not in owned_ids:
            ident.operator_confirmed_at = None
    session.commit()
    return _out(_load(session, connection_id))


@router.post("/channels/{identity_id}/confirm", response_model=ChannelOut)
def confirm_channel(
    identity_id: int, payload: ChannelConfirmIn | None = None, session: Session = SessionDep
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


@router.post("/channels/{identity_id}/unconfirm", response_model=ChannelOut)
def unconfirm_channel(identity_id: int, session: Session = SessionDep) -> ChannelOut:
    """Revoke operator confirmation for this channel."""
    record = session.get(ChannelIdentity, identity_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Channel identity {identity_id} does not exist."
        )
    record.operator_confirmed_at = None
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

    # Always ``"client"`` here, and that is the point of this route: it is the
    # operator's own action. ``revoked_by`` is also written as ``"google"`` when a
    # grant stops refreshing (see ``JobRunner._record_unrefreshable``) — that is a
    # different actor with a different deletion deadline under ``DF-21``, so it must
    # not be decided by a local flag that was always false.
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
    conn.revoked_by = "client"
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
    connection_id: int,
    force: bool = False,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> None:
    """Remove a connection that nothing references (or force unlink)."""
    conn = _load(session, connection_id)
    jobs = session.execute(
        select(ScheduledJob).where(ScheduledJob.connection_id == conn.id)
    ).scalars().all()
    if jobs:
        if not force:
            raise HTTPException(
                status_code=409,
                detail="This connection is referenced by at least one job. Cancel or "
                "reassign those jobs first.",
            )
        for job in jobs:
            job.connection_id = None
            job.channel_identity_id = None

    key = f"conn:{conn.public_id}"
    for name in ("access", "refresh"):
        token = ctx.credentials.read(f"{key}:{name}")
        if token and name == "refresh":
            with suppress(Exception):
                ctx.connector.revoke(token)
        ctx.credentials.delete(f"{key}:{name}")
    session.delete(conn)
    session.commit()


_ = FailureKind
