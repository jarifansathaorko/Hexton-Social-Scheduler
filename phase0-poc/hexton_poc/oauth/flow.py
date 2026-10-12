"""Loopback OAuth 2.0 authorization-code flow with PKCE and strict `state` validation.

Deliberately implemented directly over ``httpx`` rather than with
``google-auth-oauthlib``: that library's local server does not give the caller a place to
enforce ``state`` matching before the token exchange, and ``AC-MVP-002``/``02`` section 7
require the authorization response to be validated.

References: OAuth 2.0 for Native Apps (RFC 8252) loopback redirect, Google's OAuth 2.0
Authorization endpoint, and ``docfacts.DF-17`` (Testing-status token expiry).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

import httpx

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

#: Minimum scopes for a publishing client. `youtube.upload` alone cannot read the channel
#: identity, so `youtube` (read/write) is the correct minimum for this use - still
#: narrower than `youtube.force-ssl`. Sheets is requested separately and independently.
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube",
)
SHEETS_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    # File *selection* needs the narrowest Drive scope; never the full drive scope.
    "https://www.googleapis.com/auth/drive.file",
)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using the S256 method."""
    verifier = _b64u(secrets.token_bytes(64))
    challenge = _b64u(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass(frozen=True)
class OAuthError(Exception):
    error: str
    description: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"OAuthError: {self.error} {self.description}".strip()


@dataclass
class AuthorizationResult:
    code: str
    state: str
    raw_query: dict[str, list[str]]


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: str = ""
    obtained_at: float = 0.0
    # Recorded so the UI can warn before expiry rather than failing mid-job (DF-17).
    expires_at: float | None = None

    def to_storage(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
            "obtained_at": self.obtained_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_storage(cls, data: dict) -> "TokenSet":
        return cls(**data)


class _LoopbackHandler(BaseHTTPRequestHandler):
    """Single-request loopback callback. Refuses anything that is not the expected path."""

    server_version = "HextonPoC/1.0"
    expected_path: str = "/oauth2/callback"
    on_code: Callable[[AuthorizationResult | None, OAuthError | None], None]

    def log_message(self, *_args) -> None:   # keep stdout clean for evidence capture
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.expected_path:
            self._respond(404, "Unknown path.")
            return

        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if "error" in query:
            self._respond(400, "Authorization was denied or failed. You may close this tab.")
            self._finish(None, OAuthError(query["error"][0],
                                          (query.get("error_description") or [""])[0]))
            return
        if "code" not in query or "state" not in query:
            self._respond(400, "Malformed authorization response.")
            self._finish(None, OAuthError("malformed_response",
                                          "authorization response must carry code and state"))
            return

        self._respond(200, "Authorization complete. You may close this tab and return to the app.")
        self._finish(
            AuthorizationResult(
                code=query["code"][0],
                state=query["state"][0],
                raw_query=query,
            ),
            None,
        )

    def _respond(self, status: int, message: str) -> None:
        body = (
            f"<!doctype html><meta charset='utf-8'><title>Hexton Scheduler</title>"
            f"<p style='font-family:system-ui'>{message}</p>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _finish(self, result, err) -> None:
        self.server.result = (result, err)  # type: ignore[attr-defined]
        self.server.shutdown_event.set()    # type: ignore[attr-defined]


class LoopbackServer:
    """Binds 127.0.0.1 on an ephemeral port and captures exactly one callback."""

    def __init__(self, path: str = "/oauth2/callback") -> None:
        self._httpd = HTTPServer(("127.0.0.1", 0), _LoopbackHandler)
        self._httpd.expected_path = path                       # type: ignore[attr-defined]
        self._httpd.result = (None, None)                      # type: ignore[attr-defined]
        self._httpd.shutdown_event = threading.Event()         # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def redirect_uri(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}{self._httpd.expected_path}"  # type: ignore[attr-defined]

    def __enter__(self) -> "LoopbackServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._httpd.shutdown_event.set()                       # type: ignore[attr-defined]
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def wait(self, timeout: float = 300.0):
        if not self._httpd.shutdown_event.wait(timeout):       # type: ignore[attr-defined]
            raise TimeoutError("No authorization response received before the timeout.")
        return self._httpd.result                               # type: ignore[attr-defined]


@dataclass
class OAuthFlow:
    client_id: str
    client_secret: str
    scopes: tuple[str, ...] = YOUTUBE_SCOPES
    access_type: str = "offline"           # offline -> a refresh token is issued
    prompt: str = "consent"
    include_granted_scopes: str = "true"
    auth_endpoint: str = GOOGLE_AUTH_ENDPOINT
    token_endpoint: str = GOOGLE_TOKEN_ENDPOINT
    revoke_endpoint: str = GOOGLE_REVOKE_ENDPOINT

    state: str = field(default="", init=False)
    code_verifier: str = field(default="", init=False)

    def authorization_url(self, redirect_uri: str) -> tuple[str, str, str]:
        """Return ``(url, state, code_verifier)``."""
        self.state = _b64u(secrets.token_bytes(32))
        self.code_verifier, challenge = make_pkce()
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": self.access_type,
            "prompt": self.prompt,
            "include_granted_scopes": self.include_granted_scopes,
            "state": self.state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.auth_endpoint}?{urllib.parse.urlencode(params)}", self.state, self.code_verifier

    def validate_callback(self, result: AuthorizationResult) -> str:
        """Constant-time ``state`` comparison. Raises :class:`OAuthError` on mismatch.

        This is the CSRF control for the whole credential path; a response whose state
        does not match what this process generated is discarded.
        """
        if not secrets.compare_digest(result.state, self.state):
            raise OAuthError("state_mismatch",
                             "The authorization response did not carry the state this "
                             "process issued; it has been discarded.")
        return result.code

    def exchange_code(self, result: AuthorizationResult, redirect_uri: str,
                      *, client: httpx.Client | None = None) -> TokenSet:
        import time

        code = self.validate_callback(result)
        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": self.code_verifier,
        }
        owns = client is None
        client = client or httpx.Client(timeout=30.0)
        try:
            resp = client.post(self.token_endpoint, data=payload)
        finally:
            if owns:
                client.close()
        if resp.status_code != 200:
            raise OAuthError("token_exchange_failed",
                             f"HTTP {resp.status_code}: {resp.text[:300]}")
        return self._to_token_set(resp.json())

    def refresh(self, refresh_token: str, *, client: httpx.Client | None = None) -> TokenSet:
        if not refresh_token:
            raise OAuthError("no_refresh_token",
                             "No refresh token was stored. Under an OAuth consent screen in "
                             "Testing status authorizations expire after seven days "
                             "(DF-17), so reauthorization is required.")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        owns = client is None
        client = client or httpx.Client(timeout=30.0)
        try:
            resp = client.post(self.token_endpoint, data=payload)
        finally:
            if owns:
                client.close()
        if resp.status_code != 200:
            raise OAuthError("refresh_failed", f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = self._to_token_set(resp.json())
        # Google does not always return a new refresh token on refresh; keep the old one.
        data.refresh_token = data.refresh_token or refresh_token
        return data

    @staticmethod
    def revoke(token: str, *, client: httpx.Client | None = None,
               revoke_endpoint: str = GOOGLE_REVOKE_ENDPOINT) -> bool:
        """Programmatically revoke a token. Required by COMP-04 the moment the user asks."""
        owns = client is None
        client = client or httpx.Client(timeout=30.0)
        try:
            resp = client.post(revoke_endpoint, data={"token": token})
        finally:
            if owns:
                client.close()
        # 200 = revoked; 400 with an invalid-token error also means it is no longer usable.
        return resp.status_code in (200, 400)

    @staticmethod
    def _to_token_set(data: dict) -> TokenSet:
        import time

        now = time.time()
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            expires_in=int(data.get("expires_in", 0)),
            scope=data.get("scope", ""),
            obtained_at=now,
            expires_at=now + int(data.get("expires_in", 0)) if data.get("expires_in") else None,
        )
