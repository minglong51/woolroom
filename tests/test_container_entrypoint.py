from __future__ import annotations

import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "scripts" / "docker-entrypoint.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_entrypoint(
    tmp_path: Path,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess[str], tuple[str, ...]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace_path = tmp_path / "trace"
    _executable(bin_dir / "id", 'test "${1:-}" = "-u" && printf "1000\\n"')
    recorder = (
        'command_line=$(printf "%s" "$0 $*" | tr "\\n" " "); '
        'printf "%s|db=%s|path=%s|asgi=%s\\n" '
        '"$command_line" "$DATABASE_URL" "$WOOLROOM_DB_PATH" "$WOOLROOM_ASGI_APP" '
        '>> "$TRACE_PATH"'
    )
    _executable(
        bin_dir / "python",
        recorder
        + "\n"
        + 'case "$*" in *"import importlib"*) '
        + 'test "${FAIL_ASGI_PREFLIGHT:-}" != "1" ;; esac',
    )
    for command in ("uvicorn", "litestream"):
        _executable(bin_dir / command, recorder)

    environment = os.environ.copy()
    for name in (
        "BUCKET_NAME",
        "DATABASE_URL",
        "FAIL_ASGI_PREFLIGHT",
        "WOOLROOM_ASGI_APP",
        "WOOLROOM_DB_PATH",
    ):
        environment.pop(name, None)
    environment.update(overrides)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    environment["TRACE_PATH"] = str(trace_path)
    result = subprocess.run(
        [str(ENTRYPOINT)],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = tuple(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.exists() else ()
    return result, lines


def test_stock_container_uses_one_database_and_one_worker(tmp_path: Path) -> None:
    result, trace = _run_entrypoint(tmp_path)

    assert result.returncode == 0, result.stderr
    expected = (
        "db=sqlite+aiosqlite:////data/woolroom.db|"
        "path=/data/woolroom.db|asgi=app.main:app"
    )
    assert len(trace) == 4
    assert trace[0] == f"{tmp_path}/bin/python -c import app.config|{expected}"
    assert "import importlib" in trace[1]
    assert trace[1].endswith(f"|{expected}")
    assert trace[2] == f"{tmp_path}/bin/python scripts/migrate.py|{expected}"
    assert trace[3] == (
        f"{tmp_path}/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 "
        f"--workers 1 --proxy-headers --forwarded-allow-ips=*|{expected}"
    )


def test_deployment_templates_share_the_stock_database_default() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    fly = tomllib.loads((REPO_ROOT / "fly.toml").read_text(encoding="utf-8"))
    litestream = (REPO_ROOT / "litestream.yml").read_text(encoding="utf-8")

    assert "install -d -o app -g app /data" in dockerfile
    assert "litestream-v0.3.13-linux-${TARGETARCH}.deb" in dockerfile
    assert fly["env"]["WOOLROOM_DB_PATH"] == "/data/woolroom.db"
    assert "- path: ${WOOLROOM_DB_PATH}" in litestream


@pytest.mark.parametrize(
    "overrides",
    [
        {"WOOLROOM_DB_PATH": "/data/custom.db"},
        {"DATABASE_URL": "sqlite+aiosqlite:////data/custom.db"},
        {
            "WOOLROOM_DB_PATH": "/data/custom.db",
            "DATABASE_URL": "sqlite+aiosqlite:////data/custom.db",
        },
    ],
)
def test_custom_sqlite_path_drives_migration_and_runtime(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    result, trace = _run_entrypoint(tmp_path, **overrides)

    assert result.returncode == 0, result.stderr
    assert len(trace) == 4
    assert all("db=sqlite+aiosqlite:////data/custom.db" in line for line in trace)
    assert all("path=/data/custom.db" in line for line in trace)


def test_custom_asgi_target_keeps_single_worker(tmp_path: Path) -> None:
    result, trace = _run_entrypoint(
        tmp_path,
        WOOLROOM_ASGI_APP="deployment.app:application",
    )

    assert result.returncode == 0, result.stderr
    uvicorn = trace[-1]
    assert "uvicorn deployment.app:application" in uvicorn
    assert "--workers 1" in uvicorn


def test_litestream_uses_the_resolved_database_path(tmp_path: Path) -> None:
    result, trace = _run_entrypoint(
        tmp_path,
        BUCKET_NAME="synthetic-bucket",
        WOOLROOM_DB_PATH="/data/custom.db",
    )

    assert result.returncode == 0, result.stderr
    assert trace[2].startswith(f"{tmp_path}/bin/litestream restore ")
    assert trace[3].startswith(f"{tmp_path}/bin/python scripts/migrate.py")
    assert trace[4].startswith(f"{tmp_path}/bin/litestream replicate ")
    litestream = [line for line in trace if "/litestream " in line]
    assert "litestream restore -if-db-not-exists -if-replica-exists /data/custom.db" in litestream[0]
    assert "litestream replicate -exec uvicorn app.main:app" in litestream[1]
    assert all("path=/data/custom.db" in line for line in litestream)


def test_mismatched_database_settings_fail_before_commands(tmp_path: Path) -> None:
    result, trace = _run_entrypoint(
        tmp_path,
        WOOLROOM_DB_PATH="/data/custom.db",
        DATABASE_URL="sqlite+aiosqlite:////data/other.db",
    )

    assert result.returncode == 2
    assert trace == ()
    assert "must identify the same SQLite file" in result.stderr


def test_invalid_asgi_import_fails_before_restore_or_migration(tmp_path: Path) -> None:
    result, trace = _run_entrypoint(
        tmp_path,
        BUCKET_NAME="synthetic-bucket",
        FAIL_ASGI_PREFLIGHT="1",
        WOOLROOM_ASGI_APP="deployment.app:application",
    )

    assert result.returncode == 1
    assert len(trace) == 2
    assert "import app.config" in trace[0]
    assert "import importlib" in trace[1]
    assert not any("litestream" in line or "scripts/migrate.py" in line for line in trace)
    assert "could not be imported as a callable" in result.stderr


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("WOOLROOM_DB_PATH", "relative.db", "must be absolute"),
        ("WOOLROOM_DB_PATH", "/data/custom.db?mode=ro", "query or fragment"),
        ("DATABASE_URL", "postgresql://db/app", "file-backed sqlite+aiosqlite"),
        ("WOOLROOM_ASGI_APP", "deployment.app:app;bad", "one module:attribute"),
        ("WOOLROOM_ASGI_APP", "deployment.app", "one module:attribute"),
        ("WOOLROOM_ASGI_APP", "a:b:c", "one module:attribute"),
    ],
)
def test_unsafe_container_configuration_fails_closed(
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    result, trace = _run_entrypoint(tmp_path, **{name: value})

    assert result.returncode == 2
    assert trace == ()
    assert message in result.stderr
