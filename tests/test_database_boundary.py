from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

import woolroom
from app.storage.models import Base
from woolroom.database import _fingerprint_schema, main


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _create_canonical_unversioned(path: Path) -> None:
    engine = create_async_engine(_url(path))

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())


def _rows(path: Path, table: str) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(f'SELECT * FROM "{table}" ORDER BY 1'))


def _assert_refused_without_writes(
    path: Path,
    operation,
    expected_state: woolroom.DatabaseState,
) -> woolroom.DatabaseInspection:
    before = path.read_bytes()
    with pytest.raises(woolroom.DatabaseBoundaryError) as caught:
        operation()
    assert path.read_bytes() == before
    assert caught.value.inspection is not None
    assert caught.value.inspection.state is expected_state
    return caught.value.inspection


def test_database_boundary_is_part_of_the_public_distribution_api() -> None:
    public_api = {
        "DatabaseBoundaryError",
        "DatabaseInspection",
        "DatabaseState",
        "adopt_database",
        "inspect_database",
        "migration_head",
        "migration_revisions",
        "upgrade_database",
    }

    assert public_api <= set(woolroom.__all__)
    assert all(hasattr(woolroom, name) for name in public_api)


def test_fresh_upgrade_is_idempotent_and_uses_packaged_head(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    url = _url(path)

    assert woolroom.inspect_database(url).state is woolroom.DatabaseState.EMPTY
    first = woolroom.upgrade_database(url)
    first_bytes = path.read_bytes()
    second = woolroom.upgrade_database(url)

    config = Config()
    config.set_main_option("script_location", str(woolroom.migration_path()))
    scripts = ScriptDirectory.from_config(config)
    assert woolroom.migration_revisions() == (scripts.get_base(),)
    assert woolroom.migration_head() == scripts.get_current_head()
    assert first == second
    assert second.state is woolroom.DatabaseState.VERSIONED
    assert second.current_revisions == (woolroom.migration_head(),)
    assert path.read_bytes() == first_bytes


def test_upgrade_refuses_canonical_unversioned_before_ddl(tmp_path: Path) -> None:
    path = tmp_path / "canonical.db"
    _create_canonical_unversioned(path)

    inspection = _assert_refused_without_writes(
        path,
        lambda: woolroom.upgrade_database(_url(path)),
        woolroom.DatabaseState.CANONICAL_UNVERSIONED,
    )

    assert inspection.can_adopt is True
    assert "explicit adoption" in str(
        pytest.raises(
            woolroom.DatabaseBoundaryError,
            woolroom.upgrade_database,
            _url(path),
        ).value
    )


@pytest.mark.parametrize(
    ("revisions", "expected_state"),
    [
        (("not-a-woolroom-revision",), woolroom.DatabaseState.UNKNOWN_REVISION),
        (
            (woolroom.migration_head(), "not-a-woolroom-revision"),
            woolroom.DatabaseState.MULTIPLE_REVISIONS,
        ),
        ((), woolroom.DatabaseState.EMPTY_VERSION_TABLE),
    ],
)
def test_upgrade_refuses_unsafe_version_markers_without_writes(
    tmp_path: Path,
    revisions: tuple[str, ...],
    expected_state: woolroom.DatabaseState,
) -> None:
    path = tmp_path / f"{expected_state.value}.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.executemany(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            ((revision,) for revision in revisions),
        )
        connection.execute("CREATE TABLE plugin_cards (pet_id TEXT PRIMARY KEY, label TEXT)")
        connection.execute("INSERT INTO plugin_cards VALUES ('pet-1', 'kept')")

    rows = _rows(path, "plugin_cards")
    _assert_refused_without_writes(
        path,
        lambda: woolroom.upgrade_database(_url(path)),
        expected_state,
    )
    assert _rows(path, "plugin_cards") == rows


def test_case_variant_version_marker_cannot_bypass_explicit_adoption(tmp_path: Path) -> None:
    path = tmp_path / "case-variant-version.db"
    _create_canonical_unversioned(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE Alembic_Version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO Alembic_Version (version_num) VALUES ('not-a-woolroom-revision')"
        )

    inspection = woolroom.inspect_database(_url(path))
    assert inspection.state is woolroom.DatabaseState.UNKNOWN_REVISION
    assert inspection.can_adopt is False
    _assert_refused_without_writes(
        path,
        lambda: woolroom.adopt_database(_url(path), apply=True),
        woolroom.DatabaseState.UNKNOWN_REVISION,
    )


def test_upgrade_refuses_invalid_version_table_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "invalid-version-table.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (wrong_column TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('kept')")

    _assert_refused_without_writes(
        path,
        lambda: woolroom.upgrade_database(_url(path)),
        woolroom.DatabaseState.INVALID_VERSION_TABLE,
    )


