from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
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


def _assigned_strings(path: Path, target_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == target_name
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]


def test_public_distribution_api_is_exposed() -> None:
    public_api = {
        "AdoptionDefaults",
        "DEFAULT_AUTH_NAMESPACE",
        "DEFAULT_SITE_IDENTITY",
        "PLUGIN_API_VERSION",
        "AuthNamespace",
        "BoundPetCard",
        "CatalogOverlayError",
        "CatalogOverlayProvider",
        "EmptyCatalogOverlayProvider",
        "GuestCardSubject",
        "OwnerCardSubject",
        "SiteIdentity",
        "__version__",
        "create_app",
        "migration_path",
        "upgrade_sqlite_database",
    }

    assert public_api <= set(woolroom.__all__)
    assert all(hasattr(woolroom, name) for name in public_api)
    assert isinstance(woolroom.__version__, str) and woolroom.__version__
    assert isinstance(woolroom.PLUGIN_API_VERSION, int)
    assert woolroom.PLUGIN_API_VERSION == 2
    assert callable(woolroom.create_app)
    assert callable(woolroom.migration_path)
    assert callable(woolroom.upgrade_sqlite_database)
    assert get_type_hints(woolroom.create_app)["return"].__name__ == "FastAPI"
    assert get_type_hints(woolroom.upgrade_sqlite_database) == {
        "path": str | Path,
        "return": woolroom.DatabaseInspection,
    }

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
    identity = woolroom.SiteIdentity(name="hosted woolroom")
    assert woolroom.create_app(site_identity=identity).state.site_identity is identity


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
    assert _assigned_strings(REPO_ROOT / "woolroom" / "__init__.py", "__version__") == [
        project_version
    ]
    assert _assigned_strings(
        REPO_ROOT / "packages" / "woolpack" / "src" / "woolpack" / "__init__.py",
        "__version__",
    ) == [pack_version]

    cli_path = REPO_ROOT / "packages" / "woolpack" / "src" / "woolpack" / "cli.py"
    cli_tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    package_version_function = next(
        node
        for node in cli_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_package_version"
    )
    assert [
        node.value.value
        for node in ast.walk(package_version_function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ] == [pack_version]

    issue_template = (
        REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "pack_submission.md"
    ).read_text(encoding="utf-8")
    assert re.findall(r"woolpack==[0-9A-Za-z_.+-]+", issue_template) == [
        f"woolpack=={pack_version}",
        f"woolpack=={pack_version}",
    ]


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
    profile_dir = files("app.packs").joinpath("profiles")
    package_dir = files("woolroom")

    assert package_dir.joinpath("py.typed").is_file()
    assert static_dir.is_dir()
    assert static_dir.joinpath("index.html").is_file()
    assert static_dir.joinpath("app.js").is_file()
    assert static_dir.joinpath("style.css").is_file()
    for species in ("dog", "pig"):
        pack_dir = profile_dir.joinpath(species)
        assert pack_dir.joinpath("pack.yaml").is_file()
        assert pack_dir.joinpath("phrases", f"{species}.yaml").is_file()
        assert pack_dir.joinpath("species", f"{species}.svg").is_file()
