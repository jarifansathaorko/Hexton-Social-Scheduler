"""Alembic and ``models.py`` must describe the same database.

Two code paths produce this schema — the declarative metadata, which is what the
application actually runs against in tests, and ``0001_initial_schema``, which is what
a real install gets. They are allowed to differ only in ways SQLite would notice. The
classic failure is a column added to a model after the migration was written: every
local test passes, and the first fresh install fails on the first insert.

These tests therefore build both databases from scratch and compare what comes out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from hexton.db.models import Base
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND / "migrations"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS))
    # ``%`` must be doubled: ``set_main_option`` interpolates, and a Windows temp
    # path can contain one.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _url_for(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def _describe(engine) -> dict:
    """The parts of a schema a mismatch on would actually break something."""
    inspector = inspect(engine)
    described: dict[str, dict] = {}
    for name in inspector.get_table_names():
        if name == "alembic_version":
            continue
        described[name] = {
            "columns": {
                column["name"]: (
                    str(column["type"]).lower(),
                    bool(column["nullable"]),
                )
                for column in inspector.get_columns(name)
            },
            "pk": sorted(inspector.get_pk_constraint(name).get("constrained_columns") or []),
            "fk": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    (fk.get("options") or {}).get("ondelete"),
                )
                for fk in inspector.get_foreign_keys(name)
            ),
        }
    return described


def _migrated(path: Path) -> dict:
    url = _url_for(path)
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    try:
        return _describe(engine)
    finally:
        engine.dispose()


def _from_metadata(path: Path) -> dict:
    engine = create_engine(_url_for(path))
    try:
        Base.metadata.create_all(engine)
        return _describe(engine)
    finally:
        engine.dispose()


@pytest.fixture
def migrated_schema(tmp_path) -> dict:
    return _migrated(tmp_path / "migrated.sqlite3")


def test_upgrade_head_creates_every_model_table(migrated_schema, tmp_path) -> None:
    # A second database built straight from the models, for the difference below.
    declared = _from_metadata(tmp_path / "declared.sqlite3")
    assert set(migrated_schema) == set(declared), (
        f"tables only in migration: {set(declared) - set(migrated_schema)}; "
        f"only in models: {set(migrated_schema) - set(declared)}"
    )
    assert len(migrated_schema) >= 10  # not vacuously passing


def test_the_migrated_schema_matches_the_models(migrated_schema, tmp_path) -> None:
    declared = _from_metadata(tmp_path / "declared2.sqlite3")

    for table, expected in declared.items():
        actual = migrated_schema[table]
        missing = set(expected["columns"]) - set(actual["columns"])
        assert not missing, f"{table}: migration never created {sorted(missing)}"
        extra = set(actual["columns"]) - set(expected["columns"])
        assert not extra, f"{table}: migration created {sorted(extra)} not in the models"

        for column, spec in expected["columns"].items():
            assert actual["columns"][column] == spec, (
                f"{table}.{column}: models say {spec}, migration says {actual['columns'][column]}"
            )
        assert actual["pk"] == expected["pk"], f"{table}: primary key differs"
        assert actual["fk"] == expected["fk"], (
            f"{table}: foreign keys differ\n models: {expected['fk']}\n migration: {actual['fk']}"
        )


def test_the_migration_can_be_reversed(migrated_schema, tmp_path) -> None:
    path = tmp_path / "downgrade.sqlite3"
    url = _url_for(path)
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(url)
    try:
        remaining = {
            name for name in inspect(engine).get_table_names() if name != "alembic_version"
        }
    finally:
        engine.dispose()
    assert remaining == set(), f"downgrade left {sorted(remaining)} behind"


def test_alembic_check_reports_no_drift(tmp_path) -> None:
    """Autogenerate's own diff between ``models.py`` and the migration.

    This is the check that would catch a column added to a model after the migration
    was written, which is exactly the drift the structural comparison above cannot
    see — there, a column missing from *both* passes unnoticed.
    """
    url = _url_for(tmp_path / "drift.sqlite3")
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises AutogenerateDiffsDetected if the two disagree


def test_a_second_upgrade_is_refused_by_alembic_itself(tmp_path) -> None:
    """The version table is the guard against migrating an already-migrated install."""
    path = tmp_path / "once.sqlite3"
    url = _url_for(path)
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # idempotent at the same revision

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            from sqlalchemy import text

            rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    finally:
        engine.dispose()
    # The head is read rather than asserted literally: a new migration must not require
    # editing this test, or the test starts guarding the migration list instead of the
    # behaviour.
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert [row[0] for row in rows] == [head]
