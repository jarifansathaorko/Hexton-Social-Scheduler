"""FastAPI dependencies.

The application context is built once in :mod:`hexton.api.app` and hung off
``app.state``. Nothing here builds a connector, a credential store or an engine: those
are chosen at process startup, so a request can never end up on a different one than
the process was configured for.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from hexton.api.security import SessionSigner
from hexton.bootstrap import AppContext
from hexton.domain.scheduling import resolve_zone

if TYPE_CHECKING:  # pragma: no cover
    pass


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_signer(request: Request) -> SessionSigner:
    return request.app.state.signer


def get_zone(ctx: AppContext = Depends(get_ctx)) -> ZoneInfo:
    """The operator's display zone, resolved in exactly one place.

    A bad zone key fails here with a message that names ``tzdata`` rather than
    surfacing later as a wrong-looking publication time (Phase 0 §7.6).
    """
    return resolve_zone(ctx.settings.display_timezone)


def db_session(ctx: AppContext = Depends(get_ctx)) -> Iterator[Session]:
    session = ctx.sessions()
    try:
        yield session
    finally:
        session.close()


CtxDep = Depends(get_ctx)
ZoneDep = Depends(get_zone)
SessionDep = Depends(db_session)
SignerDep = Depends(get_signer)
