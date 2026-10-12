"""Durable upload-session persistence.

The whole recovery story rests on this state surviving a hard process kill. It is stored
in SQLite with WAL enabled so that the claims in ``02-technical-prd.md`` section 2.1
(WAL, busy_timeout, short transactions) are exercised rather than assumed.

Nothing here talks to the network.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SessionPhase(str, Enum):
    """Persisted phases. Every transition is written *before* the side effect it names."""

    CREATED = "created"              # intent recorded, no network yet
    SESSION_ESTABLISHED = "session_established"   # POST returned 200 + Location
    UPLOADING = "uploading"
    COMPLETED = "completed"          # 201 + video id persisted
    SESSION_EXPIRED = "session_expired"           # 404 on the session URI
    AMBIGUOUS = "ambiguous"          # request sent, outcome unknown
    FAILED = "failed"


@dataclass
class UploadSession:
    schedule_id: str
    connection_id: str
    file_path: str
    file_sha256: str
    total_bytes: int
    session_uri: str = ""
    phase: SessionPhase = SessionPhase.CREATED
    bytes_uploaded: int = 0          # highest contiguous byte confirmed by a Range header
    video_id: str = ""
    content_type: str = "video/*"
    attempt: int = 0
    insert_may_have_succeeded: bool = False
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def offset(self) -> int:
        """Next byte to send. DF-14: must be exactly one past the confirmed last byte."""
        return self.bytes_uploaded

    @property
    def remaining(self) -> int:
        return max(0, self.total_bytes - self.bytes_uploaded)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_session (
    schedule_id              TEXT PRIMARY KEY,
    connection_id            TEXT NOT NULL,
    file_path                TEXT NOT NULL,
    file_sha256              TEXT NOT NULL,
    total_bytes              INTEGER NOT NULL,
    session_uri              TEXT NOT NULL DEFAULT '',
    phase                    TEXT NOT NULL,
    bytes_uploaded           INTEGER NOT NULL DEFAULT 0,
    video_id                 TEXT NOT NULL DEFAULT '',
    content_type             TEXT NOT NULL DEFAULT 'video/*',
    attempt                  INTEGER NOT NULL DEFAULT 0,
    insert_may_have_succeeded INTEGER NOT NULL DEFAULT 0,
    last_error               TEXT NOT NULL DEFAULT '',
    updated_at               REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS job_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id TEXT NOT NULL,
    ts_utc     TEXT NOT NULL,
    severity   TEXT NOT NULL,
    type       TEXT NOT NULL,
    message    TEXT NOT NULL,
    context    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS quota_ledger (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    method    TEXT NOT NULL,
    units     INTEGER NOT NULL,
    bucket    TEXT NOT NULL,
    ts_utc    TEXT NOT NULL
);
"""


class SessionStore:
    """SQLite-backed store. Enable WAL once; keep write transactions short."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # §2.1: WAL so a long write does not stall readers; busy_timeout so a transient
        # lock waits instead of raising immediately.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- journal_mode is a persistent property; assert it rather than assume it -----
    @property
    def journal_mode(self) -> str:
        return str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])

    # -- upload sessions -----------------------------------------------------------

    def save(self, s: UploadSession) -> None:
        s.updated_at = time.time()
        self._conn.execute(
            """
            INSERT INTO upload_session (schedule_id, connection_id, file_path, file_sha256,
                total_bytes, session_uri, phase, bytes_uploaded, video_id, content_type,
                attempt, insert_may_have_succeeded, last_error, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(schedule_id) DO UPDATE SET
                session_uri=excluded.session_uri, phase=excluded.phase,
                bytes_uploaded=excluded.bytes_uploaded, video_id=excluded.video_id,
                insert_may_have_succeeded=excluded.insert_may_have_succeeded,
                last_error=excluded.last_error, updated_at=excluded.updated_at
            """,
            (s.schedule_id, s.connection_id, s.file_path, s.file_sha256, s.total_bytes,
             s.session_uri, s.phase.value, s.bytes_uploaded, s.video_id, s.content_type,
             s.attempt, int(s.insert_may_have_succeeded), s.last_error, s.updated_at),
        )

    def load(self, schedule_id: str) -> UploadSession | None:
        row = self._conn.execute(
            "SELECT * FROM upload_session WHERE schedule_id=?", (schedule_id,)
        ).fetchone()
        if row is None:
            return None
        return UploadSession(
            schedule_id=row["schedule_id"],
            connection_id=row["connection_id"],
            file_path=row["file_path"],
            file_sha256=row["file_sha256"],
            total_bytes=row["total_bytes"],
            session_uri=row["session_uri"],
            phase=SessionPhase(row["phase"]),
            bytes_uploaded=row["bytes_uploaded"],
            video_id=row["video_id"],
            content_type=row["content_type"],
            attempt=row["attempt"],
            insert_may_have_succeeded=bool(row["insert_may_have_succeeded"]),
            last_error=row["last_error"],
            updated_at=row["updated_at"],
        )

    def delete(self, schedule_id: str) -> None:
        self._conn.execute("DELETE FROM upload_session WHERE schedule_id=?", (schedule_id,))

    def pending(self) -> list[UploadSession]:
        rows = self._conn.execute(
            "SELECT schedule_id FROM upload_session WHERE phase IN (?,?)",
            (SessionPhase.SESSION_ESTABLISHED.value, SessionPhase.UPLOADING.value),
        ).fetchall()
        out = []
        for r in rows:
            s = self.load(r["schedule_id"])
            if s:
                out.append(s)
        return out

    def ambiguous(self) -> list[UploadSession]:
        rows = self._conn.execute(
            "SELECT schedule_id FROM upload_session WHERE insert_may_have_succeeded=1 "
            "AND video_id=''"
        ).fetchall()
        out = []
        for r in rows:
            s = self.load(r["schedule_id"])
            if s:
                out.append(s)
        return out

    # -- audit --------------------------------------------------------------------

    def event(self, schedule_id: str, severity: str, type_: str, message: str,
              context: str = "") -> None:
        from datetime import datetime, timezone

        self._conn.execute(
            "INSERT INTO job_event (schedule_id, ts_utc, severity, type, message, context) "
            "VALUES (?,?,?,?,?,?)",
            (schedule_id, datetime.now(timezone.utc).isoformat(), severity, type_, message, context),
        )

    def events(self, schedule_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM job_event WHERE schedule_id=? ORDER BY id", (schedule_id,)
        ).fetchall()

    # -- quota --------------------------------------------------------------------

    def record_quota(self, method: str, units: int, bucket: str) -> None:
        from datetime import datetime, timezone

        self._conn.execute(
            "INSERT INTO quota_ledger (method, units, bucket, ts_utc) VALUES (?,?,?,?)",
            (method, units, bucket, datetime.now(timezone.utc).isoformat()),
        )

    def quota_total(self, bucket: str | None = None) -> int:
        if bucket is None:
            row = self._conn.execute("SELECT COALESCE(SUM(units),0) FROM quota_ledger").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(units),0) FROM quota_ledger WHERE bucket=?", (bucket,)
            ).fetchone()
        return int(row[0])


def file_size(path: str | Path) -> int:
    return os.path.getsize(path)
