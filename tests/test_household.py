"""Two-pet household flows: co-adoption ceremony, room switching, playdates.

The household = one founding cat + a second cat, two humans confirmed on both.
The second cat arrives half-decided: the adopter picks name + first quirk, the
partner picks the second quirk (that pick IS their adoption confirmation).

Voice-distinctness between species is a pack concern now that the cat is the
only builtin species — it is pinned by tests/test_packs.py and
tests/test_pack_pebble.py against the pebble overlay, not here.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.data.species import SPECIES_REGISTRY


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(tmp_path: Path, monkeypatch) -> object:
    db_path = tmp_path / "woolroom-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", "")
    monkeypatch.setenv("ADOPT_ALLOWLIST", "")
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.setenv("OPEN_SIGNUP", "true")
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


def _join_via_invite(client: TestClient, invite_url: str, display_name: str) -> None:
    invite_path = urlparse(invite_url).path
    joined = client.get(invite_path, follow_redirects=False)
    assert joined.status_code == 303
    started = client.post("/api/start", json={"display_name": display_name})
    assert started.status_code == 200


def _household(owner: TestClient, partner: TestClient) -> dict:
    """Two humans confirmed on the founding cat. Returns the cat's dict."""
    cat = _start_and_adopt(owner, "Ash", "Purl")
    invite = owner.post("/api/invite")
    assert invite.status_code == 200
    _join_via_invite(partner, invite.json()["url"], "Wren")
    return cat


def _adopt_second_cat(owner: TestClient, name: str = "Socks") -> dict:
    res = owner.post(
        "/api/adopt-second",
        json={"name": name, "quirk": "content_sigher", "coat": "ash"},
    )
    assert res.status_code == 200
    return res.json()["pet"]


def _confirm_second(partner: TestClient, pet_id: str, quirk: str = "zoomie_initiator") -> None:
    res = partner.post("/api/second-quirk", json={"pet_id": pet_id, "quirk": quirk})
    assert res.status_code == 200


