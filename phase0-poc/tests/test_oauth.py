"""OAuth loopback flow: `state` validation, PKCE, and the minimal scope set.

SCOPE
-----
These tests exercise the client-side plumbing against a real local HTTP server. The token
exchange with Google itself is exercised against a local stand-in only. **No test in this
file contacts Google, and none of them establish that a real Google client will accept
this flow** - that requires a Cloud project and remains BLOCKED.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from hexton_poc.oauth.flow import (
    SHEETS_SCOPES,
    YOUTUBE_SCOPES,
    LoopbackServer,
    OAuthError,
    OAuthFlow,
    make_pkce,
)

CLIENT_ID = "example-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "example-secret"


def _flow(**kw) -> OAuthFlow:
    return OAuthFlow(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, **kw)


# -- PKCE -----------------------------------------------------------------------

def test_pkce_verifier_and_challenge_are_urlsafe_and_unpadded():
    verifier, challenge = make_pkce()
    assert "=" not in verifier and "=" not in challenge
    assert 43 <= len(verifier) <= 128, "RFC 7636 requires 43-128 characters"


def test_the_challenge_is_the_documented_s256_transform_of_the_verifier():
    verifier, challenge = make_pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_two_pkce_pairs_are_never_the_same():
    assert len({make_pkce() for _ in range(20)}) == 20


# -- authorization URL ----------------------------------------------------------

def test_the_authorization_url_carries_state_pkce_and_the_expected_parameters():
    flow = _flow()
    url, state, verifier = flow.authorization_url("http://127.0.0.1:9/oauth2/callback")
    q = parse_qs(urlsplit(url).query)

    assert urlsplit(url).netloc == "accounts.google.com"
    assert q["state"] == [state]
    assert q["code_challenge_method"] == ["S256"]
    assert q["access_type"] == ["offline"]     # offline -> a refresh token
    assert q["redirect_uri"] == ["http://127.0.0.1:9/oauth2/callback"]
    assert q["response_type"] == ["code"]
    # The challenge must match the verifier that was returned.
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expected]


def test_each_authorization_generates_a_fresh_state():
    flow = _flow()
    states = {flow.authorization_url("http://127.0.0.1:1/cb")[1] for _ in range(10)}
    assert len(states) == 10


def test_the_requested_scope_is_the_documented_minimum_for_this_use():
    assert YOUTUBE_SCOPES == ("https://www.googleapis.com/auth/youtube",)
    # youtube.upload alone cannot resolve the channel identity, which the confirmation
    # gate requires - so the slightly wider (but still narrow) youtube scope is correct.
    assert "youtube.force-ssl" not in " ".join(YOUTUBE_SCOPES)
    # The full Drive scope must never be requested; only file-picker access to our own file.
    assert not any(s.endswith("/drive") for s in SHEETS_SCOPES)
    assert SHEETS_SCOPES[-1].endswith("/drive.file")


# -- state validation -------------------------------------------------------------

def test_a_mismatched_state_is_discarded_not_exchanged():
    flow = _flow()
    flow.authorization_url("http://127.0.0.1:1/cb")
    from hexton_poc.oauth.flow import AuthorizationResult

    forged = AuthorizationResult(code="stolen-code", state="not-the-state-we-issued",
                                 raw_query={})
    with pytest.raises(OAuthError) as excinfo:
        flow.validate_callback(forged)
    assert excinfo.value.error == "state_mismatch"
    assert "discarded" in excinfo.value.description


def test_the_matching_state_returns_the_code():
    flow = _flow()
    _, state, _ = flow.authorization_url("http://127.0.0.1:1/cb")
    from hexton_poc.oauth.flow import AuthorizationResult

    ok = AuthorizationResult(code="real-code", state=state, raw_query={})
    assert flow.validate_callback(ok) == "real-code"


# -- loopback server end to end ------------------------------------------------------

def test_the_loopback_server_captures_a_single_authorization_response():
    flow = _flow()
    with LoopbackServer() as server:
        _, state, _ = flow.authorization_url(server.redirect_uri)
        assert server.redirect_uri.startswith("http://127.0.0.1:")
        assert server.redirect_uri.endswith("/oauth2/callback")

        # Simulate the browser following the redirect.
        resp = httpx.get(f"{server.redirect_uri}?{urlencode({'code': 'abc', 'state': state})}",
                         timeout=10.0)
        assert resp.status_code == 200
        assert "close this tab" in resp.text

        result, err = server.wait(timeout=5)
    assert err is None
    assert result.code == "abc" and result.state == state


def test_an_error_response_from_the_authorization_server_is_surfaced():
    flow = _flow()
    with LoopbackServer() as server:
        flow.authorization_url(server.redirect_uri)
        httpx.get(f"{server.redirect_uri}?{urlencode({'error': 'access_denied'})}", timeout=10.0)
        result, err = server.wait(timeout=5)
    assert result is None
    assert err is not None and err.error == "access_denied"


def test_an_unexpected_path_is_refused():
    with LoopbackServer() as server:
        root = server.redirect_uri.rsplit("/oauth2/callback", 1)[0]
        resp = httpx.get(f"{root}/evil", timeout=10.0)
        assert resp.status_code == 404


def test_a_malformed_response_without_code_or_state_is_refused():
    flow = _flow()
    with LoopbackServer() as server:
        flow.authorization_url(server.redirect_uri)
        httpx.get(f"{server.redirect_uri}?foo=bar", timeout=10.0)
        result, err = server.wait(timeout=5)
    assert result is None
    assert err is not None and err.error == "malformed_response"


# -- token endpoint plumbing (against a local stand-in) ------------------------------

class _TokenHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list = []

    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = parse_qs(self.rfile.read(length).decode())
        type(self).requests.append(body)
        if body.get("grant_type", [""])[0] == "refresh_token":
            payload = {"access_token": "refreshed", "expires_in": 3599, "token_type": "Bearer"}
        else:
            payload = {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3599,
                       "token_type": "Bearer", "scope": " ".join(YOUTUBE_SCOPES)}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def token_endpoint():
    _TokenHandler.requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_the_code_exchange_sends_pkce_and_returns_a_token_set(token_endpoint):
    from hexton_poc.oauth.flow import AuthorizationResult

    flow = _flow(token_endpoint=token_endpoint)
    url, state, verifier = flow.authorization_url("http://127.0.0.1:9/cb")
    tokens = flow.exchange_code(AuthorizationResult(code="c1", state=state, raw_query={}),
                                "http://127.0.0.1:9/cb")

    assert tokens.access_token == "at-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.expires_at is not None
    sent = _TokenHandler.requests[0]
    assert sent["code"] == ["c1"]
    assert sent["code_verifier"] == [verifier]
    assert sent["grant_type"] == ["authorization_code"]


def test_refresh_preserves_the_previous_refresh_token_when_none_is_returned(token_endpoint):
    flow = _flow(token_endpoint=token_endpoint)
    tokens = flow.refresh("original-refresh")
    assert tokens.access_token == "refreshed"
    assert tokens.refresh_token == "original-refresh"


def test_refresh_without_a_stored_refresh_token_explains_the_testing_mode_limit():
    """DF-17: in Testing status the refresh token expires after seven days."""
    flow = _flow()
    with pytest.raises(OAuthError) as excinfo:
        flow.refresh("")
    assert excinfo.value.error == "no_refresh_token"
    assert "seven days" in excinfo.value.description
