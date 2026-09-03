"""Alembic head must equal the models. Dev/test runs create_all; prod runs
alembic only — so nothing else notices when a model change ships without a
migration: 230 green tests and a broken prod deploy. This test builds one
database each way and compares the real SQLite schemas."""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]


def _schema(db_path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db_path)
    try:
        tables = sorted(
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if name != "alembic_version" and not name.startswith("sqlite_")
        )
        return {
            # (name, declared type, notnull, is-pk) per column — the shape a
            # deploy actually depends on; defaults/indexes are checked by use.
            t: [
                (row[1], row[2].upper(), row[3], row[5])
                for row in conn.execute(f"PRAGMA table_info({t})")
            ]
            for t in tables
        }
    finally:
        conn.close()


def test_alembic_head_matches_models(tmp_path: Path, monkeypatch) -> None:
    # Import at run time, not collection time: other tests reload app.* via
    # sys.modules.pop, and env.py imports whatever app.config is CURRENT —
    # patching a stale singleton points alembic at the wrong database.
    models = importlib.import_module("app.storage.models")
    settings = importlib.import_module("app.config").settings

    models_db = tmp_path / "models.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{models_db}")

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_all())

    alembic_db = tmp_path / "alembic.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{alembic_db}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "woolroom" / "migrations"))
    command.upgrade(cfg, "head")

    models_schema = _schema(models_db)
    alembic_schema = _schema(alembic_db)
    # Guard the instrument: two empty schemas compare equal too.
    assert len(models_schema) >= 10, f"create_all produced {len(models_schema)} tables"
    assert models_schema.keys() == alembic_schema.keys(), (
        "table sets differ — a model changed without a migration (or vice versa)"
    )
    for table in models_schema:
        assert models_schema[table] == alembic_schema[table], (
            f"columns differ on {table!r} — write the migration"
        )
