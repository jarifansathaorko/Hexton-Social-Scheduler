"""The composition root.

Every process — API, worker, CLI — builds its dependencies here, once, at startup,
and then passes them down explicitly. There are no service locators, no ``get_container()``
anywhere else, and no runtime feature flags in business logic. That is what makes
"which connector is this process using?" a question with one answer per process, and
what makes it impossible for a production code path to reach a test double.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from hexton.config import Settings, get_settings
from hexton.connectors.base import PlatformConnector
from hexton.connectors.fake import FakeState
from hexton.connectors.registry import (
    BuildProfile,
    select_connector,
    select_sheets_connector_factory,
)
from hexton.connectors.sheets import SheetConnector
from hexton.credentials.store import CredentialStore, select_credential_store
from hexton.db.session import build_engine, make_session_factory
from hexton.logging import configure_logging

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hexton.connectors.fake_sheets import SheetState

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppContext:
    """Everything a process needs, resolved at startup."""

    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    credentials: CredentialStore
    connector: PlatformConnector
    profile: BuildProfile
    #: Builds the Sheets connector for a connection's credential. Selected at startup so
    #: the build-profile gate is applied once, here, and cannot be bypassed later.
    sheets_connector_for: Callable[[object], SheetConnector] = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def is_packaged(self) -> bool:
        return self.profile is BuildProfile.PACKAGED

    def sheets(self, credentials: object) -> SheetConnector:
        """The Sheets connector for ``credentials``. Never a fake in a packaged build."""
        if self.sheets_connector_for is None:  # pragma: no cover - defensive
            raise RuntimeError("No Sheets connector factory was configured.")
        return self.sheets_connector_for(credentials)

    def close(self) -> None:
        self.engine.dispose()


def load_or_create_session_key(path: Path) -> bytes:
    """Load the cookie-signing key, creating it with tight permissions on first run.

    The key lives beside the database rather than in it, so a database file copied to
    another machine cannot forge a session for this one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_bytes().strip()
        if len(key) >= 32:
            return key
        log.warning("Session key at %s is too short; regenerating.", path)

    key = secrets.token_bytes(32)
    path.write_bytes(key.hex().encode("ascii"))
    try:
        # On POSIX this yields a ``rw-------`` file. On Windows the C runtime maps
        # only the read-only bit, so it is not a user-only ACL — the file's real
        # protection is the DACL it inherited from ``%LOCALAPPDATA%``. Kept because it
        # is correct where it can be correct, and spelled out because a comment
        # claiming "user-only ACLs" here was not true on this platform (``F-25``).
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform specific
        log.warning("Could not tighten permissions on %s", path)
    return key


def build_context(
    settings: Settings | None = None,
    *,
    profile: BuildProfile = BuildProfile.DEVELOPMENT,
    fake_state: FakeState | None = None,
    sheets_state: SheetState | None = None,
    engine: Engine | None = None,
    credential_store: CredentialStore | None = None,
    log_to_stderr: bool = True,
) -> AppContext:
    """Assemble a process's dependencies. Call once, at startup."""
    settings = settings or get_settings()
    settings.ensure_directories()
    configure_logging(log_dir=settings.log_dir, to_stderr=log_to_stderr)

    engine = engine or build_engine(settings.database_path)
    sessions = make_session_factory(engine)

    credentials = credential_store or select_credential_store(
        settings.credential_store_kind,
        data_dir=settings.data_dir,
        allow_plaintext=settings.allow_plaintext_credentials,
    )

    connector = select_connector(settings, profile=profile, fake_state=fake_state)
    sheets_factory = select_sheets_connector_factory(
        settings, profile=profile, fake_state=sheets_state
    )

    log.info(
        "Context built: profile=%s connector=%s credentials=%s database=%s",
        profile.value,
        connector.provider,
        credentials.name,
        settings.database_path,
    )
    return AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        credentials=credentials,
        connector=connector,
        profile=profile,
        sheets_connector_for=sheets_factory,
    )
