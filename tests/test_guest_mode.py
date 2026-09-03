"""Read-only guest mode: grant, gate, sanitized scene, WS fanout invariants."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]

GUEST_COOKIE = "woolroom_guest_access"
SITE_ACCESS_COOKIE = "woolroom_site_access"
SESSION_COOKIE = "woolroom_session"

GUEST_SCENE_KEYS = {
    "id",
    "name",
    "species",
    "pronoun",
    "quirks",
    "coat",
    "pose_detail",
    "animation_state",
    "mood_arousal",
    "mood_valence",
    "adopted_at",
    "life_stage",
    "pet_age_years",
    "render_scale",
    "stage_proportions",
    "fed_minutes_ago",
    "hungry",
    "scene_fx",
    "scene_events",
    "app_version",
}


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch,
    site_password: str = "",
    guest_enabled: bool = True,
    guest_pet_id: str = "",
) -> object:
    db_path = tmp_path / "woolroom-guest-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("OPEN_SIGNUP", "true")
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true" if guest_enabled else "false")
    monkeypatch.setenv("GUEST_PET_ID", guest_pet_id)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main.create_app()


def _start_and_adopt(client: TestClient, display_name: str, pet_name: str) -> dict:
    start = client.post("/api/start", json={"display_name": display_name})
    assert start.status_code == 200
    adopt = client.post(
        "/api/adopt",
        json={"name": pet_name, "quirks": ["content_sigher", "lean_in_greeter"]},
    )
    assert adopt.status_code == 200
    return adopt.json()["pet"]


def _grant_guest(client: TestClient) -> None:
    resp = client.post("/api/guest-access")
    assert resp.status_code == 200
    assert client.cookies.get(GUEST_COOKIE)


def _grant_site(client: TestClient, password: str = "den-word") -> None:
    resp = client.post("/api/site-access", json={"password": password})
    assert resp.status_code == 200


def test_guest_grant_issues_cookie_and_no_session(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/api/guest-access")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "guest": True}
        assert client.cookies.get(GUEST_COOKIE)
        # Guests never get a session cookie.
        assert client.cookies.get(SESSION_COOKIE) is None


def test_guest_grant_404_when_flag_off(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, guest_enabled=False)

    with TestClient(app) as client:
        resp = client.post("/api/guest-access")
        assert resp.status_code == 404
        assert client.cookies.get(GUEST_COOKIE) is None
        assert client.get("/api/guest/scene").status_code == 401


def test_guest_cookie_passes_site_gate_as_guest(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as client:
        # No cookie at all: the outer gate bounces browsers to /access.
        bounced = client.get("/", follow_redirects=False)
        assert bounced.status_code == 303
        assert bounced.headers["location"].startswith("/access")

        _grant_guest(client)

        # The guest cookie opens the outer gate...
        assert client.get("/").status_code == 200
        # ...and /api/me reports guest status so the boot path can fall back.
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["user"] is None
        assert me.json()["guest"] is True


def test_owner_access_replaces_guest_cookie(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as client:
        _grant_guest(client)

        response = client.post("/api/site-access", json={"password": "den-word"})
        assert response.status_code == 200
        assert client.cookies.get(SITE_ACCESS_COOKIE)
        assert client.cookies.get(GUEST_COOKIE) is None

        set_cookies = response.headers.get_list("set-cookie")
        assert len(set_cookies) == 2
        guest_clear = next(value for value in set_cookies if value.startswith(f"{GUEST_COOKIE}="))
        assert "Max-Age=0" in guest_clear
        assert "Path=/" in guest_clear


def test_failed_owner_access_preserves_guest_cookie(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as client:
        _grant_guest(client)
        guest_cookie = client.cookies.get(GUEST_COOKIE)

        response = client.post("/api/site-access", json={"password": "wrong"})
        assert response.status_code == 401
        assert client.cookies.get(GUEST_COOKIE) == guest_cookie
        assert client.cookies.get(SITE_ACCESS_COOKIE) is None
        assert response.headers.get_list("set-cookie") == []


def test_authed_user_reports_guest_false(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["user"]["display_name"] == "Ash"
        assert me.json()["guest"] is False


def test_guest_scene_matches_explicit_allowlist(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as owner, TestClient(app) as guest:
        _grant_site(owner)
        pet = _start_and_adopt(owner, "Ash", "Purl")
        # Seed the private surfaces: household names, a whisper, a trace.
        acted = owner.post("/api/action", json={"type": "message", "text": "miss you"})
        assert acted.status_code == 200

        # Without the guest cookie the endpoint refuses.
        assert guest.get("/api/guest/scene").status_code == 401

        _grant_guest(guest)
        resp = guest.get("/api/guest/scene")
        assert resp.status_code == 200
        body = resp.json()
        assert body["guest"] is True
        payload = body["pet"]
        assert payload["id"] == pet["id"]
        assert set(payload) == GUEST_SCENE_KEYS
        # Belt-and-braces: no household name or whisper text anywhere in the
        # serialized payload.
        blob = json.dumps(payload)
        assert "Ash" not in blob
        assert "miss you" not in blob


def test_guest_write_endpoints_still_401(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as owner, TestClient(app) as guest:
        _grant_site(owner)
        _start_and_adopt(owner, "Ash", "Purl")
        _grant_guest(guest)

        assert guest.post("/api/action", json={"type": "greet"}).status_code == 401
        assert guest.post("/api/invite").status_code == 401
        assert guest.get("/api/memory").status_code == 401
        assert guest.get("/api/unseen-notes").status_code == 401
        assert guest.put("/api/coat", json={"coat": "ash"}).status_code == 401
        assert guest.put("/api/aliases", json={"partner_aliases": {}}).status_code == 401


def test_guest_ws_gets_sanitized_initial_and_broadcast_state(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")
        session_cookie = client.cookies.get(SESSION_COOKIE)
        assert session_cookie

        # Rebuild the jar as a pure guest: no session cookie, guest cookie only.
        client.cookies.clear()
        _grant_guest(client)
        assert client.cookies.get(SESSION_COOKIE) is None

        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            assert initial["type"] == "pet_state"
            assert initial["pet"]["id"] == pet["id"]
            assert set(initial["pet"]) == GUEST_SCENE_KEYS

            # The owner acts; the guest socket must receive exactly the
            # sanitized pet_state frame (the response frame is asserted absent
            # at the channel level in the next test).
            client.cookies.set(SESSION_COOKIE, session_cookie)
            acted = client.post("/api/action", json={"type": "greet"})
            assert acted.status_code == 200

            frame = ws.receive_json()
            assert frame["type"] == "pet_state"
            assert set(frame["pet"]) == GUEST_SCENE_KEYS
            assert frame["pet"]["name"] == "Purl"


def test_ws_rejects_visitor_without_any_cookie(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(app) as client:
        _grant_site(client)
        _start_and_adopt(client, "Ash", "Purl")
        client.cookies.clear()
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_channel_guest_bucket_only_gets_sanitized_pet_state(
    tmp_path: Path, monkeypatch
) -> None:
    _load_app(tmp_path, monkeypatch)
    from app.channels.webapp import channel

    class _FakeWs:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

    guest_ws = _FakeWs()
    assert await channel.register_guest("pet-1", guest_ws)
    # Guests are not presence: the bucket never moves online_count.
    assert channel.online_count("pet-1") == 0
    assert channel.guest_count("pet-1") == 1

    # response / milestone / outing / presence frames never reach guests.
    for frame_type in ("response", "milestone", "outing", "presence"):
        await channel.broadcast("pet-1", {"type": frame_type, "text": "secret"})
    assert guest_ws.sent == []

    # pet_state fans out sanitized.
    full_payload = {
        "id": "pet-1",
        "name": "Purl",
        "household_names": ["Ash", "Wren"],
        "room_notes": [{"line": "miss you"}],
        "online_count": 2,
        "scene_fx": None,
        "future_private": {"secret": "new field"},
    }
    await channel.broadcast("pet-1", {"type": "pet_state", "pet": full_payload})
    assert len(guest_ws.sent) == 1
    sent = guest_ws.sent[0]
    assert sent["type"] == "pet_state"
    assert sent["pet"]["name"] == "Purl"
    assert sent["pet"]["scene_fx"] is None
    assert set(sent["pet"]) == {"id", "name", "scene_fx"}

    await channel.unregister_guest("pet-1", guest_ws)
    assert channel.guest_count("pet-1") == 0


# ────────── demo-pet pinning (GUEST_PET_ID) ──────────


def test_guest_pet_id_pins_resolution_and_never_falls_back(
    tmp_path: Path, monkeypatch
) -> None:
    """Pinned id resolves ONLY that pet; a bad pin 404s instead of falling
    back to the first pet (which could be a real household's pet)."""
    app = _load_app(tmp_path, monkeypatch)
    from app.config import settings

    with TestClient(app) as owner_a, TestClient(app) as owner_b, TestClient(app) as guest:
        pet_a = _start_and_adopt(owner_a, "Ash", "Purl")       # first pet
        pet_b = _start_and_adopt(owner_b, "Wren", "biscuit")   # the "demo" pet
        _grant_guest(guest)

        # Unpinned: first pet (dev convenience).
        assert guest.get("/api/guest/scene").json()["pet"]["id"] == pet_a["id"]

        # Pinned to B: B resolves even though A exists and is first.
        monkeypatch.setattr(settings, "guest_pet_id", pet_b["id"])
        scene = guest.get("/api/guest/scene")
        assert scene.status_code == 200
        assert scene.json()["pet"]["id"] == pet_b["id"]
        assert scene.json()["pet"]["name"] == "biscuit"

        # Pinned to a nonexistent id: clean 404, NO fallback to pet A — the
        # privacy boundary is the point of the pin.
        monkeypatch.setattr(settings, "guest_pet_id", "does-not-exist")
        missing = guest.get("/api/guest/scene")
        assert missing.status_code == 404
        assert "demo pet" in missing.json()["detail"]


def test_guest_scene_404_when_no_pets_at_all(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as guest:
        _grant_guest(guest)
        resp = guest.get("/api/guest/scene")
        assert resp.status_code == 404
        assert "demo pet" in resp.json()["detail"]


def test_seed_demo_pet_idempotent(tmp_path: Path) -> None:
    """Second run creates nothing and prints the same id; exactly one pet row,
    and it has no participants."""
    db = tmp_path / "seed-test.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "SECRET_KEY": "seed-test-secret",
        "ENV": "dev",
    }
    env.pop("ANTHROPIC_API_KEY", None)
    script = REPO_ROOT / "scripts" / "seed_demo_pet.py"

    first = subprocess.run(
        [sys.executable, str(script)],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert first.returncode == 0, first.stderr
    assert "created demo pet" in first.stdout
    pet_id = first.stdout.strip().splitlines()[-1]
    assert pet_id

    second = subprocess.run(
        [sys.executable, str(script)],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert second.returncode == 0, second.stderr
    assert "already exists" in second.stdout
    assert "nothing changed" in second.stdout
    assert second.stdout.strip().splitlines()[-1] == pet_id

    with sqlite3.connect(db) as conn:
        (pet_count,) = conn.execute("SELECT COUNT(*) FROM pets").fetchone()
        (participant_count,) = conn.execute(
            "SELECT COUNT(*) FROM pet_participants"
        ).fetchone()
        (name, coat, quirks) = conn.execute(
            "SELECT name, coat, quirks FROM pets"
        ).fetchone()
    assert pet_count == 1
    assert participant_count == 0  # the demo pet is nobody's pet
    assert name == "biscuit"
    assert coat == "marmalade"
    assert json.loads(quirks) == ["lean_in_greeter", "content_sigher"]
