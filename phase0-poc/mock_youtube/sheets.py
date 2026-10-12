"""A local stand-in for ``spreadsheets.values`` implementing read-before-append.

Same principle as ``mock_youtube``: it lets the deduplication *logic* be exercised for
real, and it proves nothing about Google Sheets itself. ``FEAS-09`` stays BLOCKED.

It is deliberately unforgiving: ``append`` is not idempotent, exactly like the real API.
A retry after a timeout writes a second row. That is the behaviour the sync-key mechanism
exists to survive.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class SheetState:
    def __init__(self, key_column: str = "Sync Key") -> None:
        self.key_column = key_column
        self.rows: list[dict] = []
        self.append_calls = 0
        self.read_calls = 0
        # Injection: swallow the response after the append has been applied.
        self.drop_response_after_append: bool = False
        self.headers_row: list[str] | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: SheetState

    def log_message(self, *_args) -> None:
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        st = self.state
        st.read_calls += 1
        header = st.headers_row or [st.key_column]
        values = [header] + [[r.get(c, "") for c in header] for r in st.rows]
        self._json(200, {"range": "A:Z", "values": values})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        # The real path is .../values/<range>:append, so match the suffix, not a fixed name.
        if not parsed.path.endswith(":append"):
            self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            self._json(404, {"error": {"message": "not found"}})
            return
        qs = parse_qs(parsed.query)
        option = qs.get("valueInputOption", ["RAW"])[0]

        st = self.state
        length = int(self.headers.get("Content-Length", "0"))
        raw = json.loads(self.rfile.read(length) or b"{}")
        values = raw.get("values", [])
        st.append_calls += 1

        cols = st.headers_row or [st.key_column]
        for row in values:
            record = {}
            for i, col in enumerate(cols):
                record[col] = row[i] if i < len(row) else ""
            # Deliberately NOT idempotent: a second identical append adds a second row.
            st.rows.append(record)

        if st.drop_response_after_append:
            try:
                self.connection.close()
            except OSError:
                pass
            return

        self._json(200, {
            "updates": {"updatedRows": len(values)},
            "tableRange": "A1",
            "range": "A:Z",
            "_valueInputOptionEcho": option,
        })


class MockSheetsServer:
    def __init__(self, headers_row: list[str] | None = None) -> None:
        self.state = SheetState()
        self.state.headers_row = headers_row
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockSheetsServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
