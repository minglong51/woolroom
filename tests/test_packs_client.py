"""Pack-species figure delivery: GET /api/packs + the boot render contract.

woolroom Phase 1b: the pack loader's `PACK_ASSETS` reach the client over
GET /api/packs — fetched at boot next to /api/voice, resolved by figures.js
for any species the builtin cat tables don't carry. These tests pin the
endpoint (empty with no packs, the pebble fixture's sanitized assets, the
statics cache policy, the guest allowlist — the same static-content
treatment as /api/voice) and the boot-level render contract: a pebble pet's
/api/me payload plus the packs payload give the client everything
figures.js needs to draw it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

FIXTURE_PACK = Path(__file__).parent / "fixtures" / "packs" / "pebble"

GUEST_COOKIE = "woolroom_guest_access"

PEBBLE_PALETTES = {"gray": {"body": "#9a9a94", "belly": "#cfcfc8", "point": "#77776f"}}
PEBBLE_GEOMETRY = {
    "earBelow": 400,
    "headBelow": 408,
    "tail": {"yAbove": 444, "xAbove": 238},
    "belly": {"yAbove": 416, "xAbove": 180, "xBelow": 220},
}


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _boot_app(
    tmp_path: Path,
    monkeypatch,
    pack_paths_env: str | None = None,
    site_password: str = "",
    guest_enabled: bool = True,
) -> object:
    """Fresh app modules + lifespan, mirroring test_packs._boot_app with the
    site-gate knobs of test_voice_client._load_app."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/woolroom-packs-test.db")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true" if guest_enabled else "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if pack_paths_env is None:
        monkeypatch.delenv("PACK_PATHS", raising=False)
    else:
        monkeypatch.setenv("PACK_PATHS", pack_paths_env)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main


# ────────── the endpoint ──────────


def test_packs_endpoint_empty_with_no_packs(tmp_path: Path, monkeypatch) -> None:
    main = _boot_app(tmp_path, monkeypatch)

    with TestClient(main.create_app()) as client:
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        assert resp.json() == {}


def test_packs_endpoint_serves_pack_species_assets(tmp_path: Path, monkeypatch) -> None:
    main = _boot_app(tmp_path, monkeypatch, str(FIXTURE_PACK))

    with TestClient(main.create_app()) as client:
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        packs = resp.json()
        # The builtin cat is NOT served — the client carries it already.
        assert set(packs) == {"pebble"}
        entry = packs["pebble"]
        assert set(entry) == {"svg", "palettes", "geometry", "pronoun"}
        assert entry["pronoun"] == "it"
        assert entry["palettes"] == PEBBLE_PALETTES
        assert entry["geometry"] == PEBBLE_GEOMETRY
        # The figure is the sanitizer's output, not the raw file: the wool
        # rig's class contract and singleton eye id survive, verbatim.
        loader = importlib.import_module("app.packs.loader")
        assert entry["svg"] == loader.PACK_ASSETS["pebble"]["figure"]
        assert entry["svg"].startswith("<g>")
        assert 'class="tailg"' in entry["svg"]
        assert 'id="dog-eyes"' in entry["svg"]


def test_packs_endpoint_cache_policy_mirrors_statics(tmp_path: Path, monkeypatch) -> None:
    main = _boot_app(tmp_path, monkeypatch, str(FIXTURE_PACK))

    with TestClient(main.create_app()) as client:
        bare = client.get("/api/packs")
        assert bare.headers["Cache-Control"] == "public, max-age=0, must-revalidate"
        versioned = client.get(f"/api/packs?v={main.APP_VERSION}")
        assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        stale = client.get("/api/packs?v=not-the-version")
        assert stale.headers["Cache-Control"] == "public, max-age=0, must-revalidate"


def test_packs_endpoint_guest_readable_behind_site_gate(tmp_path: Path, monkeypatch) -> None:
    """Guests boot the room read-only and must still render pack species, so
    the payload sits on the guest allowlist next to /api/voice."""
    main = _boot_app(tmp_path, monkeypatch, str(FIXTURE_PACK), site_password="den-word")

    with TestClient(main.create_app()) as client:
        denied = client.get("/api/packs")
        assert denied.status_code == 401  # no cookie: the outer gate still holds
        granted = client.post("/api/guest-access")
        assert granted.status_code == 200
        assert client.cookies.get(GUEST_COOKIE)
        assert client.get("/api/packs").status_code == 200


# ────────── boot render contract: pet_state + packs payload ──────────


async def test_boot_pebble_pet_renders_from_the_packs_payload(
    tmp_path: Path, monkeypatch
) -> None:
    """A pebble adopted while the pack is loaded: /api/me names the species,
    /api/packs carries everything figures.js resolves for it."""
    main = _boot_app(tmp_path, monkeypatch, str(FIXTURE_PACK))

    from app.storage import repo
    from app.storage.db import SessionLocal

    with TestClient(main.create_app()) as client:
        async with SessionLocal() as session:
            user = await repo.create_user(session, "Rock Watcher")
            pet = await repo.create_pet(
                session, "Rocky", quirks=[], coat="gray", species="pebble"
            )
            await repo.add_participant(session, pet.id, user.id)
            await session.commit()
            token = await repo.recovery_token_for(session, user.id)

        login = client.get(f"/r/{token}", follow_redirects=False)
        assert login.status_code == 303

        me = client.get("/api/me").json()
        assert me["pet"]["species"] == "pebble"
        assert me["pet"]["coat"] == "gray"

        packs = client.get("/api/packs").json()
        # The client-side render path, replayed: petFigureSvg resolves the
        # species in this.packs, paletteFor finds the pet's coat in the
        # species' palettes, touchZoneFor reads every zone key off geometry,
        # and the svg carries the rig's singleton eye id (deidentify's
        # visitor variant swaps it out by exact-match replace).
        entry = packs[me["pet"]["species"]]
        palette = entry["palettes"][me["pet"]["coat"]]
        assert set(palette) == {"body", "belly", "point"}
        assert set(entry["geometry"]) == {"earBelow", "headBelow", "tail", "belly"}
        assert 'id="dog-eyes"' in entry["svg"]