def test_explicit_adoption_preserves_core_values_and_extra_plugin_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "adopt.db"
    url = _url(path)
    _create_canonical_unversioned(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, partner_aliases, last_room_pet_id) "
            "VALUES ('person-1', 'Keeper', '{\"person-2\": \"Pal\"}', NULL)"
        )
        connection.execute(
            "INSERT INTO pets "
            "(id, name, temperament, quirks, species, household_id, mood_arousal, "
            "mood_valence, animation_state, coat) "
            "VALUES ('pet-1', 'Moss', '{}', '[]', 'dog', 'pet-1', 41, 67, 'sleeping', 'red')"
        )
        connection.execute(
            "CREATE TABLE plugin_cards "
            "(pet_id TEXT PRIMARY KEY, title TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO plugin_cards VALUES ('pet-1', 'A private card', '{\"kind\": 7}')"
        )

    users = _rows(path, "users")
    pets = _rows(path, "pets")
    cards = _rows(path, "plugin_cards")
    before = path.read_bytes()

    dry_run = woolroom.adopt_database(url)
    assert dry_run.state is woolroom.DatabaseState.CANONICAL_UNVERSIONED
    assert dry_run.extra_tables == ("plugin_cards",)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone() is None

    adopted = woolroom.adopt_database(url, apply=True)
    assert adopted.state is woolroom.DatabaseState.VERSIONED
    assert adopted.current_revisions == (woolroom.migration_head(),)
    assert adopted.extra_tables == ("plugin_cards",)
    assert _rows(path, "users") == users
    assert _rows(path, "pets") == pets
    assert _rows(path, "plugin_cards") == cards
    assert woolroom.upgrade_database(url).at_head is True


def test_known_woolroom_revision_is_the_upgrade_authority(tmp_path: Path) -> None:
    path = tmp_path / "privately-verified.db"
    _create_canonical_unversioned(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER deployment_guard BEFORE UPDATE ON pets "
            "BEGIN SELECT RAISE(ABORT, 'guarded'); END"
        )
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (woolroom.migration_head(),),
        )

    before = path.read_bytes()
    inspection = woolroom.upgrade_database(_url(path))

    assert inspection.at_head is True
    assert path.read_bytes() == before


def test_known_revision_upgrade_refuses_core_foreign_key_violations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orphaned-core-row.db"
    woolroom.upgrade_database(_url(path))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO buffer_events (pet_id, event_type, meta) "
            "VALUES ('missing-pet', 'message', '{}')"
        )

    rows = _rows(path, "buffer_events")
    _assert_refused_without_writes(
        path,
        lambda: woolroom.upgrade_database(_url(path)),
        woolroom.DatabaseState.CORE_FOREIGN_KEY_VIOLATION,
    )
    assert _rows(path, "buffer_events") == rows


def test_adoption_refuses_partial_core_schema_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE users ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY, "
            "display_name VARCHAR(64) NOT NULL, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "last_seen_at DATETIME, partner_aliases JSON NOT NULL, "
            "last_room_pet_id VARCHAR(32))"
        )
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, partner_aliases) VALUES ('person-1', 'Keeper', '{}')"
        )

    rows = _rows(path, "users")
    inspection = _assert_refused_without_writes(
        path,
        lambda: woolroom.adopt_database(_url(path), apply=True),
        woolroom.DatabaseState.NONCANONICAL_UNVERSIONED,
    )
    assert any(difference.startswith("missing core table:") for difference in inspection.differences)
    assert _rows(path, "users") == rows


