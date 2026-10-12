"""SQLite engine and session management.

WAL is required (``02`` §2.1) so the API and the worker can hold the database
concurrently: WAL lets one writer and many readers proceed without blocking each
other, which is exactly the shape of this application.

Three settings matter and each is deliberate:

``journal_mode=WAL``
    See above. Without it the worker's claims block the API's reads on every tick.

``busy_timeout``
    The API and the worker are separate processes. A short write lock is normal and
    must be *waited out*, not reported as a failure.

``foreign_keys=ON``
    SQLite ships with foreign keys **off** by default. Turning it on per-connection
    is the only way the relationships below are actually enforced.

``BEGIN IMMEDIATE`` is used only for the job-claim operation, where two workers
racing for the same row must be serialized *before* reading, not after. Ordinary
reads use the default deferred transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

DEFAULT_BUSY_TIMEOUT_MS = 15_000


def build_engine(
    path: Path | str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    echo: bool = False,
) -> Engine:
    """Create the SQLite engine with the pragmas this application depends on."""
    path_str = str(path)
    if path_str != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite+pysqlite:///{path_str}"
    engine = create_engine(
        url,
        echo=echo,
        future=True,
        poolclass=QueuePool,
        # The API process is short-lived per request; a small pool keeps the
        # per-connection pragmas below cheap to apply.
        pool_size=5,
        max_overflow=5,
        connect_args={
            "check_same_thread": False,
            "timeout": busy_timeout_ms / 1000.0,
        },
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_conn: object, _record: object) -> None:  # pragma: no cover - driver
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def immediate_transaction(engine: Engine) -> Iterator[Connection]:
    """A ``BEGIN IMMEDIATE`` transaction on a dedicated connection.

    Used by the job-claim path. Under a deferred transaction two workers can both
    read the same claimable job and only discover the conflict when one of them
    writes, which wastes an attempt and can double-send an upload. Taking the write
    lock *before* the read turns the claim into a single serialized decision.

    The ``BEGIN`` is issued as SQL rather than through
    ``execution_options(isolation_level=...)`` because SQLAlchemy's SQLite dialect
    only accepts ``READ UNCOMMITTED`` / ``SERIALIZABLE`` / ``AUTOCOMMIT`` there and
    raises ``ArgumentError`` for ``"IMMEDIATE"``. It also has to be issued by hand
    rather than left to pysqlite, because that driver only opens a transaction
    implicitly *before DML* — a ``SELECT`` issued first would run in autocommit and
    two workers would both read the same row.

    This runs on a connection of its own rather than inside a ``Session``, because the
    claim is a small, self-contained read-modify-write that needs the database's
    write lock rather than the ORM's identity map. The caller loads the ORM object
    afterwards, in an ordinary session, once the claim is durable.
    """
    connection = engine.connect()
    with connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def is_writable(engine: Engine) -> bool:
    """Cheap liveness check used by the health endpoint."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one() == 1
