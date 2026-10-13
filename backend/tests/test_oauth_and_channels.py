"""OAuth regression, channel identity confirmation, and credential isolation tests.

Verifies requirements for Prompt 3 of the testing playbook:
- OAuth state parameter validation and replay attack prevention (403).
- Loopback redirect URI security.
- Scope handling and PKCE S256 verifier usage.
- Disable vs. Revoke semantics (disable preserves credentials, revoke purges and schedules retention deletion).
- Server-verified channel identity resolution and operator confirmation gate.
- Rejection of cross-connection channel injection (channel_mismatch).
- Redaction audit: credentials never exposed in DB models, API responses, or diagnostics.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hexton.api.app import create_app
from hexton.api.security import CSRF_HEADER
from hexton.bootstrap import AppContext, build_context
from hexton.config import Settings
from hexton.connectors.youtube import YouTubeConnector
from hexton.credentials.store import InMemoryCredentialStore
from hexton.db.models import Base, ChannelIdentity, DataRetention, PlatformConnection
from mock_youtube_server import MockYouTubeHandler, MockYouTubeServer

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="module")
def mock_server() -> Generator[MockYouTubeServer, None, None]:
    server = MockYouTubeServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def reset_server(mock_server: MockYouTubeServer) -> None:
    mock_server.reset()


@pytest.fixture
def oauth_ctx(tmp_path: Path, mock_server: MockYouTubeServer) -> Generator[AppContext, None, None]:
    settings = Settings(
        data_dir=tmp_path / "data",
        connector_kind="youtube",
        test_mode=True,
        credential_store_kind="in_memory",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
    )
    connector = YouTubeConnector(
        client_id="test-client-id",
        client_secret="test-client-secret",
        enabled=True,
        api_base_url=mock_server.base_url,
        upload_base_url=mock_server.base_url,
        oauth_base_url=mock_server.base_url,
    )
    context = build_context(
        settings,
        connector=connector,
        credential_store=InMemoryCredentialStore(),
        log_to_stderr=False,
    )
    Base.metadata.create_all(context.engine)
    yield context
    context.close()


@pytest.fixture
def client(oauth_ctx: AppContext) -> Generator[TestClient, None, None]:
    app = create_app(oauth_ctx)
    with TestClient(
        app, base_url=f"http://{oauth_ctx.settings.host}:{oauth_ctx.settings.port}"
    ) as c:
        yield c


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/session")
    assert resp.status_code == 200
    return {CSRF_HEADER: resp.json()["csrf_token"]}


def test_oauth_authorization_url_and_pkce(client: TestClient, auth_headers: dict[str, str]):
    # Create YouTube connection
    create_resp = client.post(
        "/api/connections",
        json={"provider": "youtube", "label": "My YouTube Channel"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    conn_id = create_resp.json()["id"]

    # Request authorization parameters
    auth_resp = client.post(f"/api/connections/{conn_id}/authorize", headers=auth_headers)
    assert auth_resp.status_code == 200
    data = auth_resp.json()
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in data["authorization_url"]
    assert "code_challenge=" in data["authorization_url"]
    assert "code_challenge_method=S256" in data["authorization_url"]
    assert "state=" in data["authorization_url"]
    assert "access_type=offline" in data["authorization_url"]
    assert data["redirect_uri"].startswith("http://127.0.0.1:")
    assert data["redirect_uri"].endswith("/oauth/callback")


def test_oauth_state_validation_and_replay_protection(
    client: TestClient, auth_headers: dict[str, str], oauth_ctx: AppContext
):
    create_resp = client.post(
        "/api/connections",
        json={"provider": "youtube", "label": "Channel for State Test"},
        headers=auth_headers,
    )
    conn_id = create_resp.json()["id"]

    auth_resp = client.post(f"/api/connections/{conn_id}/authorize", headers=auth_headers)
    valid_state = auth_resp.json()["state"]

    # 1. Invalid / unknown state is rejected with 403
    bad_resp = client.get("/oauth/callback?code=mock_code&state=invalid_state_123")
    assert bad_resp.status_code == 403

    # 2. Missing code or state returns 400
    missing_resp = client.get("/oauth/callback?code=mock_code")
    assert missing_resp.status_code == 400

    # 3. Setup mock channel on the server
    MockYouTubeHandler.mock_channels = [
        {
            "id": "UC_resolved_ch1",
            "snippet": {"title": "Resolved Channel One", "customUrl": "@ch1"},
            "statistics": {"subscriberCount": "500"},
        }
    ]

    # 4. Valid state exchange succeeds
    ok_resp = client.get(
        f"/oauth/callback?code=valid_code&state={valid_state}",
        headers={"Accept": "application/json"},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["status"] == "authorized"

    # Verify channel was resolved and added to database
    with oauth_ctx.sessions() as s:
        conn = s.get(PlatformConnection, conn_id)
        assert conn is not None
        assert conn.status == "active"
        identities = s.query(ChannelIdentity).filter_by(connection_id=conn_id).all()
        assert len(identities) == 1
        assert identities[0].channel_id == "UC_resolved_ch1"
        assert identities[0].title == "Resolved Channel One"
        # Operator confirmation is NOT yet granted
        assert identities[0].operator_confirmed_at is None

    # 5. Replay attack: calling again with the SAME valid_state must be rejected with 403
    replay_resp = client.get(
        f"/oauth/callback?code=valid_code&state={valid_state}",
        headers={"Accept": "application/json"},
    )
    assert replay_resp.status_code == 403


def test_disable_versus_revoke_semantics(
    client: TestClient, auth_headers: dict[str, str], oauth_ctx: AppContext
):
    # Setup active connection with credentials
    with oauth_ctx.sessions() as s:
        conn = PlatformConnection(
            provider="youtube",
            label="Test Live",
            status="active",
            scopes=["upload"],
            granted_at=datetime.now(UTC),
        )
        s.add(conn)
        s.commit()
        conn_id = conn.id
        public_id = conn.public_id

    key = f"conn:{public_id}"
    oauth_ctx.credentials.write(f"{key}:access", "secret-access-token")
    oauth_ctx.credentials.write(f"{key}:refresh", "secret-refresh-token")

    # 1. Disable connection: preserves credentials, changes status to disabled
    dis_resp = client.post(f"/api/connections/{conn_id}/disable", headers=auth_headers)
    assert dis_resp.status_code == 200
    assert dis_resp.json()["status"] == "disabled"
    assert oauth_ctx.credentials.read(f"{key}:access") == "secret-access-token"
    assert oauth_ctx.credentials.read(f"{key}:refresh") == "secret-refresh-token"

    # 2. Enable connection back: status becomes active
    en_resp = client.post(f"/api/connections/{conn_id}/enable", headers=auth_headers)
    assert en_resp.status_code == 200
    assert en_resp.json()["status"] == "active"

    # 3. Revoke connection: deletes credentials and records retention deadline
    rev_resp = client.post(f"/api/connections/{conn_id}/revoke", headers=auth_headers)
    assert rev_resp.status_code == 200
    assert rev_resp.json()["status"] == "revoked"

    # Credentials MUST be erased from store
    assert oauth_ctx.credentials.read(f"{key}:access") is None
    assert oauth_ctx.credentials.read(f"{key}:refresh") is None

    # DataRetention record exists
    with oauth_ctx.sessions() as s:
        retention = s.query(DataRetention).filter_by(subject_id=public_id).first()
        assert retention is not None
        assert retention.deadline is not None


def test_channel_operator_confirmation_toggle(
    client: TestClient, auth_headers: dict[str, str], oauth_ctx: AppContext
):
    with oauth_ctx.sessions() as s:
        conn = PlatformConnection(provider="youtube", label="Channel Owner", status="active")
        s.add(conn)
        s.flush()
        identity = ChannelIdentity(
            connection_id=conn.id,
            channel_id="UC_target",
            title="Target Channel",
            verification_source="channels_list_mine",
            verified_at=datetime.now(UTC),
            operator_confirmed_at=None,
        )
        s.add(identity)
        s.commit()
        identity_id = identity.id

    # Confirm channel identity
    conf_resp = client.post(
        f"/api/connections/channels/{identity_id}/confirm",
        headers=auth_headers,
    )
    assert conf_resp.status_code == 200
    assert conf_resp.json()["operator_confirmed_at"] is not None

    # Unconfirm channel identity
    unconf_resp = client.post(
        f"/api/connections/channels/{identity_id}/unconfirm",
        headers=auth_headers,
    )
    assert unconf_resp.status_code == 200
    assert unconf_resp.json()["operator_confirmed_at"] is None


def test_credential_redaction_guarantees(
    client: TestClient, auth_headers: dict[str, str], oauth_ctx: AppContext
):
    secret_access = "super-secret-access-token-999"
    secret_refresh = "super-secret-refresh-token-888"

    with oauth_ctx.sessions() as s:
        conn = PlatformConnection(provider="youtube", label="Secret Channel", status="active")
        s.add(conn)
        s.commit()
        conn_id = conn.id
        public_id = conn.public_id

    oauth_ctx.credentials.write(f"conn:{public_id}:access", secret_access)
    oauth_ctx.credentials.write(f"conn:{public_id}:refresh", secret_refresh)

    # 1. Inspect GET /api/connections
    list_resp = client.get("/api/connections")
    assert list_resp.status_code == 200
    list_text = list_resp.text
    assert secret_access not in list_text
    assert secret_refresh not in list_text

    # 2. Inspect diagnostics
    diag_resp = client.get("/api/diagnostics")
    if diag_resp.status_code == 200:
        diag_text = diag_resp.text
        assert secret_access not in diag_text
        assert secret_refresh not in diag_text

    # 3. Database inspection: PlatformConnection has NO token columns
    with oauth_ctx.sessions() as s:
        c = s.get(PlatformConnection, conn_id)
        assert not hasattr(c, "access_token")
        assert not hasattr(c, "refresh_token")


def test_arbitrary_channel_injection_rejected(
    client: TestClient, auth_headers: dict[str, str], oauth_ctx: AppContext, tmp_path: Path
):
    from conftest import make_clip
    from hexton.db.models import MediaFile

    clip_bytes = make_clip(tmp_path / "valid.mp4")
    with oauth_ctx.sessions() as s:
        conn1 = PlatformConnection(provider="youtube", label="Conn 1", status="active")
        conn2 = PlatformConnection(provider="youtube", label="Conn 2", status="active")
        s.add_all([conn1, conn2])
        s.flush()
        ch1 = ChannelIdentity(
            connection_id=conn1.id,
            channel_id="UC_1",
            title="Channel 1",
            verification_source="channels_list_mine",
            operator_confirmed_at=datetime.now(UTC),
        )
        ch2 = ChannelIdentity(
            connection_id=conn2.id,
            channel_id="UC_2",
            title="Channel 2",
            verification_source="channels_list_mine",
            operator_confirmed_at=datetime.now(UTC),
        )
        media = MediaFile(
            path=str(tmp_path / "valid.mp4"),
            original_name="valid.mp4",
            sha256="dummy",
            size_bytes=len(clip_bytes),
            is_valid=True,
        )
        s.add_all([ch1, ch2, media])
        s.commit()
        conn1_id = conn1.id
        ch2_id = ch2.id
        media_id = media.id

    # 1. Validation with nonexistent channel identity ID returns unknown_channel error
    val1 = client.post(
        "/api/jobs/validate",
        json={
            "media_id": media_id,
            "title": "Title",
            "description": "Desc",
            "privacy": "private",
            "connection_id": conn1_id,
            "channel_identity_id": 999999,
        },
        headers=auth_headers,
    )
    assert val1.status_code == 200
    assert any(i["code"] == "unknown_channel" for i in val1.json()["issues"])

    # 2. Validation with channel belonging to another connection returns channel_mismatch error
    val2 = client.post(
        "/api/jobs/validate",
        json={
            "media_id": media_id,
            "title": "Title",
            "description": "Desc",
            "privacy": "private",
            "connection_id": conn1_id,
            "channel_identity_id": ch2_id,
        },
        headers=auth_headers,
    )
    assert val2.status_code == 200
    assert any(i["code"] == "channel_mismatch" for i in val2.json()["issues"])
