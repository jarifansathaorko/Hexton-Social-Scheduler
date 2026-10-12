"""First-party resumable upload client over ``httpx``.

Why not the Google client library: its chunked uploader does not expose or persist the
resumable ``Location`` session URI, which is precisely the state needed to recover an
interrupted upload across a process restart (``AC-MVP-006``, ``AC-MVP-010``, ``02`` §1).

Protocol implemented from ``docfacts.DF-13`` / ``DF-14`` / ``DF-15``:

* ``POST /upload/youtube/v3/videos?uploadType=resumable&part=...`` -> ``200`` + ``Location``.
* ``PUT <session>`` with ``Content-Range: bytes <first>-<last>/<total>``.
* ``308 Resume Incomplete`` + ``Range: bytes=0-N`` while incomplete; resume at ``N+1``.
* Empty ``PUT`` with ``Content-Range: bytes */<total>`` queries status.
* ``201`` completes and carries the video resource.
* ``404`` means the session expired: the upload must restart from zero, which is the exact
  situation that can produce a duplicate - so it is routed to reconciliation, never to a
  silent restart.

SAFETY RULE ENFORCED HERE: an ``insert`` whose response was never read sets
``insert_may_have_succeeded`` and is **never** re-issued automatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"

#: Documented resumable-response codes we treat as retryable-with-backoff.
RETRYABLE_5XX = {500, 502, 503, 504}


class UploadError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 retryable: bool = False, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class UploadOutcome:
    video_id: str
    raw: dict
    attempts: int


def parse_range_header(value: str | None) -> int:
    """Return the highest confirmed byte index from a ``Range`` header, or -1 if absent.

    ``Range: bytes=0-999999`` means the first 1,000,000 bytes are on the server, so the
    next byte to send is 1,000,000 (DF-14, zero-based and inclusive).
    """
    if not value:
        return -1
    value = value.strip()
    if not value.lower().startswith("bytes="):
        raise UploadError(f"unexpected Range header format: {value!r}")
    spec = value[len("bytes="):].strip()
    if "-" not in spec:
        raise UploadError(f"unexpected Range header format: {value!r}")
    high = spec.split("-", 1)[1].strip()
    if high == "":
        raise UploadError(f"unexpected Range header format: {value!r}")
    return int(high)


class ResumableUploader:
    """Drives one resumable upload, persisting state at every safe boundary."""

    def __init__(
        self,
        *,
        access_token_provider: Callable[[], str],
        client: httpx.Client,
        chunk_size: int = 8 * 1024 * 1024,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        on_progress: Callable[[int, int], None] | None = None,
        endpoint: str = UPLOAD_ENDPOINT,
        persist: Callable[[], None] | None = None,
    ) -> None:
        self._token = access_token_provider
        self._client = client
        self._endpoint = endpoint
        # Called after every state mutation. This is what makes a hard process kill
        # recoverable: the callback writes the session to disk before any further bytes
        # are sent, so a test can raise from inside it to simulate a power cut.
        self._persist = persist
        # DF-14: the chunk must be a multiple of 256 KB.
        if chunk_size % (256 * 1024) != 0:
            raise ValueError("chunk_size must be a multiple of 256 KiB (documented constraint)")
        self._chunk = chunk_size
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._on_progress = on_progress

    # -- headers ------------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token()}"}
        if extra:
            h.update(extra)
        return h

    def _save(self, session) -> None:
        if self._persist is not None:
            self._persist()

    # -- step 1: start the session ------------------------------------------------

    def start(self, session, metadata_body: dict, *, mime_type: str = "video/*") -> str:
        """POST to create the session, then persist the ``Location`` **before** any bytes."""
        path = Path(session.file_path)
        if not path.exists() or not path.is_file():
            raise UploadError(f"selected video does not exist or is not a file: {path}")
        size = path.stat().st_size
        if size != session.total_bytes:
            raise UploadError(
                f"file size changed: recorded {session.total_bytes} bytes, now {size}. "
                "Re-approve the asset before uploading."
            )
        try:
            with path.open("rb") as fh:
                fh.read(1)
        except OSError as exc:
            raise UploadError(f"video file is not readable: {exc}") from exc

        params = {"uploadType": "resumable", "part": "snippet,status"}
        resp = self._client.post(
            self._endpoint,
            params=params,
            json=metadata_body,
            headers=self._headers({
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": mime_type,
            }),
        )
        if resp.status_code != 200:
            raise UploadError(
                f"failed to start resumable session: HTTP {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
                retryable=resp.status_code in RETRYABLE_5XX or resp.status_code == 429,
            )
        location = resp.headers.get("Location")
        if not location:
            raise UploadError("200 response carried no Location header; cannot persist a session")

        # Persist BEFORE sending any media. A crash here loses nothing but the session.
        session.session_uri = location
        from .session import SessionPhase

        session.phase = SessionPhase.SESSION_ESTABLISHED
        self._save(session)
        return location

    # -- status probe -------------------------------------------------------------

    def probe(self, session) -> tuple[str, int, dict | None]:
        """Query an interrupted upload's status (documented step 4.1).

        Returns ``(kind, next_offset, video_resource)`` where ``kind`` is one of
        ``'completed'``, ``'in_progress'``, ``'expired'``.
        """
        from .session import SessionPhase

        if not session.session_uri:
            raise UploadError("no persisted session URI to probe")
        resp = self._client.put(
            session.session_uri,
            content=b"",
            headers=self._headers({
                "Content-Length": "0",
                "Content-Range": f"bytes */{session.total_bytes}",
            }),
        )
        if resp.status_code == 201:
            session.phase = SessionPhase.COMPLETED
            session.video_id = (resp.json() or {}).get("id", "")
            session.bytes_uploaded = session.total_bytes
            self._save(session)
            return "completed", session.total_bytes, (resp.json() or {})
        if resp.status_code == 404:
            # DF-15: the session URI has expired. Restarting from zero can duplicate.
            session.phase = SessionPhase.SESSION_EXPIRED
            session.insert_may_have_succeeded = True
            self._save(session)
            return "expired", session.bytes_uploaded, None
        if resp.status_code == 308:
            high = parse_range_header(resp.headers.get("Range"))
            # No Range header at all means nothing has been uploaded yet.
            session.bytes_uploaded = high + 1 if high >= 0 else 0
            self._save(session)
            return "in_progress", session.bytes_uploaded, None
        if resp.status_code in RETRYABLE_5XX:
            raise UploadError(f"probe returned HTTP {resp.status_code}",
                              status_code=resp.status_code, retryable=True)
        raise UploadError(f"unexpected probe response HTTP {resp.status_code} {resp.text[:200]}",
                          status_code=resp.status_code)

    # -- steps 3 and 4: transfer --------------------------------------------------

    def run(self, session, *, max_restarts: int = 0) -> UploadOutcome:
        """Transfer the file, resuming from the persisted offset.

        ``max_restarts`` defaults to **0**: on an expired session this method does *not*
        silently start over. It raises :class:`UploadError` with ``ambiguous=True`` so the
        caller must reconcile first (``02`` §6.2). That is the duplicate-prevention rule.
        """
        from .session import SessionPhase

        path = Path(session.file_path)
        attempts = 0
        consecutive_errors = 0

        while True:
            attempts += 1
            session.attempt = attempts
            first = session.bytes_uploaded
            last = min(first + self._chunk, session.total_bytes) - 1
            if first >= session.total_bytes:
                raise UploadError("nothing left to send but no 201 was received",
                                  ambiguous=True)

            session.phase = SessionPhase.UPLOADING
            try:
                with path.open("rb") as fh:
                    fh.seek(first)
                    payload = fh.read(last - first + 1)
                resp = self._client.put(
                    session.session_uri,
                    content=payload,
                    headers=self._headers({
                        "Content-Length": str(len(payload)),
                        "Content-Type": session.content_type,
                        "Content-Range": f"bytes {first}-{last}/{session.total_bytes}",
                    }),
                )
            except httpx.HTTPError as exc:
                # The request may have reached YouTube. We do not know, so we do not retry
                # the insert; we mark the session ambiguous and let reconciliation decide.
                session.insert_may_have_succeeded = True
                session.phase = SessionPhase.AMBIGUOUS
                session.last_error = f"{type(exc).__name__}: {exc}"
                self._save(session)
                raise UploadError(
                    f"transport failure at offset {first}: {exc}. The request may have been "
                    "accepted; the outcome must be reconciled, not retried.",
                    ambiguous=True,
                ) from exc

            if resp.status_code == 201:
                body = resp.json() or {}
                session.phase = SessionPhase.COMPLETED
                session.video_id = body.get("id", "")
                session.bytes_uploaded = session.total_bytes
                if not session.video_id:
                    raise UploadError("201 received but the response carried no video id",
                                      ambiguous=True)
                self._save(session)
                return UploadOutcome(video_id=session.video_id, raw=body, attempts=attempts)

            if resp.status_code == 308:
                high = parse_range_header(resp.headers.get("Range"))
                new_offset = high + 1 if high >= 0 else 0
                if new_offset <= session.bytes_uploaded:
                    # The server is not advancing. DF-14 warns that an overlapping or
                    # skipping resume uploads nothing at all - so back off rather than
                    # blast the same bytes again.
                    consecutive_errors += 1
                    if consecutive_errors > self._max_attempts:
                        raise UploadError("server is not advancing the offset; giving up",
                                          status_code=308, retryable=False)
                    self._sleep(min(2 ** consecutive_errors, 30))
                    continue
                session.bytes_uploaded = new_offset
                consecutive_errors = 0
                # Durable BEFORE reporting progress: a kill here must lose no ground.
                self._save(session)
                if self._on_progress:
                    self._on_progress(session.bytes_uploaded, session.total_bytes)
                continue

            if resp.status_code == 404:
                session.phase = SessionPhase.SESSION_EXPIRED
                session.insert_may_have_succeeded = True
                if max_restarts <= 0:
                    self._save(session)
                    raise UploadError(
                        "The resumable session has expired (HTTP 404). Restarting from byte "
                        "zero could create a duplicate video, so the upload is parked for "
                        "reconciliation rather than restarted.",
                        status_code=404,
                        ambiguous=True,
                    )
                max_restarts -= 1
                session.session_uri = ""
                session.bytes_uploaded = 0
                self._save(session)
                continue

            if resp.status_code == 429 or resp.status_code in RETRYABLE_5XX:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() \
                    else min(2 ** consecutive_errors, 60)
                consecutive_errors += 1
                if consecutive_errors > self._max_attempts:
                    raise UploadError(f"exhausted retries on HTTP {resp.status_code}",
                                      status_code=resp.status_code, retryable=False)
                self._sleep(delay)
                continue

            # Anything else is a permanent failure (4xx or an unlisted 5xx).
            raise UploadError(
                f"upload failed permanently: HTTP {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
                retryable=False,
            )
