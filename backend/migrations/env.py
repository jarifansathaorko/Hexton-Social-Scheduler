"""Alembic environment.

The migration target is the application's own metadata, and the database URL comes
from application settings rather than from ``alembic.ini``. That is deliberate: the
``.ini`` value is only a fallback for ``alembic offline`` rendering, and a stale URL
in a config file is a classic way to migrate the wrong database.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Allow `alembic` to be run from the backend directory without installing first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hexton.config import get_settings  # noqa: E402
from hexton.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    # ``disable_existing_loggers=False`` is deliberate: without it, importing this
    # module tears down loggers pytest is using and the run loses its capture.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url", None)
    if override and override != "sqlite:///hexton.sqlite3":
        return override
    return f"sqlite+pysqlite:///{get_settings().database_path}"


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the table.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
