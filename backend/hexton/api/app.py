"""The FastAPI application.

Built by :func:`create_app` around a :class:`~hexton.bootstrap.AppContext` that was
assembled at process startup. Nothing in this module decides *which* connector or
*which* credential store to use — that decision was made once, in the composition
root, and this module only wires routes to it.

Serving the frontend from this same origin is a security property, not a convenience.
``02`` §9.2 requires a single local origin precisely so that the session cookie can be
``SameSite=Strict`` and the CSRF check can be a same-origin comparison. Splitting the
frontend onto its own port would force a weaker cookie and a CORS surface, for no
benefit the product can name.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from hexton import __version__
from hexton.api.routers import (
    compliance,
    connections,
    dashboard,
    events,
    jobs,
    media,
    mentions,
    reporting,
    system,
)
from hexton.api.security import (
    LocalSecurityMiddleware,
    SessionSigner,
    allowed_hosts_for,
)
from hexton.bootstrap import AppContext, load_or_create_session_key

log = logging.getLogger(__name__)


#: Where the built frontend lives. The repository is ``<root>/frontend`` beside
#: ``<root>/backend``, and this file is ``<root>/backend/hexton/api/app.py`` — so the
#: bundle is three levels up, not two. Getting that wrong pointed at
#: ``backend/frontend/dist``, which does not exist, and the app would have served the
#: 503 "not built yet" page forever even with a freshly built bundle.
def _default_frontend_dist() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[3] / "frontend" / "dist"
    alongside_package = here.parents[2] / "frontend" / "dist"
    for candidate in (repo_root, alongside_package):
        if candidate.exists():
            return candidate
    # Neither exists yet; report the layout we actually document.
    return repo_root


FRONTEND_DIST = _default_frontend_dist()


def create_app(ctx: AppContext, *, frontend_dist: Path | None = None) -> FastAPI:
    """Assemble the API. One call, at process startup."""
    settings = ctx.settings
    signer = SessionSigner(
        load_or_create_session_key(settings.session_key_path),
        max_age_seconds=settings.session_max_age_seconds,
    )

    app = FastAPI(
        title="Hexton",
        version=__version__,
        # The API docs are a developer surface on loopback; they are not reachable
        # without a session because the security middleware guards /docs too.
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.ctx = ctx
    app.state.signer = signer

    app.add_middleware(
        LocalSecurityMiddleware,
        signer=signer,
        allowed_hosts=allowed_hosts_for(settings.host, settings.port),
        cookie_name=settings.session_cookie_name,
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        # Never leak a stack trace to a local caller: it can contain a token, a path,
        # or a fragment of a query built from operator input.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "The request failed. See the log in the data directory."},
        )

    app.include_router(system.router)
    app.include_router(dashboard.router)
    app.include_router(media.router)
    app.include_router(jobs.router)
    app.include_router(connections.router)
    # Registered before the SPA fallback so the loopback redirect target is a real
    # endpoint rather than an index.html response (see connections.callback_router).
    app.include_router(connections.callback_router)
    app.include_router(events.router)
    app.include_router(reporting.router)
    app.include_router(compliance.router)
    app.include_router(mentions.router)

    _mount_frontend(app, frontend_dist or FRONTEND_DIST)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built single-page app from this origin, with an SPA fallback."""
    if not dist.exists():

        @app.get("/", include_in_schema=False)
        async def _missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "The frontend has not been built yet. Run "
                    "`npm --prefix ../frontend run build` and restart, or use "
                    "`npm --prefix ../frontend run dev` during development.",
                },
            )

        log.warning("Frontend bundle not found at %s; serving the API only.", dist)
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            # The SPA fallback must never answer for the API. Registered routes are
            # matched first, so anything reaching here is an unknown endpoint; serving
            # index.html with a 200 turned a typo into a successful-looking response and
            # hid real 404s from both an operator and a test.
            return JSONResponse(
                status_code=404,
                content={"detail": f"Unknown endpoint /{full_path}.", "code": "not_found"},
            )
        candidate = (dist / full_path).resolve() if full_path else index
        # A path traversal attempt must not read outside the bundle directory.
        if full_path and (not candidate.is_relative_to(dist) or not candidate.is_file()):
            candidate = index
        return FileResponse(candidate)

    log.info("Serving the frontend from %s", dist)
