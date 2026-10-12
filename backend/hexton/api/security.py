"""Local web security: session cookie, CSRF, and Host/Origin enforcement.

Design decisions, all of them consequences of the threat model rather than habit:

**Loopback is a boundary, not a policy.** ``02`` §9.1 requires loopback binding, and
:mod:`hexton.config` refuses a non-loopback host at configuration time. This module
adds the checks that binding alone does not provide: another process on the same
machine — or another *user* account on the same machine — can reach ``127.0.0.1``, so
the requests themselves still have to be authenticated and origin-checked.

**One mechanism, not two.** There is no password prompt and no separate "local login".
A single signed, HttpOnly, SameSite=Strict cookie is the whole design, minted by the
frontend on first load and re-minted when it expires. Adding a password to a loopback
application would defend against a different threat than the one this application
actually faces, and would create a second thing to get wrong.

**CSRF is a derived token, not a stored one.** The CSRF token is an HMAC of the
session's nonce under the process key, so it cannot drift from the cookie it protects
and the application needs no session table. State-changing requests must present it in
a header, which a cross-origin form post cannot set.

**Tokens never reach the browser.** No schema in this package carries an access token,
a refresh token, a client secret or a credential-store key. The frontend can read
*whether* a connection is healthy; it can never read the material that makes it work.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Methods that change state and therefore require a CSRF token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths reachable without a session. Deliberately tiny: the shell and the means of
#: obtaining a session, and nothing else.
OPEN_PATHS = frozenset({"/api/health", "/api/session", "/api/version"})

CSRF_HEADER = "X-CSRF-Token"
COOKIE_SECURE = False  # loopback over http; a Secure cookie would never be sent


@dataclass(frozen=True)
class SessionIdentity:
    nonce: str
    expires_at: int

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class SessionSigner:
    """Mints and verifies the session cookie and its derived CSRF token.

    Stateless on purpose. A server-side session table would be one more thing to keep
    consistent with the job database, and would not raise the security bar for an
    application whose only listeners are on loopback.
    """

    def __init__(self, key: bytes, *, max_age_seconds: int = 12 * 3600) -> None:
        if len(key) < 32:
            raise ValueError("The session key must be at least 32 bytes.")
        self._key = key
        self.max_age = max_age_seconds

    def _mac(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def mint(self) -> tuple[str, str]:
        """Return ``(cookie_value, csrf_token)`` for a brand-new session."""
        expires = int(time.time()) + self.max_age
        nonce = secrets.token_urlsafe(24)
        payload = f"{expires}.{nonce}"
        return f"{payload}.{self._mac(payload)}", self.csrf_for(nonce)

    def csrf_for(self, nonce: str) -> str:
        """The CSRF token that belongs to a nonce. Deterministic, never stored."""
        return self._mac(f"{nonce}:csrf")

    def verify(self, cookie_value: str | None) -> SessionIdentity | None:
        if not cookie_value:
            return None
        parts = cookie_value.split(".")
        if len(parts) != 3:
            return None
        expires_s, nonce, signature = parts
        payload = f"{expires_s}.{nonce}"
        if not hmac.compare_digest(signature, self._mac(payload)):
            return None
        try:
            expires = int(expires_s)
        except ValueError:
            return None
        identity = SessionIdentity(nonce=nonce, expires_at=expires)
        return None if identity.expired else identity


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """Host, Origin, CSRF and hardening headers in one place.

    One middleware rather than four because the rules interact: an absent ``Origin``
    is acceptable for a same-origin navigation but not for a state change, and both
    answers depend on knowing the request was aimed at our own host.
    """

    def __init__(
        self,
        app: object,
        *,
        signer: SessionSigner,
        allowed_hosts: frozenset[str],
        cookie_name: str,
    ) -> None:
        super().__init__(app)
        self.signer = signer
        self.allowed_hosts = allowed_hosts
        self.cookie_name = cookie_name
        #: Nonces minted this process, so the API can confirm a CSRF token belongs to
        #: the cookie that presented it. A pure HMAC comparison would accept a token
        #: derived from a *different* valid session's nonce.
        self._live: set[str] = set()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        host = (request.headers.get("host") or "").lower()
        if host and host not in self.allowed_hosts:
            return _deny(
                403,
                "host_not_allowed",
                f"Host {host!r} is not this application's loopback address. Hexton "
                f"answers only on {', '.join(sorted(self.allowed_hosts))}.",
            )

        path = request.url.path
        identity = self.signer.verify(request.cookies.get(self.cookie_name))
        if identity:
            self._live.add(identity.nonce)

        if path.startswith("/api/"):
            # Origin is checked on *every* state-changing API request, including the
            # open ones. ``/api/session`` mints the cookie, and a page on another
            # origin has no business doing that from here; ``SameSite=Strict`` already
            # keeps the cookie from travelling, so this is a second lock on the same
            # door rather than a substitute for it.
            if request.method in UNSAFE_METHODS:
                origin = request.headers.get("origin")
                if origin is not None and origin.rstrip("/") not in self._allowed_origins(host):
                    return _deny(
                        403,
                        "origin_not_allowed",
                        f"Origin {origin!r} is not this application's own origin.",
                    )

            if path not in OPEN_PATHS:
                if identity is None:
                    return _deny(
                        401,
                        "no_session",
                        "This request needs a local session. The frontend mints one on "
                        "first load; reload the page if it has expired.",
                    )
                if request.method in UNSAFE_METHODS:
                    # Starlette's Headers mapping is case-insensitive, so the canonical
                    # constant matches whatever casing the client used.
                    supplied = request.headers.get(CSRF_HEADER)
                    expected = self.signer.csrf_for(identity.nonce)
                    if not supplied or not hmac.compare_digest(supplied, expected):
                        return _deny(
                            403,
                            "csrf_failed",
                            f"State-changing requests must carry a valid {CSRF_HEADER} "
                            f"header derived from the current session.",
                        )

        response = await call_next(request)
        _harden(response)
        return response

    def _allowed_origins(self, host: str) -> frozenset[str]:
        return frozenset(f"http://{h}" for h in self.allowed_hosts if h == host or host == "")

    def forget(self, nonce: str) -> None:
        self._live.discard(nonce)


def _deny(status: int, code: str, message: str) -> JSONResponse:
    # Denied responses get the same hardening headers as successful ones: a 401 or a
    # 403 carries an origin and a body, and leaving it unprotected would make the
    # headers a property of being allowed in rather than of the application.
    response = JSONResponse(status_code=status, content={"detail": message, "code": code})
    _harden(response)
    return response


def _harden(response: Response) -> None:
    # ``response.headers`` is already a MutableHeaders instance. Constructing one
    # with ``MutableHeaders(response=...)`` was removed from Starlette, and using it
    # here raised on every single response — a failure that only shows up once the
    # API is actually asked for something.
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    headers["X-Frame-Options"] = "DENY"
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # The frontend is served from this origin and uses no third-party scripts, so the
    # policy can be strict without breaking anything — which is the point.
    headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def allowed_hosts_for(host: str, port: int) -> frozenset[str]:
    """The exact Host values this process will answer.

    Computed once at startup rather than matched loosely, because a wildcard host
    check is indistinguishable from no host check at all.
    """
    return frozenset({f"{host}:{port}", f"localhost:{port}", f"[::1]:{port}"})


def read_headers(request: Request) -> Headers:
    return request.headers
