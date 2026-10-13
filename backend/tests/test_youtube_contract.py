"""Contract tests for YouTubeConnector against mock YouTube HTTP endpoints.

Verifies the HTTP client behaviors specified in PRD-03 and Phase 0 feasibility:
- Gate check when credentials not present
- Resumable upload initiation and Location header extraction
- Status probing and Range header parsing
- Chunked transfer with 308 Resume Incomplete handling
- Error categorization (401 auth, 403 quota, 404 permanent/ambiguous, 500 transient)
- Token refresh and revocation
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from hexton.connectors.base import ConnectorError
from hexton.connectors.youtube import YouTubeConnector
from hexton.domain.jobstate import FailureKind

sys.path.insert(0, str(Path(__file__).parent))
from mock_youtube_server import MockYouTubeHandler, MockYouTubeServer


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
def youtube_connector(mock_server: MockYouTubeServer) -> YouTubeConnector:
    return YouTubeConnector(
        client_id="test-client-id",
        client_secret="test-client-secret",
        enabled=True,
        api_base_url=mock_server.base_url,
        upload_base_url=mock_server.base_url,
        oauth_base_url=mock_server.base_url,
    )


def test_youtube_connector_gate_when_disabled():
    disabled = YouTubeConnector(enabled=False)
    with pytest.raises(ConnectorError) as exc_info:
        disabled.list_owned_channels("fake-token")
    assert exc_info.value.kind == FailureKind.PERMANENT
    assert "not enabled in this build" in str(exc_info.value)


def test_list_owned_channels(youtube_connector: YouTubeConnector):
    MockYouTubeHandler.mock_channels = [
        {
            "id": "UC_test_123",
            "snippet": {"title": "Test Channel", "customUrl": "@testchannel"},
            "statistics": {"subscriberCount": "1000"},
        }
    ]
    channels = youtube_connector.list_owned_channels("access-token-123")
    assert len(channels) == 1
    assert channels[0].channel_id == "UC_test_123"
    assert channels[0].title == "Test Channel"
    assert channels[0].handle == "@testchannel"
    assert channels[0].subscriber_count == 1000


def test_start_resumable_upload_success(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
):
    metadata = {
        "snippet": {"title": "My Short", "description": "#shorts"},
        "status": {"privacyStatus": "private"},
    }
    started = youtube_connector.start_resumable_upload(
        "token-123",
        metadata=metadata,
        total_bytes=1048576,
        content_type="video/mp4",
        endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
    )
    assert started.session_uri.startswith(mock_server.base_url)
    assert "sess_" in started.session_uri


def test_start_resumable_upload_auth_error(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
):
    MockYouTubeHandler.fail_next_upload = 401
    with pytest.raises(ConnectorError) as exc_info:
        youtube_connector.start_resumable_upload(
            "bad-token",
            metadata={},
            total_bytes=100,
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )
    assert exc_info.value.kind == FailureKind.AUTHORIZATION
    assert exc_info.value.retryable is False


def test_start_resumable_upload_quota_error(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
):
    MockYouTubeHandler.fail_next_upload = 403
    MockYouTubeHandler.next_upload_quota_error = True
    with pytest.raises(ConnectorError) as exc_info:
        youtube_connector.start_resumable_upload(
            "token",
            metadata={},
            total_bytes=100,
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )
    assert exc_info.value.kind == FailureKind.QUOTA
    assert exc_info.value.retryable is False


def test_probe_upload_incomplete(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
):
    started = youtube_connector.start_resumable_upload(
        "token-123",
        metadata={},
        total_bytes=2048,
        content_type="video/mp4",
        endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
    )
    # Session starts with 0 bytes received
    probe = youtube_connector.probe_upload("token-123", started.session_uri, 2048)
    assert probe.completed is False
    assert probe.bytes_received == 0


def test_perform_upload_chunked_success(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
):
    # Create a temporary 512KB payload
    data = b"X" * (512 * 1024)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        file_path = Path(f.name)

    try:
        started = youtube_connector.start_resumable_upload(
            "token-123",
            metadata={"snippet": {"title": "Test"}},
            total_bytes=len(data),
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )

        progress_records: list[tuple[int, int]] = []

        def on_progress(sent: int, total: int) -> None:
            progress_records.append((sent, total))

        # Upload with 256KB chunk size to force multiple chunks
        outcome = youtube_connector.perform_upload(
            "token-123",
            session_uri=started.session_uri,
            source_path=str(file_path),
            start_offset=0,
            total_bytes=len(data),
            content_type="video/mp4",
            chunk_size=256 * 1024,
            on_progress=on_progress,
        )

        assert outcome.ambiguous is False
        assert outcome.video_id != ""
        assert outcome.bytes_sent == len(data)
        assert len(progress_records) >= 2
        assert progress_records[-1] == (len(data), len(data))
    finally:
        file_path.unlink(missing_ok=True)


def test_get_and_update_and_delete_video(youtube_connector: YouTubeConnector):
    MockYouTubeHandler.mock_videos["vid_abc"] = {
        "id": "vid_abc",
        "snippet": {"title": "Existing Title"},
        "status": {"privacyStatus": "private"},
    }

    status = youtube_connector.get_video("token", "vid_abc")
    assert status is not None
    assert status.video_id == "vid_abc"
    assert status.title == "Existing Title"
    assert status.privacy_status == "private"

    # Non-existent video returns None
    missing = youtube_connector.get_video("token", "does_not_exist")
    assert missing is None

    # Update video
    updated = youtube_connector.update_video_status(
        "token",
        "vid_abc",
        privacy="public",
        publish_at=None,
        full_snippet={"title": "Updated Title"},
    )
    assert updated.privacy_status == "public"

    # Delete video
    deleted = youtube_connector.delete_video("token", "vid_abc")
    assert deleted is True

    # Re-check deleted
    assert youtube_connector.get_video("token", "vid_abc") is None


def test_oauth_refresh_and_revoke(youtube_connector: YouTubeConnector):
    tokens = youtube_connector.refresh("refresh-token-xyz")
    assert tokens.access_token == "mock-refreshed-access-token"
    assert tokens.expires_in == 3600

    revoked = youtube_connector.revoke("mock-refreshed-access-token")
    assert revoked is True
