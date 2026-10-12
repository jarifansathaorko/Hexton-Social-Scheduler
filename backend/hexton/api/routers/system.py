"""Session minting, health, and version."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy.orm import Session

from hexton import __version__
from hexton.api.deps import CtxDep, SessionDep, SignerDep
from hexton.api.schemas import HealthOut, SessionOut, VersionOut
from hexton.api.security import COOKIE_SECURE, SessionSigner
from hexton.bootstrap import AppContext
from hexton.db.session import is_writable
from hexton.worker.heartbeat import worker_is_alive

router = APIRouter(prefix="/api", tags=["system"])


@router.post("/session", response_model=SessionOut)
def create_session(
    response: Response,
    signer: SessionSigner = SignerDep,
    ctx: AppContext = CtxDep,
) -> SessionOut:
    """Mint the single local session this application uses.

    There is no password and no second mechanism. The cookie is HttpOnly and
    SameSite=Strict, so a cross-origin page cannot read it or send it; the CSRF token
    returned here must accompany every state-changing request.
    """
    cookie, csrf = signer.mint()
    response.set_cookie(
        key=ctx.settings.session_cookie_name,
        value=cookie,
        max_age=ctx.settings.session_max_age_seconds,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return SessionOut(csrf_token=csrf, expires_in=ctx.settings.session_max_age_seconds)


@router.get("/health", response_model=HealthOut)
def health(session: Session = SessionDep, ctx: AppContext = CtxDep) -> HealthOut:
    try:
        database = is_writable(ctx.engine)
    except Exception:
        database = False
    return HealthOut(
        status="ok" if database else "degraded",
        version=__version__,
        database=database,
        # From the heartbeat row, never from this process's own state: the worker is
        # a different process, and hard-coding ``True`` here reported a running
        # worker when none was started.
        worker_alive=worker_is_alive(session, heartbeat_seconds=ctx.settings.heartbeat_seconds),
        connector=ctx.connector.provider,
        credential_store=ctx.credentials.name,
        profile=ctx.profile.value,
    )


@router.get("/version", response_model=VersionOut)
def version() -> VersionOut:
    return VersionOut(version=__version__, api=1)
