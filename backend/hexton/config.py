"""Application configuration.

Settings are read once, at process startup, and passed down explicitly. There are no
scattered ``if TESTING`` flags anywhere in the production paths — the brief's rule is
that dependencies are chosen at startup, so :mod:`hexton.bootstrap` is the single
place that decides which connector, which credential store and which engine a process
gets.

Data directory layout (all under one root so a backup is one folder copy):

    %LOCALAPPDATA%\\Hexton\\
        hexton.sqlite3        the database
        media\\                imported source clips
        logs\\                 worker and API logs
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Hexton"


def default_data_dir() -> Path:
    """Per-user local application data. Never a shared or machine-wide location."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / "hexton"


class Settings(BaseSettings):
    """Runtime configuration. Environment variables override the defaults."""

    model_config = SettingsConfigDict(
        env_prefix="HEXTON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- network -----------------------------------------------------------
    #: Loopback only. Binding to 0.0.0.0 would expose a token-bearing local API to
    #: the network, and there is no requirement that justifies it.
    host: str = "127.0.0.1"
    port: int = 8787

    # --- storage -----------------------------------------------------------
    data_dir: Path = Field(default_factory=default_data_dir)
    database_filename: str = "hexton.sqlite3"

    # --- composition -------------------------------------------------------
    #: Selected once at process startup. "fake" (tests / first run) or "youtube".
    connector_kind: str = "fake"
    #: Selected once at process startup. "windows_credential_manager" | "dpapi_file"
    #: | "plaintext" (refused unless HEXTON_ALLOW_PLAINTEXT=1).
    credential_store_kind: str = "auto"

    # --- session security --------------------------------------------------
    #: 32 bytes of entropy, hex. Generated and persisted on first run into
    #: ``data_dir/session.key`` with user-only ACLs; never committed.
    session_cookie_name: str = "hexton_session"
    session_max_age_seconds: int = 12 * 60 * 60

    # --- scheduling --------------------------------------------------------
    display_timezone: str = "Asia/Dhaka"
    min_lead_minutes: int = 30
    warning_lead_minutes: int = 120
    missed_grace_minutes: int = 5

    # --- worker ------------------------------------------------------------
    worker_poll_seconds: float = 2.0
    #: A claimed job's lease. Must comfortably exceed the longest expected single
    #: step, because an expired lease means another worker may claim the same job.
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    worker_max_attempts: int = 3
    #: How long a completed job's row is kept before the sweeper may purge it.
    retention_days: int = 90

    # --- safety ------------------------------------------------------------
    #: Never enable in production. Exists so tests can run without Windows.
    allow_plaintext_credentials: bool = False

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        allowed = {"127.0.0.1", "localhost", "::1"}
        if value not in allowed:
            raise ValueError(
                f"host must be loopback only (one of {sorted(allowed)}); got {value!r}. "
                f"This API can mint and read authorization state, so binding it beyond "
                f"loopback is refused at configuration time rather than left to a "
                f"deployment note."
            )
        return value

    @field_validator("connector_kind")
    @classmethod
    def _known_connector(cls, value: str) -> str:
        if value not in {"fake", "youtube"}:
            raise ValueError(
                f"connector_kind must be 'fake' or 'youtube'; got {value!r}. The real "
                f"connector is not wired until FEAS-01..FEAS-05 pass."
            )
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_filename

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def session_key_path(self) -> Path:
        return self.data_dir / "session.key"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.media_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings.

    Cached so that a single process reads configuration exactly once, and so tests
    can clear it between cases with ``get_settings.cache_clear()``.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