def test_adopt_second_builds_pending_household(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        cat = _household(owner, partner)
        second = _adopt_second_cat(owner)

        assert second["species"] == "cat"
        assert second["name"] == "Socks"
        # The founding cat's room now sees its sibling next door.
        owner_me = owner.get("/api/me").json()
        assert [p["name"] for p in owner_me["pets"]] == ["Purl", "Socks"]
        assert owner_me["pets"][0]["pending"] is False
        assert owner_me["pets"][1]["pending"] is False  # adopter is confirmed
        assert owner_me["active_pet_id"] == cat["id"]  # didn't yank the room
        # The partner sees the second cat as pending — the card, not the room.
        partner_me = partner.get("/api/me").json()
        assert partner_me["pets"][1]["pending"] is True
        assert partner_me["active_pet_id"] == cat["id"]
        assert partner_me["pet"]["sibling"]["name"] == "Socks"
        assert partner_me["pet"]["sibling"]["species"] == "cat"


def test_second_quirk_completes_the_ceremony(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)

        confirmed = partner.post(
            "/api/second-quirk",
            json={"pet_id": second["id"], "quirk": "zoomie_initiator"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["pet"]["quirks"] == ["content_sigher", "zoomie_initiator"]

        partner_me = partner.get("/api/me").json()
        assert partner_me["pets"][1]["pending"] is False
        assert partner_me["pet"]["household_names"] == ["Ash", "Wren"]


def test_second_quirk_rejects_wrong_user_and_duplicates(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)

        # The adopter is already confirmed — the ceremony is not theirs.
        again = owner.post(
            "/api/second-quirk",
            json={"pet_id": second["id"], "quirk": "side_eye_judge"},
        )
        assert again.status_code == 409
        # Unknown quirk.
        bogus = partner.post(
            "/api/second-quirk", json={"pet_id": second["id"], "quirk": "flies"}
        )
        assert bogus.status_code == 400
        # Duplicate of the adopter's first pick.
        dupe = partner.post(
            "/api/second-quirk",
            json={"pet_id": second["id"], "quirk": "content_sigher"},
        )
        assert dupe.status_code == 409


def test_pending_partner_cannot_act_or_switch_into_the_second_room(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)

        acted = partner.post(f"/api/action?pet={second['id']}", json={"type": "greet"})
        assert acted.status_code == 403
        switched = partner.post("/api/room", json={"pet_id": second["id"]})
        assert switched.status_code == 403
        # …but the founding cat's room still answers normally.
        ok = partner.post(f"/api/action?pet={partner.get('/api/me').json()['active_pet_id']}",
                          json={"type": "greet"})
        assert ok.status_code == 200


def test_room_switch_lands_on_last_room_left(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        cat = _household(owner, partner)
        second = _adopt_second_cat(owner)
        _confirm_second(partner, second["id"])

        switched = partner.post("/api/room", json={"pet_id": second["id"]})
        assert switched.status_code == 200
        assert switched.json()["pet"]["id"] == second["id"]
        assert switched.json()["pet"]["species"] == "cat"

        partner_me = partner.get("/api/me").json()
        assert partner_me["active_pet_id"] == second["id"]
        assert partner_me["pet"]["id"] == second["id"]

        # Back to the founding cat — the pointer follows.
        partner.post("/api/room", json={"pet_id": cat["id"]})
        assert partner.get("/api/me").json()["active_pet_id"] == cat["id"]


def test_visit_playdate_round_trip(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        cat = _household(owner, partner)
        second = _adopt_second_cat(owner)
        _confirm_second(partner, second["id"])

        # The founding cat follows the human into the second cat's room.
        started = owner.post("/api/visit", json={"pet_id": cat["id"]})
        assert started.status_code == 200
        assert started.json()["visit"]["role"] == "host"

        second_room = owner.post("/api/room", json={"pet_id": second["id"]}).json()["pet"]
        assert second_room["visit"]["role"] == "host"
        assert second_room["visit"]["visitor"]["name"] == "Purl"
        assert second_room["visit"]["visitor"]["species"] == "cat"

        # The founding cat's own room reads as away-while-visiting.
        cat_me = owner.post("/api/room", json={"pet_id": cat["id"]}).json()["pet"]
        assert cat_me["visit"]["role"] == "away"
        assert cat_me["visit"]["host_name"] == "Socks"

        # The first visit became a kept moment on the visitor's timeline.
        memory = owner.get(f"/api/memory?pet={cat['id']}").json()
        assert any(m["event_type"] == "visit" for m in memory["moments"])
        # Timestamps the frontend parses carry the Z — a bare isoformat()
        # reads as local time in the browser and misgroups fresh moments.
        assert memory["adopted_at"].endswith("Z")
        assert all(m["created_at"].endswith("Z") for m in memory["moments"])

        ended = partner.post("/api/visit/end", json={"pet_id": second["id"]})
        assert ended.status_code == 200
        assert ended.json()["ended"] is True
        after = owner.post("/api/room", json={"pet_id": second["id"]}).json()["pet"]
        assert after["visit"] is None


def test_visit_requires_a_sibling_room(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        cat = _household(owner, partner)
        res = owner.post("/api/visit", json={"pet_id": cat["id"]})
        assert res.status_code == 409


def test_action_targets_the_room_in_the_query_param(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)
        _confirm_second(partner, second["id"])

        res = partner.post(f"/api/action?pet={second['id']}", json={"type": "feed"})
        assert res.status_code == 200
        assert res.json()["pet"]["id"] == second["id"]
        # Feeding the second cat does not touch the founding cat's bowl.
        me = partner.get("/api/me").json()
        purl = next(p for p in me["pets"] if p["name"] == "Purl")
        assert purl["hungry"] is True


def test_ws_room_scoped_initial_state(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)
        _confirm_second(partner, second["id"])

        with partner.websocket_connect(f"/ws?pet={second['id']}") as ws:
            event = ws.receive_json()
        assert event["type"] == "pet_state"
        assert event["pet"]["id"] == second["id"]
        assert event["pet"]["species"] == "cat"


def test_pet_payload_serves_registry_pronoun(tmp_path: Path, monkeypatch) -> None:
    """Both rooms' pet_state payloads carry the registry pronoun (additive)."""
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner:
        _household(owner, partner)
        second = _adopt_second_cat(owner)
        _confirm_second(partner, second["id"])

        first_room = owner.get("/api/me").json()["pet"]
        assert first_room["species"] == "cat"
        assert first_room["pronoun"] == SPECIES_REGISTRY["cat"]["pronoun"]

        second_room = owner.post("/api/room", json={"pet_id": second["id"]}).json()["pet"]
        assert second_room["species"] == "cat"
        assert second_room["pronoun"] == SPECIES_REGISTRY["cat"]["pronoun"]


def test_household_caps_hold_at_default_config(tmp_path: Path, monkeypatch) -> None:
    """Defaults preserve the pair-shaped caps: no third human, no third room,
    and adoption still wants exactly QUIRK_PICK_COUNT (2) quirks."""
    app = _load_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as partner, TestClient(app) as late:
        _household(owner, partner)

        # Two humans confirmed on the founding cat — the invite tap is closed.
        invite = owner.post("/api/invite")
        assert invite.status_code == 409
        assert invite.json()["detail"] == "pet already has two humans"

        # Two rooms in the household — a third is refused.
        _adopt_second_cat(owner)
        third = owner.post(
            "/api/adopt-second",
            json={"name": "Biscuit", "quirk": "content_sigher", "coat": "tuxedo"},
        )
        assert third.status_code == 409
        assert third.json()["detail"] == "two rooms is plenty for now"

        # Adoption enforces the pick count at the schema boundary: one quirk
        # 422s, two sail through.
        started = late.post("/api/start", json={"display_name": "Cleo"})
        assert started.status_code == 200
        one_quirk = late.post(
            "/api/adopt", json={"name": "Solo", "quirks": ["content_sigher"]}
        )
        assert one_quirk.status_code == 422
        two_quirks = late.post(
            "/api/adopt",
            json={"name": "Solo", "quirks": ["content_sigher", "lean_in_greeter"]},
        )
        assert two_quirks.status_code == 200
