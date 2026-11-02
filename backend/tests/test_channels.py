"""Tests for the YouTube channel search and mention autocomplete router."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hexton.api.app import create_app
from hexton.api.deps import AppContext
from hexton.api.security import CSRF_HEADER


@pytest.fixture
def app(ctx: AppContext, tmp_path: Path) -> FastAPI:
    return create_app(ctx, frontend_dist=tmp_path / "not-built")


@pytest.fixture
def client(ctx: AppContext, app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as c:
        yield c


@pytest.fixture
def auth(ctx: AppContext, client: TestClient) -> dict[str, str]:
    response = client.post("/api/session")
    assert response.status_code == 200
    return {CSRF_HEADER: response.json()["csrf_token"]}


def test_channel_search_for_fomo(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/api/channels/search", params={"q": "fomo"}, headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    handles = [item["handle"].lower() for item in data]
    assert any("fomo" in h for h in handles)
    first = next(item for item in data if "fomo" in item["handle"].lower())
    assert first["title"]
    assert first["handle"]


def test_channel_search_with_at_prefix(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/api/channels/search", params={"q": "@MrBeast"}, headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert any("mrbeast" in item["handle"].lower() for item in data)


def test_channel_search_empty_query_rejected(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/api/channels/search", params={"q": ""}, headers=auth)
    assert resp.status_code == 422


def test_channel_search_finds_database_identity(
    client: TestClient, auth: dict[str, str], ctx: AppContext
) -> None:
    from hexton.db.models import ChannelIdentity, PlatformConnection

    with ctx.sessions() as session:
        conn = PlatformConnection(
            provider="youtube",
            label="Local Test Conn",
            status="active",
            scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        )
        session.add(conn)
        session.flush()

        ch = ChannelIdentity(
            connection_id=conn.id,
            channel_id="UC_custom_test_123",
            title="Custom Local Creator",
            handle="customlocalcreator",
            verification_source="operator_confirmed",
            subscriber_count=12500,
        )
        session.add(ch)
        session.commit()

    resp = client.get("/api/channels/search", params={"q": "@customlocalcreator"}, headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["handle"] == "customlocalcreator" for item in data)
    found = next(item for item in data if item["handle"] == "customlocalcreator")
    assert found["title"] == "Custom Local Creator"
    assert "12.5K" in found["subscriber_count"]