@pytest.mark.parametrize("mutation", ["check", "trigger"])
def test_adoption_refuses_core_constraints_and_triggers_without_writes(
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / f"{mutation}.db"
    _create_canonical_unversioned(path)
    with sqlite3.connect(path) as connection:
        if mutation == "check":
            connection.execute(
                "ALTER TABLE eval_runs ADD COLUMN plugin_score INTEGER CHECK (plugin_score >= 0)"
            )
        else:
            connection.execute(
                "CREATE TRIGGER plugin_pet_guard BEFORE UPDATE ON pets "
                "BEGIN SELECT RAISE(ABORT, 'guarded'); END"
            )

    inspection = _assert_refused_without_writes(
        path,
        lambda: woolroom.adopt_database(_url(path), apply=True),
        woolroom.DatabaseState.NONCANONICAL_UNVERSIONED,
    )
    assert any(f"differs: {mutation}s" in difference for difference in inspection.differences)


def test_semantic_fingerprint_ignores_physical_order_and_object_names(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    with sqlite3.connect(first_path) as connection:
        connection.executescript(
            """
            CREATE TABLE parent (id TEXT NOT NULL PRIMARY KEY);
            CREATE TABLE sample (
                left_id TEXT NOT NULL,
                right_id TEXT NOT NULL,
                created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
                CONSTRAINT first_pk PRIMARY KEY (left_id, right_id),
                CONSTRAINT first_unique UNIQUE (right_id),
                CONSTRAINT first_fk FOREIGN KEY (left_id) REFERENCES parent(id)
                    ON DELETE CASCADE,
                CONSTRAINT first_check CHECK (length(right_id) > 0)
            );
            CREATE INDEX first_lookup ON sample (created_at DESC);
            CREATE TRIGGER first_trigger AFTER UPDATE ON sample
                BEGIN UPDATE parent SET id = id WHERE id = NEW.left_id; END;
            """
        )
    with sqlite3.connect(second_path) as connection:
        connection.executescript(
            """
            CREATE TABLE parent (id TEXT NOT NULL PRIMARY KEY);
            CREATE TABLE sample (
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                right_id TEXT NOT NULL,
                left_id TEXT NOT NULL,
                CONSTRAINT renamed_pk PRIMARY KEY (left_id, right_id),
                CONSTRAINT renamed_unique UNIQUE (right_id),
                CONSTRAINT renamed_fk FOREIGN KEY (left_id) REFERENCES parent(id)
                    ON DELETE CASCADE,
                CONSTRAINT renamed_check CHECK (length(right_id)>0)
            );
            CREATE INDEX renamed_lookup ON sample (created_at DESC);
            CREATE TRIGGER renamed_trigger AFTER UPDATE ON sample
                BEGIN UPDATE parent SET id=id WHERE id=NEW.left_id; END;
            """
        )

    with sqlite3.connect(first_path) as first, sqlite3.connect(second_path) as second:
        first_fingerprint = _fingerprint_schema(first, table_names=("parent", "sample"))
        second_fingerprint = _fingerprint_schema(second, table_names=("parent", "sample"))

    assert first_fingerprint == second_fingerprint


def test_semantic_fingerprint_covers_required_schema_dimensions(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.db"
    changed_path = tmp_path / "changed.db"
    common = """
        CREATE TABLE parent (id TEXT NOT NULL PRIMARY KEY);
        CREATE TABLE sample (
            left_id TEXT NOT NULL,
            rank INTEGER DEFAULT 1,
            CONSTRAINT sample_pk PRIMARY KEY (left_id, rank),
            CONSTRAINT sample_fk FOREIGN KEY (left_id) REFERENCES parent(id) ON DELETE CASCADE,
            CONSTRAINT sample_check CHECK (rank >= 0)
        );
        CREATE UNIQUE INDEX sample_rank ON sample (rank);
        CREATE TRIGGER sample_trigger AFTER UPDATE ON sample
            BEGIN UPDATE parent SET id = id WHERE id = NEW.left_id; END;
    """
    changed = common.replace("rank INTEGER DEFAULT 1", "rank TEXT NOT NULL DEFAULT 2")
    changed = changed.replace("PRIMARY KEY (left_id, rank)", "PRIMARY KEY (rank, left_id)")
    changed = changed.replace("ON DELETE CASCADE", "ON DELETE RESTRICT")
    changed = changed.replace("CHECK (rank >= 0)", "CHECK (rank > 0)")
    changed = changed.replace("CREATE UNIQUE INDEX", "CREATE INDEX")
    changed = changed.replace("AFTER UPDATE", "BEFORE UPDATE")
    with sqlite3.connect(baseline_path) as connection:
        connection.executescript(common)
    with sqlite3.connect(changed_path) as connection:
        connection.executescript(changed)

    with sqlite3.connect(baseline_path) as baseline, sqlite3.connect(changed_path) as changed_db:
        baseline_table = _fingerprint_schema(baseline, table_names=("sample",)).as_dict()[
            "sample"
        ]
        changed_table = _fingerprint_schema(changed_db, table_names=("sample",)).as_dict()[
            "sample"
        ]

    assert baseline_table.columns != changed_table.columns
    assert baseline_table.primary_key != changed_table.primary_key
    assert baseline_table.indexes != changed_table.indexes
    assert baseline_table.foreign_keys != changed_table.foreign_keys
    assert baseline_table.checks != changed_table.checks
    assert baseline_table.triggers != changed_table.triggers


def test_migration_env_honors_explicit_url_and_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ignored_path = tmp_path / "ignored.db"
    explicit_url_path = tmp_path / "explicit-url.db"
    explicit_connection_path = tmp_path / "explicit-connection.db"
    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings,
        "database_url",
        _url(ignored_path),
    )

    url_config = Config()
    url_config.set_main_option("script_location", str(woolroom.migration_path()))
    url_config.set_main_option("sqlalchemy.url", _url(explicit_url_path))
    command.upgrade(url_config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{explicit_connection_path}")
    try:
        with engine.begin() as connection:
            connection_config = Config()
            connection_config.set_main_option(
                "script_location", str(woolroom.migration_path())
            )
            connection_config.attributes["connection"] = connection
            command.upgrade(connection_config, "head")
    finally:
        engine.dispose()

    assert explicit_url_path.exists()
    assert explicit_connection_path.exists()
    assert not ignored_path.exists()
    for path in (explicit_url_path, explicit_connection_path):
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == (woolroom.migration_head(),)


def test_database_cli_adoption_is_dry_run_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "cli.db"
    _create_canonical_unversioned(path)
    before = path.read_bytes()

    assert main(["adopt", "--database-url", _url(path)]) == 0
    dry_payload = json.loads(capsys.readouterr().out)
    assert dry_payload["state"] == "canonical_unversioned"
    assert dry_payload["applied"] is False
    assert path.read_bytes() == before

    assert main(["adopt", "--database-url", _url(path), "--apply"]) == 0
    applied_payload = json.loads(capsys.readouterr().out)
    assert applied_payload["state"] == "versioned"
    assert applied_payload["applied"] is True
