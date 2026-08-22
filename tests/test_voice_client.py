"""Client voice pack: GET /api/voice, serve-time index substitution, coats.

The client copy lives in app/data/voice.py and reaches the browser over two
channels (woolroom Phase 0, client half): the /api/voice JSON the boot path
fetches, and the {{VOICE_*}} placeholders str.replace'd into index.html at
serve time. These tests pin both channels plus the auth shape: the payload is
static copy, so it is guest-readable and never flag-gated — while the site
gate itself keeps working exactly as before.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

GUEST_COOKIE = "woolroom_guest_access"


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch,
    site_password: str = "",
    guest_enabled: bool = True,
) -> object:
    db_path = tmp_path / "woolroom-voice-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true" if guest_enabled else "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main


def test_voice_endpoint_serves_client_voice_map(tmp_path: Path, monkeypatch) -> None:
    main = _load_app(tmp_path, monkeypatch)
    voice_mod = importlib.import_module("app.data.voice")

    with TestClient(main.create_app()) as client:
        resp = client.get("/api/voice")
        assert resp.status_code == 200
        assert resp.json() == voice_mod.CLIENT_VOICE


def test_voice_endpoint_cache_policy_mirrors_statics(tmp_path: Path, monkeypatch) -> None:
    main = _load_app(tmp_path, monkeypatch)

    with TestClient(main.create_app()) as client:
        bare = client.get("/api/voice")
        assert bare.headers["Cache-Control"] == "public, max-age=0, must-revalidate"
        versioned = client.get(f"/api/voice?v={main.APP_VERSION}")
        assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        stale = client.get("/api/voice?v=not-the-version")
        assert stale.headers["Cache-Control"] == "public, max-age=0, must-revalidate"


def test_voice_endpoint_not_flag_gated(tmp_path: Path, monkeypatch) -> None:
    """Unlike /api/guest-access (404 with the flag off), the copy pack is
    always served — it is static content, not a feature."""
    main = _load_app(tmp_path, monkeypatch, guest_enabled=False)

    with TestClient(main.create_app()) as client:
        assert client.post("/api/guest-access").status_code == 404
        assert client.get("/api/voice").status_code == 200


def test_voice_endpoint_guest_readable_behind_site_gate(tmp_path: Path, monkeypatch) -> None:
    """Guests boot the room read-only and render the same wool copy, so the
    pack sits on the guest allowlist next to /api/me."""
    main = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(main.create_app()) as client:
        denied = client.get("/api/voice")
        assert denied.status_code == 401  # no cookie: the outer gate still holds
        granted = client.post("/api/guest-access")
        assert granted.status_code == 200
        assert client.cookies.get(GUEST_COOKIE)
        assert client.get("/api/voice").status_code == 200


def test_access_gate_still_serves_logged_out_visitors(tmp_path: Path, monkeypatch) -> None:
    """The landing copy is substituted serve-side, so a logged-out visitor
    needs no fetch: / redirects to /access and the access page renders."""
    main = _load_app(tmp_path, monkeypatch, site_password="den-word")

    with TestClient(main.create_app()) as client:
        bounced = client.get("/", follow_redirects=False)
        assert bounced.status_code == 303
        assert bounced.headers["location"].startswith("/access")
        access = client.get("/access")
        assert access.status_code == 200
        assert "{{VOICE_" not in access.text


def test_index_has_no_unsubstituted_voice_placeholders(tmp_path: Path, monkeypatch) -> None:
    main = _load_app(tmp_path, monkeypatch)

    with TestClient(main.create_app()) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "{{VOICE_" not in resp.text
        # Spot-check both surfaces of the shared tagline and a deep one.
        assert resp.text.count("a quiet room, shared.") >= 2
        assert "someone small moved in next door." in resp.text
        assert "bring a second cat home" in resp.text


def test_voice_coats_match_species_registry(tmp_path: Path, monkeypatch) -> None:
    main = _load_app(tmp_path, monkeypatch)
    species_mod = importlib.import_module("app.data.species")

    with TestClient(main.create_app()) as client:
        payload = client.get("/api/voice").json()
        for species in ("cat",):
            assert payload["coats"][species] == list(species_mod.coats_for(species))
        # Every served coat id has a label — the swatch's aria-label can
        # never fall back to a raw underscored id.
        for ids in payload["coats"].values():
            assert all(cid in payload["coat_labels"] for cid in ids)
