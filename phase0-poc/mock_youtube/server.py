"""A local HTTP server implementing the documented YouTube resumable upload protocol.

This exists so the *client* in ``hexton_poc.youtube.resumable`` can be exercised over real
HTTP - real sockets, real ``Content-Range`` headers, real status codes, real connection
drops - without touching Google.

What this does **not** do: it is not YouTube. It cannot verify anything about YouTube's
behaviour, and a passing test against it is evidence about the client only. Every test
that uses it is labelled as such, and the Google-facing questions remain BLOCKED in
``07-feasibility-checklist.md``.

Failure injection is deliberate: an ambiguous outcome (bytes accepted, response never
delivered) is the single hardest case in this product and must be reproducible.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


@dataclass
class VideoRecord:
    video_id: str
    metadata: dict
    received_bytes: int = 0
    data: bytearray = field(default_factory=bytearray)
    completed: bool = False


@dataclass
class SessionRecord:
    session_id: str
    total_bytes: int
    metadata: dict
    received: int = 0
    expired: bool = False
    video_id: str = ""
    data: bytearray = field(default_factory=bytearray)


class MockState:
    """Shared, thread-safe-ish state. Single-threaded usage in tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.videos: dict[str, VideoRecord] = {}
        self.insert_calls: list[dict] = []
        # Failure injection.
        self.drop_after_accepting: bool = False    # accept every byte, never respond
        self.fail_first_session_post: int = 0      # return 503 N times before succeeding
        self.forbid_advancing: bool = False        # always return 308 with no new Range
        self.expire_sessions: bool = False
        self.hang_seconds: float = 0.0
        self.stuck_range_high: int | None = None   # report a frozen Range, i.e. no progress
        self.put_429_count: int = 0                # rate-limit the next N data PUTs
        self.retry_after_seconds: str = "2"
        # Stand-ins for the documented read endpoints used by reconciliation/sweep.
        self.channel_id = "UCmockchannel00000000000000000000000"
        self.activities: list[dict] = []
        self.uploads_playlist: list[dict] = []
        self.quota_calls: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: MockState

    def log_message(self, *_args) -> None:
        pass

    # -- helpers -----------------------------------------------------------------

    def _range_header(self, sess: SessionRecord) -> str:
        """``Range: bytes=0-(received-1)``, or an empty header when nothing has landed."""
        if sess.received <= 0:
            return ""
        return f"bytes=0-{sess.received - 1}"

    def _json(self, code: int, payload: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, code: int, extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    # -- GET: the documented JSON reads used by reconciliation and the sweep -----
    #
    # Quota costs are recorded so the client can be checked against DF-12.

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        st = self.state
        path = parsed.path

        if path.endswith("/videos"):
            st.quota_calls.append("videos.list")
            vid = parse_qs(parsed.query).get("id", [""])[0]
            rec = st.videos.get(vid)
            if rec is None:
                self._json(200, {"kind": "youtube#videoListResponse", "items": []})
                return
            self._json(200, {"kind": "youtube#videoListResponse",
                             "items": [_video_resource(rec)]})
            return

        if path.endswith("/activities"):
            st.quota_calls.append("activities.list")
            self._json(200, {"kind": "youtube#activityListResponse",
                             "items": list(st.activities)})
            return

        if path.endswith("/channels"):
            st.quota_calls.append("channels.list")
            rel = {"uploads": "UUmockuploads0000000000000000000000000"}
            self._json(200, {
                "kind": "youtube#channelListResponse",
                "items": [{
                    "kind": "youtube#channel",
                    "id": st.channel_id,
                    "snippet": {"title": "Mock Channel", "handle": "@mockchannel"},
                    "contentDetails": {"relatedPlaylists": rel},
                    "statistics": {"subscriberCount": "12"},
                }],
            })
            return

        if path.endswith("/playlistItems"):
            st.quota_calls.append("playlistItems.list")
            self._json(200, {"kind": "youtube#playlistItemListResponse",
                             "items": list(st.uploads_playlist)})
            return

        if path.endswith("/mine"):
            st.quota_calls.append("channels.list")
            self._json(200, {"kind": "youtube#channelListResponse", "items": []})
            return

        self._json(404, {"error": {"message": f"mock server has no GET {path}"}})

    # -- POST: start a resumable session ----------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/upload/youtube/v3/videos"):
            self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            self._json(404, {"error": {"message": "not found"}})
            return
        qs = parse_qs(parsed.query)
        if qs.get("uploadType", [""])[0] != "resumable":
            self._json(400, {"error": {"message": "only resumable is implemented here"}})
            return

        st = self.state
        # Read the request body BEFORE any early response. Leaving it unread on a
        # keep-alive connection corrupts the next request on that connection.
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        if st.fail_first_session_post > 0:
            st.fail_first_session_post -= 1
            self._json(503, {"error": {"message": "injected transient failure"}})
            return

        try:
            metadata = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "metadata is not valid JSON"}})
            return

        # Enforce the documented privacy rules at the boundary, like the real API would.
        status = metadata.get("status", {}) or {}
        if "privacyStatus" not in status:
            # DF-02: omitting it is exactly the failure mode this product must avoid.
            self._json(400, {"error": {"message": "privacyStatus must be set explicitly",
                                       "reason": "injectedPolicy"}})
            return
        if "publishAt" in status and status.get("privacyStatus") != "private":
            self._json(400, {"error": {"message": "invalidPublishAt",
                                       "reason": "publishAt requires privacyStatus=private"}})
            return

        total = int(self.headers.get("X-Upload-Content-Length", "0"))
        if total <= 0:
            self._json(400, {"error": {"message": "X-Upload-Content-Length is required"}})
            return

        sid = uuid.uuid4().hex
        st.sessions[sid] = SessionRecord(sid, total, metadata)
        st.insert_calls.append({"kind": "session_start", "metadata": metadata, "total": total})
        self._empty(200, {"Location": f"http://{self.headers.get('Host')}/upload/session/{sid}"})

    # -- PUT: transfer / probe ---------------------------------------------------

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        st = self.state
        sid = parsed.path.rsplit("/", 1)[-1]

        # Drain the body BEFORE any early return. Leaving it unread corrupts the next
        # request on a keep-alive connection, which would be an unfaithful mock.
        content_range = self.headers.get("Content-Range", "")
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length) if length else b""

        sess = st.sessions.get(sid)
        if sess is None:
            self._json(404, {"error": {"message": "session not found"}})
            return
        if sess.expired or st.expire_sessions:
            self._json(404, {"error": {"message": "session expired"}})
            return

        # -- status probe: `Content-Range: bytes */TOTAL` ------------------------
        if content_range.startswith("bytes */"):
            if sess.received >= sess.total_bytes and sess.video_id:
                rec = st.videos[sess.video_id]
                self._json(201, _video_resource(rec))
                return
            self._empty(308, {"Range": self._range_header(sess)})
            return

        # -- data PUT ------------------------------------------------------------
        if st.put_429_count > 0:
            st.put_429_count -= 1
            self._empty(429, {"Retry-After": st.retry_after_seconds,
                              "X-Quota-Reason": "injected rate limit"})
            return

        spec = content_range.replace("bytes ", "").strip()
        range_part, _, total_part = spec.partition("/")
        first_s, _, last_s = range_part.partition("-")
        first, last, total = int(first_s), int(last_s), int(total_part)

        if total != sess.total_bytes:
            self._json(400, {"error": {"message": f"total mismatch: {total} != {sess.total_bytes}"}})
            return
        # DF-14: an overlapping or skipping resume uploads nothing at all. Model it.
        if first != sess.received:
            self._empty(308, {"Range": self._range_header(sess)})
            return

        if st.stuck_range_high is not None:
            # The connection succeeds but the server never acknowledges progress.
            self._empty(308, {"Range": f"bytes=0-{st.stuck_range_high}"})
            return

        sess.received += len(payload)
        sess.data.extend(payload)

        if sess.received < sess.total_bytes:
            self._empty(308, {"Range": self._range_header(sess)})
            return

        # Fully received -> create the video.
        vid = f"mock{uuid.uuid4().hex[:10]}"
        rec = VideoRecord(vid, sess.metadata, received_bytes=sess.received,
                          data=bytearray(sess.data), completed=True)
        st.videos[vid] = rec
        sess.video_id = vid
        st.insert_calls.append({"kind": "video_created", "video_id": vid})

        if st.drop_after_accepting:
            # The bytes landed and the video exists, but the response is never delivered.
            # This is the ambiguous outcome: the client cannot know what happened.
            try:
                self.connection.close()
            except OSError:
                pass
            return

        self._json(201, _video_resource(rec))


def _video_resource(rec: VideoRecord) -> dict:
    meta = rec.metadata or {}
    snippet = meta.get("snippet", {}) or {}
    status = meta.get("status", {}) or {}
    return {
        "kind": "youtube#video",
        "id": rec.video_id,
        "snippet": {
            "channelId": "UCmockchannel00000000000000000000000",
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
            "publishedAt": "2026-10-11T00:00:00Z",
        },
        "status": {
            "uploadStatus": "uploaded",
            "privacyStatus": status.get("privacyStatus", "public"),
            **({"publishAt": status["publishAt"]} if "publishAt" in status else {}),
        },
    }


class MockYouTubeServer:
    """Context manager wrapping the threaded HTTP server."""

    def __init__(self) -> None:
        self.state = MockState()
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockYouTubeServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def upload_endpoint(self) -> str:
        return f"{self.base_url}/upload/youtube/v3/videos"

    def youtube_v3_base(self) -> str:
        return f"{self.base_url}/youtube/v3"
