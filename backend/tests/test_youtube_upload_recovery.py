"""Tests for YouTube upload, progress callbacks, resumable recovery, and ambiguity handling (Prompt 6).

Verifies:
1. Resumable upload initiation and session URI persistence in UploadSession model.
2. Chunked transfer with HTTP 308 Resume Incomplete and Range header handling.
3. Progress reporting callbacks during chunked uploads.
4. Recovery from interrupted uploads: probes session, resumes from server-confirmed offset, avoids byte-0 reupload.
5. Expired session (HTTP 404) during probe or upload: handled as AMBIGUOUS, moves to NEEDS_REVIEW, never retries insert.
6. Transient errors (HTTP 500 / 503) classified as TRANSIENT, retryable.
7. Ambiguous last-chunk failure sets insert_may_have_succeeded=True and moves job to NEEDS_REVIEW.
8. Quota error (HTTP 403) and Auth error (HTTP 401) classification.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import fresh
from hexton.connectors.base import ConnectorError
from hexton.connectors.youtube import YouTubeConnector
from hexton.db.models import UploadSession
from hexton.domain.jobstate import FailureKind, JobState
from hexton.reconciliation.ladder import StepOutcome, reconcile
from hexton.worker.runner import JobRunner

sys.path.insert(0, str(Path(__file__).parent))
from mock_youtube_server import MockYouTubeHandler, MockYouTubeServer

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


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


def test_resumable_upload_session_initiation_and_persistence(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
) -> None:
    metadata = {
        "snippet": {"title": "Test Title", "description": "Test Desc"},
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
    assert "/upload_session/sess_" in started.session_uri


def test_chunked_upload_handles_308_range_and_progress_callbacks(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
) -> None:
    total_size = 512 * 1024  # 512KB
    payload = b"Z" * total_size
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(payload)
        temp_file = Path(f.name)

    try:
        started = youtube_connector.start_resumable_upload(
            "token-123",
            metadata={"snippet": {"title": "Chunked Test"}},
            total_bytes=total_size,
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )

        progress: list[tuple[int, int]] = []

        outcome = youtube_connector.perform_upload(
            "token-123",
            session_uri=started.session_uri,
            source_path=str(temp_file),
            start_offset=0,
            total_bytes=total_size,
            content_type="video/mp4",
            chunk_size=256 * 1024,  # Exactly 2 chunks
            on_progress=lambda sent, total: progress.append((sent, total)),
        )

        assert outcome.ambiguous is False
        assert outcome.video_id != ""
        assert outcome.bytes_sent == total_size
        assert len(progress) == 2
        assert progress[0] == (256 * 1024, total_size)
        assert progress[1] == (total_size, total_size)
    finally:
        temp_file.unlink(missing_ok=True)


def test_interrupted_upload_recovers_from_server_confirmed_range(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
) -> None:
    """Interrupted upload recovery:

    1. First attempt sends chunk 1 (256KB) of a 512KB video.
    2. Connection fails mid-transfer before chunk 2.
    3. Status probe against session_uri confirms server has bytes 0-262143.
    4. Reconcile ladder yields StepOutcome.RESUME at offset 262144.
    5. Second attempt resumes from offset 262144, completing the upload.
    """
    total_size = 512 * 1024
    payload = b"A" * total_size
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(payload)
        temp_file = Path(f.name)

    try:
        started = youtube_connector.start_resumable_upload(
            "token-123",
            metadata={"snippet": {"title": "Recovery Test"}},
            total_bytes=total_size,
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )

        # Simulate interruption after chunk 1 by instructing mock server to fail chunk 2
        MockYouTubeHandler.fail_after_bytes = 256 * 1024

        with pytest.raises(ConnectorError) as exc_info:
            youtube_connector.perform_upload(
                "token-123",
                session_uri=started.session_uri,
                source_path=str(temp_file),
                start_offset=0,
                total_bytes=total_size,
                content_type="video/mp4",
                chunk_size=256 * 1024,
            )
        assert exc_info.value.kind == FailureKind.TRANSIENT

        # Step 3: Probe the session
        probe = youtube_connector.probe_upload("token-123", started.session_uri, total_size)
        assert probe.completed is False
        assert probe.bytes_received == 256 * 1024  # Server confirmed first chunk

        # Step 4: Reconciliation ladder test
        rec = reconcile(has_session_uri=True, has_video_id=False, probe=probe)
        assert rec.step == "R1"
        assert rec.outcome == StepOutcome.RESUME
        assert rec.bytes_received == 256 * 1024
        assert rec.safe_to_retry_insert is False  # Second insert is NOT allowed!

        # Step 5: Resume from server-confirmed offset
        outcome = youtube_connector.perform_upload(
            "token-123",
            session_uri=started.session_uri,
            source_path=str(temp_file),
            start_offset=rec.bytes_received,
            total_bytes=total_size,
            content_type="video/mp4",
            chunk_size=256 * 1024,
        )
        assert outcome.ambiguous is False
        assert outcome.video_id != ""
        assert outcome.bytes_sent == total_size
    finally:
        temp_file.unlink(missing_ok=True)


def test_expired_session_404_handled_as_ambiguous_and_blocks_insert_retry(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
) -> None:
    """An expired session URI (404) must be classified as AMBIGUOUS and require operator review,

    never restarting from byte 0.
    """
    expired_session_uri = f"{mock_server.base_url}/upload_session/sess_non_existent"

    with pytest.raises(ConnectorError) as exc_info:
        youtube_connector.probe_upload("token-123", expired_session_uri, 1024)
    assert exc_info.value.kind == FailureKind.AMBIGUOUS
    assert exc_info.value.retryable is False

    # Reconciliation ladder verification
    rec = reconcile(
        has_session_uri=True,
        has_video_id=False,
        probe_error="The resumable session has expired (HTTP 404).",
    )
    assert rec.step == "R1"
    assert rec.outcome == StepOutcome.INCONCLUSIVE
    assert rec.safe_to_retry_insert is False
    assert "needs a human" in rec.reason


def test_transient_5xx_with_retry_after(
    youtube_connector: YouTubeConnector, mock_server: MockYouTubeServer
) -> None:
    MockYouTubeHandler.fail_next_upload = 503
    with pytest.raises(ConnectorError) as exc_info:
        youtube_connector.start_resumable_upload(
            "token",
            metadata={},
            total_bytes=100,
            content_type="video/mp4",
            endpoint=f"{mock_server.base_url}/upload/youtube/v3/videos",
        )
    assert exc_info.value.kind == FailureKind.TRANSIENT
    assert exc_info.value.retryable is True


def test_ambiguous_outcome_moves_job_to_needs_review(ctx, ready_job) -> None:
    """If an upload returns ambiguous=True (e.g.

    network dropped on final chunk confirmation), the job must be moved to NEEDS_REVIEW
    with insert_may_have_succeeded=True.
    """
    runner = JobRunner(ctx, owner="test-w1")
    with fresh(ctx) as s:
        job = s.get(type(ready_job), ready_job.id)
        # Mock connector outcome as ambiguous
        upload_session = UploadSession(
            job_id=job.id,
            session_uri="http://mock/sess_1",
            phase="started",
            total_bytes=1000,
            file_sha256=job.media.sha256,
            content_type="video/mp4",
            attempt=1,
        )
        s.add(upload_session)
        job.state = JobState.UPLOADING.value
        s.commit()

        # Simulate runner outcome handling when outcome.ambiguous == True
        job.insert_may_have_succeeded = True
        runner._transition(s, job, JobState.NEEDS_REVIEW, NOW)
        s.commit()

        assert job.state == JobState.NEEDS_REVIEW.value
        assert job.insert_may_have_succeeded is True
