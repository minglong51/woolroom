"""The /ws origin guard (CSWSH defense): a third-party page can open a
socket with the victim's cookie auto-attached, so a cross-site Origin must
close 1008 even when the session cookie is valid. Named security control,
previously untested — a refactor of the BASE_URL/Host fallback broke it
silently."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(tmp_path: Path, monkeypatch) -> object:
    db_path = tmp_path / "woolroom-ws-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", "")
    monkeypatch.setenv("OPEN_SIGNUP", "true")
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main.create_app()


def _start_and_adopt(client: TestClient) -> None:
    assert client.post("/api/start", json={"display_name": "Ash"}).status_code == 200
    assert client.post(
        "/api/adopt",
        json={"name": "Purl", "quirks": ["content_sigher", "lean_in_greeter"]},
    ).status_code == 200


def test_cross_origin_with_valid_session_closes_1008(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _start_and_adopt(client)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/ws", headers={"origin": "https://evil.example"}
            ) as ws:
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_matching_base_url_origin_is_accepted(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _start_and_adopt(client)
        with client.websocket_connect(
            "/ws", headers={"origin": "http://testserver"}
        ) as ws:
            assert ws.receive_json()["type"] == "pet_state"


def test_absent_origin_is_accepted(tmp_path: Path, monkeypatch) -> None:
    # curl/python clients omit Origin; the guard targets browsers, which
    # always send it.
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _start_and_adopt(client)
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "pet_state"
