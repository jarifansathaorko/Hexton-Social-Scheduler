"""A lightweight in-process mock server for testing YouTubeConnector contract.

Simulates YouTube Data API v3 and OAuth endpoints:
- Resumable upload initiation (POST /upload/youtube/v3/videos?uploadType=resumable...)
- Chunked resumable upload (PUT /upload_session/...)
- Resumable probe (PUT with empty body and Content-Range: bytes */total)
- Channels list (GET /youtube/v3/channels?part=snippet,statistics&mine=true)
- Videos get/update/delete (GET/PUT/DELETE /youtube/v3/videos)
- OAuth token refresh (POST /token)
- OAuth token revoke (POST /revoke)
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse


class MockYouTubeHandler(BaseHTTPRequestHandler):
    # Class-level state managed by tests
    resumable_sessions: ClassVar[dict[str, dict[str, Any]]] = {}
    mock_channels: ClassVar[list[dict[str, Any]]] = []
    mock_videos: ClassVar[dict[str, dict[str, Any]]] = {}
    fail_next_upload: ClassVar[int | None] = None
    next_upload_quota_error: ClassVar[bool] = False
    fail_next_chunk: ClassVar[int | None] = None
    fail_next_chunk_retry_after: ClassVar[int | None] = None
    fail_after_bytes: ClassVar[int | None] = None

    def log_message(self, format: str, *args: Any) -> None:
        # Silence server request logs during test runs
        pass

    def _send_json(self, status: int, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/youtube/v3/channels":
            if qs.get("mine") == ["true"]:
                items = self.__class__.mock_channels
                self._send_json(200, {"kind": "youtube#channelListResponse", "items": items})
                return
            self._send_json(200, {"items": []})
            return

        if parsed.path == "/youtube/v3/videos":
            vid = qs.get("id", [""])[0]
            if vid in self.__class__.mock_videos:
                self._send_json(200, {"items": [self.__class__.mock_videos[vid]]})
            else:
                self._send_json(404, {"error": {"message": "Video not found", "code": 404}})
            return

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Resumable session initiation
        if parsed.path.endswith("/upload/youtube/v3/videos"):
            if self.__class__.fail_next_upload:
                code = self.__class__.fail_next_upload
                self.__class__.fail_next_upload = None
                if code == 403 and self.__class__.next_upload_quota_error:
                    self._send_json(403, {"error": {"message": "quotaExceeded", "code": 403}})
                else:
                    self._send_json(code, {"error": "Simulated error"})
                return

            # Generate unique session ID
            session_id = f"sess_{len(self.__class__.resumable_sessions) + 1}"
            session_url = f"http://{self.headers['Host']}/upload_session/{session_id}"
            metadata = json.loads(body.decode("utf-8")) if body else {}
            self.__class__.resumable_sessions[session_id] = {
                "metadata": metadata,
                "received_bytes": 0,
                "total_bytes": int(self.headers.get("X-Upload-Content-Length", 0)),
            }
            self.send_response(200)
            self.send_header("Location", session_url)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # OAuth token refresh
        if parsed.path == "/token":
            self._send_json(
                200,
                {
                    "access_token": "mock-refreshed-access-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
            return

        # OAuth token revoke
        if parsed.path == "/revoke":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_json(404, {"error": "Not Found"})

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if parsed.path.startswith("/upload_session/"):
            if self.__class__.fail_next_chunk is not None:
                status = self.__class__.fail_next_chunk
                retry_after = self.__class__.fail_next_chunk_retry_after
                self.__class__.fail_next_chunk = None
                self.__class__.fail_next_chunk_retry_after = None
                self.send_response(status)
                if retry_after is not None:
                    self.send_header("Retry-After", str(retry_after))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Simulated chunk error"}')
                return

            session_id = parsed.path.split("/")[-1]
            if session_id not in self.__class__.resumable_sessions:
                self._send_json(404, {"error": "Session Not Found"})
                return

            sess = self.__class__.resumable_sessions[session_id]
            content_range = self.headers.get("Content-Range", "")

            # Probe request: Content-Range: bytes */total
            if content_length == 0 and "bytes */" in content_range:
                if sess["received_bytes"] >= sess["total_bytes"]:
                    self._send_json(200, {"id": "mock_vid_probe"})
                else:
                    self.send_response(308)
                    if sess["received_bytes"] > 0:
                        self.send_header("Range", f"bytes=0-{sess['received_bytes'] - 1}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                return

            # Check if this chunk transfer should fail
            if (
                self.__class__.fail_after_bytes is not None
                and sess["received_bytes"] >= self.__class__.fail_after_bytes
            ):
                self.__class__.fail_after_bytes = None
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Simulated chunk error"}')
                return

            # Chunk transfer
            received = sess["received_bytes"] + len(body)
            sess["received_bytes"] = received
            if received >= sess["total_bytes"]:
                # Complete upload
                video_obj = {
                    "id": f"vid_{session_id}",
                    "snippet": sess["metadata"].get("snippet", {}),
                    "status": sess["metadata"].get("status", {}),
                }
                self.__class__.mock_videos[video_obj["id"]] = video_obj
                self._send_json(200, video_obj)
            else:
                # Incomplete chunk
                self.send_response(308)
                self.send_header("Range", f"bytes=0-{received - 1}")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return

        if parsed.path == "/youtube/v3/videos":
            data = json.loads(body.decode("utf-8")) if body else {}
            vid = data.get("id", "updated_vid")
            self.__class__.mock_videos[vid] = data
            self._send_json(200, data)
            return

        self._send_json(404, {"error": "Not Found"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/youtube/v3/videos":
            qs = parse_qs(parsed.query)
            vid = qs.get("id", [""])[0]
            self.__class__.mock_videos.pop(vid, None)
            self.send_response(204)
            self.end_headers()
            return

        self._send_json(404, {"error": "Not Found"})


class MockYouTubeServer:
    def __init__(self) -> None:
        self.server = HTTPServer(("127.0.0.1", 0), MockYouTubeHandler)
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.reset()
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def reset(self) -> None:
        MockYouTubeHandler.resumable_sessions.clear()
        MockYouTubeHandler.mock_channels.clear()
        MockYouTubeHandler.mock_videos.clear()
        MockYouTubeHandler.fail_next_upload = None
        MockYouTubeHandler.next_upload_quota_error = False
        MockYouTubeHandler.fail_next_chunk = None
        MockYouTubeHandler.fail_next_chunk_retry_after = None
        MockYouTubeHandler.fail_after_bytes = None
