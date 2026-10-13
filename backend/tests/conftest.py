"""Shared fixtures.

Every test runs against a real SQLite file in a temporary directory, a real
:class:`~hexton.connectors.fake.FakeConnector`, and an in-memory credential store.
Nothing is mocked at the SQLAlchemy level, because the properties under test —
``BEGIN IMMEDIATE`` claiming, WAL, foreign keys, transaction boundaries — only exist
in the real driver and would be invisible against an in-memory stub.
"""

from __future__ import annotations

import itertools
import struct
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hexton.bootstrap import AppContext, build_context
from hexton.config import Settings
from hexton.connectors.base import ChannelSummary
from hexton.connectors.fake import FakeState
from hexton.connectors.fake_sheets import SheetState
from hexton.connectors.sheets import OPTIONAL_COLUMNS
from hexton.credentials.store import InMemoryCredentialStore
from hexton.db.models import (
    Base,
    ContentMetadata,
    MediaFile,
    PlatformConnection,
    Schedule,
    ScheduledJob,
    SheetsConfig,
)
from sqlalchemy.orm import Session

#: The fake sheet never reads this back, and it is written only into the in-memory
#: credential store — so the secret-leak tests can assert on it without a real secret
#: ever existing anywhere.
FAKE_SHEETS_TOKEN = "sheets-access-token"

# ----------------------------------------------------------------------
# A clip that actually parses
# ----------------------------------------------------------------------


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def make_clip(
    path: Path,
    *,
    extra_bytes: int = 64,
    duration_seconds: float = 12.5,
    timescale: int = 600,
) -> bytes:
    """Write a minimal but genuinely parseable ISO-BMFF clip.

    Real bytes rather than a dummy payload: media validation reads the ``ftyp`` brand
    and the ``mvhd`` timescale out of the file, so a placeholder would make every
    media test pass for the wrong reason.
    """
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom" + b"iso2")
    mvhd = bytes([0, 0, 0, 0]) + struct.pack(">III", 0, 0, timescale)
    mvhd += struct.pack(">I", int(duration_seconds * timescale))
    mvhd += b"\x00" * 80
    moov = _box(b"moov", _box(b"mvhd", mvhd))
    free = _box(b"free", b"\x00" * extra_bytes)
    data = ftyp + moov + free
    path.write_bytes(data)
    return data


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        connector_kind="fake",
        test_mode=True,
        credential_store_kind="in_memory",
        display_timezone="Asia/Dhaka",
        worker_poll_seconds=0.01,
        lease_seconds=30,
        worker_max_attempts=3,
    )


@pytest.fixture
def fake_state() -> FakeState:
    return FakeState(
        channels=[
            ChannelSummary(channel_id="UC-main", title="Main channel", handle="@main"),
            ChannelSummary(
                channel_id="UC-brand",
                title="Brand channel",
                handle="@brand",
                is_brand_candidate=True,
            ),
        ]
    )


@pytest.fixture
def sheets_state() -> SheetState:
    """An empty, reachable fake spreadsheet.

    ``visible_spreadsheets`` and ``visible_worksheets`` are what make ``not_found``
    reachable in a test without a network: removing an entry is how an inaccessible
    worksheet is simulated, and it is the only way this suite can exercise that path.
    """
    return SheetState()


@pytest.fixture
def ctx(settings: Settings, fake_state: FakeState, sheets_state: SheetState) -> AppContext:
    context = build_context(
        settings,
        fake_state=fake_state,
        sheets_state=sheets_state,
        credential_store=InMemoryCredentialStore(),
        log_to_stderr=False,
    )
    Base.metadata.create_all(context.engine)
    yield context
    context.close()


@pytest.fixture
def session(ctx: AppContext):
    with ctx.sessions() as s:
        yield s


@contextmanager
def fresh(ctx: AppContext):
    """A brand-new session.

    The fixture session caches identity-map state, so a test that lets the worker write
    through a different connection must re-read through a fresh one — otherwise it
    would assert against the row as it was before the runner touched it.
    """
    with ctx.sessions() as s:
        yield s


def events_for(ctx: AppContext, job_id: int) -> list[tuple[str, str]]:
    """Every event recorded for a job, oldest first, as ``(kind, message)``."""
    from hexton.db.models import JobEvent

    with ctx.sessions() as s:
        rows = s.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.id.asc()).all()
        return [(r.kind, r.message) for r in rows]


#: A fixed clock. Every time-dependent test passes one of these explicitly rather than
#: reading the machine, so a slow run or a DST boundary cannot change the outcome —
#: and so ``now`` is always a UTC instant rather than whatever zone the PC is set to.
BASE = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
# FUTURE is dynamic so tests comparing against real-world datetime.now(UTC)
# (such as API validation) never fall inside the 30-min lead window or in the past.
# Fixed at 12:00:00 UTC so timezone display tests (12:00Z == 18:00 Asia/Dhaka) remain stable.
FUTURE = (datetime.now(UTC) + timedelta(days=7)).replace(hour=12, minute=0, second=0, microsecond=0)
PAST = BASE - timedelta(hours=3)


