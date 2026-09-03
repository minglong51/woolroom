from __future__ import annotations

import importlib
import importlib.metadata
import sqlite3
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import get_type_hints

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

import woolroom

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_public_distribution_api_is_exposed() -> None:
    public_api = {
        "DEFAULT_AUTH_NAMESPACE",
        "PLUGIN_API_VERSION",
        "AuthNamespace",
        "BoundPetCard",
        "CatalogOverlayError",
        "CatalogOverlayProvider",
        "EmptyCatalogOverlayProvider",
        "GuestCardSubject",
        "OwnerCardSubject",
        "__version__",
        "create_app",
        "migration_path",
    }

    assert public_api <= set(woolroom.__all__)
    assert all(hasattr(woolroom, name) for name in public_api)
    assert isinstance(woolroom.__version__, str) and woolroom.__version__
    assert isinstance(woolroom.PLUGIN_API_VERSION, int)
    assert woolroom.PLUGIN_API_VERSION == 1
    assert callable(woolroom.create_app)
    assert callable(woolroom.migration_path)
    assert get_type_hints(woolroom.create_app)["return"].__name__ == "FastAPI"

    namespace = woolroom.AuthNamespace(
        session_cookie="distribution_session",
        session_salt="distribution-session",
        site_access_cookie="distribution_site",
        site_access_salt="distribution-site",
        guest_access_cookie="distribution_guest",
        guest_access_salt="distribution-guest",
        pending_invite_cookie="distribution_pending",
    )
    assert woolroom.create_app(auth_namespace=namespace).state.auth_namespace is namespace


def test_workspace_distribution_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pack_pyproject = tomllib.loads(
        (REPO_ROOT / "packages/woolpack/pyproject.toml").read_text()
    )
    project_version = pyproject["project"]["version"]
    pack_version = pack_pyproject["project"]["version"]

    assert importlib.metadata.version("woolroom") == project_version == pack_version
    assert importlib.metadata.version("woolpack") == pack_version
    assert woolroom.__version__ == project_version


def test_packaged_migrations_have_one_head_and_upgrade_sqlite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    migration_dir = woolroom.migration_path()
    config = Config()
    config.set_main_option("script_location", str(migration_dir))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()

    assert migration_dir.is_dir()
    assert (migration_dir / "env.py").is_file()
    assert len(heads) == 1

    db_path = tmp_path / "distribution.db"
    settings = importlib.import_module("app.config").settings
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        applied_head = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        application_tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if name != "alembic_version" and not name.startswith("sqlite_")
        }

    assert applied_head == (heads[0],)
    assert application_tables


def test_source_static_assets_are_present() -> None:
    static_dir = files("app").joinpath("static")
    package_dir = files("woolroom")

    assert package_dir.joinpath("py.typed").is_file()
    assert static_dir.is_dir()
    assert static_dir.joinpath("index.html").is_file()
    assert static_dir.joinpath("app.js").is_file()
    assert static_dir.joinpath("style.css").is_file()