def reload(ctx: AppContext, job_id: int) -> ScheduledJob:
    """A job loaded eagerly and then detached, so assertions cannot go stale.

    The relationships are touched before expunging because a detached instance cannot
    lazily load one: the session is already closed by the time the test reads
    ``row.upload_sessions[0]``.
    """
    with ctx.sessions() as s:
        job = s.get(ScheduledJob, job_id)
        for rel in (
            "schedule",
            "content",
            "media",
            "connection",
            "channel",
            "upload_sessions",
            "observations",
            "events",
        ):
            getattr(job, rel)
        s.expunge(job)
        return job


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    make_clip(path)
    return path


@pytest.fixture
def connection(ctx: AppContext, session: Session) -> PlatformConnection:
    from hexton.db.models import ChannelIdentity

    conn = PlatformConnection(provider="youtube", label="Main", status="active", scopes=["upload"])
    session.add(conn)
    session.flush()
    ctx.credentials.write(f"conn:{conn.public_id}:access", "access-token")
    ctx.credentials.write(f"conn:{conn.public_id}:refresh", "refresh-token")
    channel = ChannelIdentity(
        connection_id=conn.id,
        channel_id="UC-main",
        title="Main channel",
        verification_source="channels_list_mine",
    )
    session.add(channel)
    session.commit()
    return conn


def make_media(session: Session, path: Path) -> MediaFile:
    from hexton.media import validate_file

    report = validate_file(path)
    media = MediaFile(
        path=str(path),
        original_name=path.name,
        sha256=report.sha256 or "",
        size_bytes=report.size_bytes,
        duration_seconds=report.duration_seconds,
        container=report.container,
        is_valid=True,
        validated_at=datetime.now(UTC),
    )
    session.add(media)
    session.commit()
    return media


def make_job(
    session: Session,
    media: MediaFile,
    connection: PlatformConnection | None = None,
    *,
    title: str = "A test Short",
    privacy: str = "private",
    publish_at: datetime | None = None,
    state: str = "JOB-READY",
    channel_id: int | None = None,
    idempotency_key: str | None = None,
) -> ScheduledJob:
    from hexton.db.models import ChannelIdentity

    if channel_id is None and connection is not None:
        channel = session.query(ChannelIdentity).filter_by(connection_id=connection.id).first()
        channel_id = channel.id if channel else None

    job = ScheduledJob(
        schedule=Schedule(
            publish_at_utc=publish_at,
            display_timezone="Asia/Dhaka",
            min_lead_minutes=0,
            report_date=publish_at,
        ),
        content=ContentMetadata(title=title, description="desc", privacy=privacy),
        media=media,
        connection_id=connection.id if connection else None,
        channel_identity_id=channel_id,
        state=state,
        max_attempts=3,
        # Unique per job: the column carries a unique constraint, and a test that
        # builds several jobs over one clip must not trip it by accident.
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )
    session.add(job)
    session.commit()
    return job


@pytest.fixture
def new_job(ctx, session, connection, clip):
    """Factory for a queued job over the standard clip and connection."""
    counter = itertools.count(1)

    def _new(
        *,
        title: str | None = None,
        publish_at: datetime | None = None,
        state: str = "JOB-QUEUED",
        media: MediaFile | None = None,
        **kwargs,
    ) -> ScheduledJob:
        n = next(counter)
        return make_job(
            session,
            media or make_media(session, clip),
            connection,
            title=title or f"Job {n}",
            publish_at=publish_at,
            state=state,
            **kwargs,
        )

    return _new


@pytest.fixture
def ready_job(
    ctx: AppContext, session: Session, connection: PlatformConnection, clip: Path
) -> ScheduledJob:
    media = make_media(session, clip)
    return make_job(session, media, connection, state="JOB-QUEUED")


@pytest.fixture
def sheets_connection(ctx: AppContext, session: Session) -> PlatformConnection:
    """A Google Sheets connection with a usable credential.

    The credential is written into the *in-memory* store, so nothing touches a real
    credential store — the same discipline the Phase 2 smoke run used.
    """
    from hexton.db.models import ChannelIdentity

    conn = PlatformConnection(
        provider="sheets", label="Sheets reporting", status="active", scopes=["spreadsheets"]
    )
    session.add(conn)
    session.flush()
    ctx.credentials.write(f"conn:{conn.public_id}:access", FAKE_SHEETS_TOKEN)
    ctx.credentials.write(f"conn:{conn.public_id}:refresh", "sheets-refresh-token")
    session.add(
        ChannelIdentity(
            connection_id=conn.id,
            channel_id="UC-main",
            title="Main channel",
            verification_source="operator_confirmed",
        )
    )
    session.commit()
    return conn


@pytest.fixture
def reporting(
    ctx: AppContext, session: Session, sheets_connection: PlatformConnection
) -> SheetsConfig:
    """A configured, enabled reporting destination over the fake sheet.

    Enabling goes through :func:`upsert_config` rather than by writing the column, so a
    test cannot accidentally configure something the operator could not have configured.
    """
    from hexton.services import sheets as sheets_service

    config = sheets_service.upsert_config(
        session,
        {
            "spreadsheet_id": "sheet-1",
            "worksheet": "Report",
            "connection_id": sheets_connection.id,
            "report_timezone": "Asia/Dhaka",
            "cutoff_time": "00:00",
            "include_optional_columns": list(OPTIONAL_COLUMNS),
            "enabled": True,
        },
        now=BASE,
    )
    session.commit()
    return config
