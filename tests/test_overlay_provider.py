from __future__ import annotations

import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import aiosqlite
import pytest
from fastapi.testclient import TestClient
from woolpack import PetCardV1

from woolroom.overlay import BoundPetCard, GuestCardSubject, OwnerCardSubject

SENTINEL = "overlay_sentinel"


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


class _RecordingProvider:
    def __init__(
        self,
        *,
        owner_value: dict[str, object] | PetCardV1 | None = None,
        guest_value: dict[str, object] | PetCardV1 | None = None,
        owner_error: Exception | None = None,
        guest_error: Exception | None = None,
        startup_error: Exception | None = None,
        shutdown_error: Exception | None = None,
    ) -> None:
        self.owner_value = owner_value
        self.guest_value = guest_value
        self.owner_error = owner_error
        self.guest_error = guest_error
        self.startup_error = startup_error
        self.shutdown_error = shutdown_error
        self.owner_calls: list[OwnerCardSubject] = []
        self.guest_calls: list[GuestCardSubject] = []
        self.lifecycle: list[str] = []

    async def startup(self) -> None:
        self.lifecycle.append("startup")
        if self.startup_error is not None:
            raise self.startup_error

    async def shutdown(self) -> None:
        self.lifecycle.append("shutdown")
        if self.shutdown_error is not None:
            raise self.shutdown_error

    async def owner_card(
        self, subject: OwnerCardSubject
    ) -> BoundPetCard | None:
        self.owner_calls.append(subject)
        if self.owner_error is not None:
            raise self.owner_error
        if self.owner_value is None:
            return None
        return BoundPetCard(pet_id=subject.pet_id, card=self.owner_value)

    async def guest_card(
        self, subject: GuestCardSubject
    ) -> BoundPetCard | None:
        self.guest_calls.append(subject)
        if self.guest_error is not None:
            raise self.guest_error
        if self.guest_value is None:
            return None
        return BoundPetCard(pet_id=subject.pet_id, card=self.guest_value)


class _CoatCardProvider(_RecordingProvider):
    async def owner_card(self, subject: OwnerCardSubject) -> BoundPetCard:
        self.owner_calls.append(subject)
        return BoundPetCard(pet_id=subject.pet_id, card=_card(coat=subject.coat))


class _SubjectCardProvider(_RecordingProvider):
    async def owner_card(self, subject: OwnerCardSubject) -> BoundPetCard:
        self.owner_calls.append(subject)
        return BoundPetCard(
            pet_id=subject.pet_id,
            card=_card(
                species=subject.species,
                coat=subject.coat,
            ),
        )

    async def guest_card(self, subject: GuestCardSubject) -> BoundPetCard:
        self.guest_calls.append(subject)
        return BoundPetCard(
            pet_id=subject.pet_id,
            card=_card(
                species=subject.species,
                coat=subject.coat,
            ),
        )


class _SameDatabaseProvider(_CoatCardProvider):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path

    async def startup(self) -> None:
        self.lifecycle.append("startup")
        async with aiosqlite.connect(self.db_path) as connection:
            await connection.execute(
                "CREATE TABLE private_card_reads "
                "(pet_id TEXT NOT NULL, coat TEXT NOT NULL)"
            )
            await connection.commit()

    async def owner_card(self, subject: OwnerCardSubject) -> BoundPetCard:
        async with aiosqlite.connect(self.db_path) as connection:
            await connection.execute(
                "INSERT INTO private_card_reads (pet_id, coat) VALUES (?, ?)",
                (subject.pet_id, subject.coat),
            )
            await connection.commit()
        return await super().owner_card(subject)


class _WrongPetProvider(_RecordingProvider):
    async def guest_card(self, subject: GuestCardSubject) -> BoundPetCard:
        self.guest_calls.append(subject)
        return BoundPetCard(pet_id="another_pet", card=_card(coat=subject.coat))


def _card(
    *,
    card_id: str = SENTINEL,
    species: str = "cat",
    coat: str = "ash",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "card_id": card_id,
        "species": species,
        "coat": coat,
        "pronoun": "it",
        "svg": '<g><circle class="coat" r="4" /></g>',
        "palette": {"body": "#112233", "belly": "#aabbcc", "point": "#ddeeff"},
        "geometry": {
            "earBelow": 400,
            "headBelow": 408,
            "tail": {"yAbove": 444, "xAbove": 238},
            "belly": {"yAbove": 416, "xAbove": 180, "xBelow": 220},
        },
    }


def _load_app(
    tmp_path: Path,
    monkeypatch,
    provider: _RecordingProvider | None = None,
    *,
    site_password: str = "",
) -> object:
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/woolroom-overlay-test.db"
    )
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("OPEN_SIGNUP", "true")
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("GUEST_PET_ID", "")
    monkeypatch.setenv("ADOPT_ALLOWLIST", "")
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PACK_PATHS", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    if provider is None:
        return main.create_app()
    return main.create_app(overlay_provider=provider)


def _start_and_adopt(client: TestClient, *, coat: str = "ash") -> tuple[str, dict[str, object]]:
    started = client.post("/api/start", json={"display_name": "Reader"})
    assert started.status_code == 200
    adopted = client.post(
        "/api/adopt",
        json={
            "name": "Comet",
            "quirks": ["content_sigher", "lean_in_greeter"],
            "coat": coat,
        },
    )
    assert adopted.status_code == 200
    return started.json()["user_id"], adopted.json()["pet"]


def _grant_guest(client: TestClient) -> None:
    granted = client.post("/api/guest-access")
    assert granted.status_code == 200


def test_empty_provider_preserves_owner_and_guest_flows_with_null_cards(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        logged_out = client.get("/api/me")
        assert logged_out.status_code == 200
        assert logged_out.json()["card"] is None

        _, pet = _start_and_adopt(client)
        owner = client.get("/api/me")
        assert owner.status_code == 200
        assert owner.json()["pet"]["id"] == pet["id"]
        assert owner.json()["card"] is None

        client.cookies.clear()
        _grant_guest(client)
        guest_me = client.get("/api/me")
        assert guest_me.status_code == 200
        assert guest_me.json()["card"] is None
        scene = client.get("/api/guest/scene")
        assert scene.status_code == 200
        assert scene.json()["pet"]["id"] == pet["id"]
        assert scene.json()["card"] is None


def test_owner_card_uses_only_the_authenticated_active_pet_subject(
    tmp_path: Path, monkeypatch
) -> None:
    card = _card()
    provider = _RecordingProvider(owner_value=card)
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        assert client.get("/api/me").json()["card"] is None
        assert provider.owner_calls == []

        started = client.post("/api/start", json={"display_name": "Reader"})
        assert started.status_code == 200
        assert client.get("/api/me").json()["card"] is None
        assert provider.owner_calls == []

        adopted = client.post(
            "/api/adopt",
            json={
                "name": "Comet",
                "quirks": ["content_sigher", "lean_in_greeter"],
                "coat": "ash",
            },
        )
        assert adopted.status_code == 200
        pet = adopted.json()["pet"]
        assert provider.owner_calls == []

        response = client.get("/api/me")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.json()["card"] == card
        assert provider.owner_calls == [
            OwnerCardSubject(
                user_id=started.json()["user_id"],
                pet_id=pet["id"],
                species="cat",
                coat="ash",
            )
        ]
        assert [field.name for field in fields(provider.owner_calls[0])] == [
            "user_id",
            "pet_id",
            "species",
            "coat",
        ]

        assert SENTINEL not in json.dumps(client.get("/api/voice").json())
        assert SENTINEL not in json.dumps(client.get("/api/packs").json())
        assert len(provider.owner_calls) == 1


def test_guest_card_uses_only_the_cookie_authorized_resolved_pet_subject(
    tmp_path: Path, monkeypatch
) -> None:
    card = _card(coat="tuxedo")
    provider = _RecordingProvider(guest_value=card)
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _, pet = _start_and_adopt(client, coat="tuxedo")
        assert provider.guest_calls == []

        client.cookies.clear()
        denied = client.get("/api/guest/scene")
        assert denied.status_code == 401
        assert provider.guest_calls == []

        _grant_guest(client)
        guest_me = client.get("/api/me")
        assert guest_me.status_code == 200
        assert guest_me.headers["Cache-Control"] == "private, no-store"
        assert guest_me.json()["card"] is None
        assert provider.guest_calls == []

        response = client.get("/api/guest/scene")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.json()["card"] == card
        assert provider.guest_calls == [
            GuestCardSubject(
                pet_id=pet["id"],
                species="cat",
                coat="tuxedo",
            )
        ]
        assert [field.name for field in fields(provider.guest_calls[0])] == [
            "pet_id",
            "species",
            "coat",
        ]

        assert SENTINEL not in json.dumps(client.get("/api/voice").json())
        assert SENTINEL not in json.dumps(client.get("/api/packs").json())
        assert len(provider.guest_calls) == 1


def test_guest_provider_is_not_called_without_a_resolved_pet(tmp_path: Path, monkeypatch) -> None:
    provider = _RecordingProvider(guest_value=_card())
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _grant_guest(client)
        response = client.get("/api/guest/scene")

    assert response.status_code == 404
    assert provider.guest_calls == []


def test_guest_card_must_be_bound_to_the_resolved_pet(tmp_path: Path, monkeypatch) -> None:
    provider = _WrongPetProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _start_and_adopt(client)
        client.cookies.clear()
        _grant_guest(client)
        response = client.get("/api/guest/scene")

    assert response.status_code == 503
    assert response.json() == {"detail": "guest card unavailable"}


@pytest.mark.parametrize("audience", ["owner", "guest"])
@pytest.mark.parametrize("failure", ["unknown_key", "subject_mismatch", "exception"])
def test_invalid_or_failed_overlay_cards_fail_closed_without_payload(
    tmp_path: Path,
    monkeypatch,
    audience: str,
    failure: str,
) -> None:
    card = _card()
    error: Exception | None = None
    if failure == "unknown_key":
        card["private_note"] = SENTINEL
    elif failure == "subject_mismatch":
        card["species"] = "pebble"
    else:
        error = RuntimeError(f"{SENTINEL} provider failure")

    provider = _RecordingProvider(
        owner_value=card if audience == "owner" else None,
        guest_value=card if audience == "guest" else None,
        owner_error=error if audience == "owner" else None,
        guest_error=error if audience == "guest" else None,
    )
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _start_and_adopt(client)
        if audience == "owner":
            response = client.get("/api/me")
            expected_detail = "site overlay unavailable"
        else:
            client.cookies.clear()
            _grant_guest(client)
            response = client.get("/api/guest/scene")
            expected_detail = "guest card unavailable"

    assert response.status_code == 503
    assert response.json() == {"detail": expected_detail}
    assert SENTINEL not in response.text


def test_provider_lifecycle_wraps_the_application_lifespan(tmp_path: Path, monkeypatch) -> None:
    provider = _RecordingProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    assert provider.lifecycle == []
    with TestClient(app) as client:
        assert provider.lifecycle == ["startup"]
        assert client.get("/api/me").status_code == 200
    assert provider.lifecycle == ["startup", "shutdown"]


@pytest.mark.parametrize("failure", ["startup", "shutdown"])
def test_provider_lifecycle_failures_still_dispose_the_core_engine(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    error = RuntimeError(f"{failure} failed")
    provider = _RecordingProvider(
        startup_error=error if failure == "startup" else None,
        shutdown_error=error if failure == "shutdown" else None,
    )
    app = _load_app(tmp_path, monkeypatch, provider)
    main = importlib.import_module("app.main")
    dispose = AsyncMock(wraps=main.engine.dispose)
    monkeypatch.setattr(
        main,
        "engine",
        SimpleNamespace(begin=main.engine.begin, dispose=dispose),
    )

    with pytest.raises(RuntimeError, match=f"{failure} failed"), TestClient(app) as client:
        assert failure == "shutdown"
        assert client.get("/api/me").status_code == 200

    assert provider.lifecycle == ["startup", "shutdown"]
    dispose.assert_awaited_once()


def test_provider_can_write_its_own_table_in_the_core_sqlite_database(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "woolroom-overlay-test.db"
    provider = _SameDatabaseProvider(db_path)
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _, pet = _start_and_adopt(client)
        response = client.get("/api/me")

        assert response.status_code == 200
        assert response.json()["card"]["coat"] == "ash"

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT pet_id, coat FROM private_card_reads"
        ).fetchall()
    assert rows == [(pet["id"], "ash")]


def test_manually_constructed_invalid_card_fails_closed(tmp_path: Path, monkeypatch) -> None:
    invalid = PetCardV1(
        schema_version=1,
        card_id="invalid",
        species="cat",
        coat="ash",
        pronoun="it",
        svg="<g />",
        palette={},
        geometry={},
    )
    provider = _RecordingProvider(owner_value=invalid)
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _start_and_adopt(client)
        response = client.get("/api/me")

    assert response.status_code == 503
    assert response.json() == {"detail": "site overlay unavailable"}


def test_card_refresh_is_scoped_to_an_owned_or_guest_visible_pet(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RecordingProvider(guest_value=_card())
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        _, pet = _start_and_adopt(client)
        client.cookies.clear()
        _grant_guest(client)

        visible = client.get(f"/api/card?pet={pet['id']}")
        hidden = client.get("/api/card?pet=another_pet")

    assert visible.status_code == 200
    assert visible.headers["Cache-Control"] == "private, no-store"
    assert visible.json()["card"] == _card()
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "guest card is not available"}
    assert provider.guest_calls == [
        GuestCardSubject(pet_id=pet["id"], species="cat", coat="ash")
    ]


def test_pending_ceremony_participant_can_fetch_only_the_card(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _SubjectCardProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as owner, TestClient(app) as partner:
        _start_and_adopt(owner)
        invite = owner.post("/api/invite")
        assert invite.status_code == 200
        joined = partner.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        assert joined.status_code == 303
        started = partner.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200
        adopted = owner.post(
            "/api/adopt-second",
            json={"name": "Nova", "quirk": "content_sigher", "coat": "tuxedo"},
        )
        assert adopted.status_code == 200
        pending = adopted.json()["pet"]

        provider.owner_calls.clear()
        card = partner.get(f"/api/card?pet={pending['id']}")
        room = partner.post("/api/room", json={"pet_id": pending["id"]})
        action = partner.post(
            f"/api/action?pet={pending['id']}",
            json={"type": "greet"},
        )
        unrelated = partner.get("/api/card?pet=unknown_pet")

    assert card.status_code == 200
    assert card.headers["Cache-Control"] == "private, no-store"
    assert card.json()["card"]["card_id"] == SENTINEL
    assert provider.owner_calls == [
        OwnerCardSubject(
            user_id=started.json()["user_id"],
            pet_id=pending["id"],
            species="cat",
            coat="tuxedo",
        )
    ]
    assert room.status_code == 403
    assert action.status_code == 403
    assert unrelated.status_code == 403


def test_guest_can_fetch_only_pinned_and_currently_visible_visitor_cards(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _SubjectCardProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as owner, TestClient(app) as partner, TestClient(app) as guest:
        _, pinned = _start_and_adopt(owner)
        invite = owner.post("/api/invite")
        assert invite.status_code == 200
        joined = partner.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        assert joined.status_code == 303
        assert partner.post(
            "/api/start", json={"display_name": "Partner"}
        ).status_code == 200
        adopted = owner.post(
            "/api/adopt-second",
            json={"name": "Nova", "quirk": "content_sigher", "coat": "ash"},
        )
        assert adopted.status_code == 200
        visitor = adopted.json()["pet"]
        assert partner.post(
            "/api/second-quirk",
            json={"pet_id": visitor["id"], "quirk": "zoomie_initiator"},
        ).status_code == 200
        started = owner.post("/api/visit", json={"pet_id": visitor["id"]})
        assert started.status_code == 200
        assert started.json()["visit"]["host_pet_id"] == pinned["id"]

        _grant_guest(guest)
        scene = guest.get("/api/guest/scene")
        pinned_card = guest.get(f"/api/card?pet={pinned['id']}")
        visitor_card = guest.get(f"/api/card?pet={visitor['id']}")
        hidden = guest.get("/api/card?pet=unknown_pet")
        packs = guest.get("/api/packs")
        voice = guest.get("/api/voice")
        assert owner.post(
            "/api/visit/end", json={"pet_id": pinned["id"]}
        ).status_code == 200
        after_visit = guest.get(f"/api/card?pet={visitor['id']}")
        assert owner.post(
            "/api/visit", json={"pet_id": pinned["id"]}
        ).status_code == 200
        away_side = guest.get(f"/api/card?pet={visitor['id']}")
        assert owner.post(
            "/api/visit/end", json={"pet_id": pinned["id"]}
        ).status_code == 200

    assert scene.status_code == 200
    visit = scene.json()["pet"]["visit"]
    assert set(visit) == {"id", "role", "visitor"}
    assert visit["role"] == "host"
    assert set(visit["visitor"]) == {
        "id",
        "name",
        "species",
        "coat",
        "animation_state",
        "render_scale",
    }
    assert visit["visitor"]["id"] == visitor["id"]
    assert pinned_card.status_code == 200
    assert visitor_card.status_code == 200
    assert visitor_card.json()["card"]["card_id"] == SENTINEL
    assert hidden.status_code == 404
    assert after_visit.status_code == 404
    assert away_side.status_code == 404
    assert SENTINEL not in json.dumps(packs.json())
    assert SENTINEL not in json.dumps(voice.json())
    assert [subject.pet_id for subject in provider.guest_calls] == [
        pinned["id"],
        pinned["id"],
        visitor["id"],
    ]


def test_outer_guest_access_uses_the_guest_projection_even_with_an_owner_cookie(
    tmp_path: Path, monkeypatch
) -> None:
    owner_card = _card()
    owner_card["card_id"] = "owner_card"
    guest_card = _card()
    guest_card["card_id"] = "guest_card"
    provider = _RecordingProvider(owner_value=owner_card, guest_value=guest_card)
    app = _load_app(tmp_path, monkeypatch, provider, site_password="door")

    with TestClient(app) as client:
        unlocked = client.post("/api/site-access", json={"password": "door"})
        assert unlocked.status_code == 200
        _, pet = _start_and_adopt(client)
        provider.owner_calls.clear()
        locked = client.post("/api/site-access/logout")
        assert locked.status_code == 200
        entered_guest_mode = client.post("/api/guest-access")
        assert entered_guest_mode.status_code == 200

        response = client.get(f"/api/card?pet={pet['id']}")

    assert response.status_code == 200
    assert response.json()["card"]["card_id"] == "guest_card"
    assert provider.owner_calls == []
    assert provider.guest_calls == [
        GuestCardSubject(pet_id=pet["id"], species="cat", coat="ash")
    ]


def test_coat_change_returns_the_rebound_card(tmp_path: Path, monkeypatch) -> None:
    provider = _CoatCardProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as client:
        user_id, pet = _start_and_adopt(client)
        provider.owner_calls.clear()
        response = client.put(
            f"/api/coat?pet={pet['id']}",
            json={"coat": "tuxedo"},
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["coat"] == "tuxedo"
    assert response.json()["card"]["coat"] == "tuxedo"
    assert provider.owner_calls == [
        OwnerCardSubject(
            user_id=user_id,
            pet_id=pet["id"],
            species="cat",
            coat="tuxedo",
        )
    ]


def test_room_switch_returns_the_target_pets_card(tmp_path: Path, monkeypatch) -> None:
    provider = _CoatCardProvider()
    app = _load_app(tmp_path, monkeypatch, provider)

    with TestClient(app) as owner, TestClient(app) as partner:
        _, founding = _start_and_adopt(owner, coat="ash")
        invite = owner.post("/api/invite")
        assert invite.status_code == 200
        joined = partner.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        assert joined.status_code == 303
        started = partner.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200
        adopted = owner.post(
            "/api/adopt-second",
            json={"name": "Nova", "quirk": "content_sigher", "coat": "tuxedo"},
        )
        assert adopted.status_code == 200
        second = adopted.json()["pet"]
        confirmed = partner.post(
            "/api/second-quirk",
            json={"pet_id": second["id"], "quirk": "zoomie_initiator"},
        )
        assert confirmed.status_code == 200

        provider.owner_calls.clear()
        switched = partner.post("/api/room", json={"pet_id": second["id"]})

        assert switched.status_code == 200
        assert switched.headers["Cache-Control"] == "private, no-store"
        assert switched.json()["pet"]["id"] == second["id"]
        assert switched.json()["card"]["coat"] == "tuxedo"
        assert provider.owner_calls == [
            OwnerCardSubject(
                user_id=started.json()["user_id"],
                pet_id=second["id"],
                species="cat",
                coat="tuxedo",
            )
        ]
        assert founding["id"] != second["id"]


def test_client_resolves_a_matching_card_without_mutating_public_packs(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser card contract")
    source = Path(__file__).parents[1] / "app/static/js/figures.js"
    module = tmp_path / "figures.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    module_uri = module.as_uri()
    script = f"""
      import {{ figureSvg, packsForPet, paletteFor, touchZoneFor }} from {json.dumps(module_uri)};
      const publicPacks = {{ pebble: {{ svg: "<g id='public'/>", palettes: {{}}, geometry: {{}} }} }};
      const card = {json.dumps(_card())};
      const pet = {{ species: "cat", coat: "ash" }};
      const resolved = packsForPet(card, pet, publicPacks);
      const mismatch = packsForPet(card, {{ species: "cat", coat: "tuxedo" }}, publicPacks);
      console.log(JSON.stringify({{
        keys: Object.keys(resolved),
        publicKeys: Object.keys(publicPacks),
        mismatchIsPublic: mismatch === publicPacks,
        svg: figureSvg("cat", {{ packs: resolved }}),
        palette: paletteFor("cat", "ash", resolved),
        zone: touchZoneFor("cat", 200, 350, resolved),
      }}));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["keys"] == ["cat"]
    assert payload["publicKeys"] == ["pebble"]
    assert payload["mismatchIsPublic"] is True
    assert SENTINEL not in json.dumps(payload)
    assert "circle" in payload["svg"]
    assert payload["palette"] == _card()["palette"]
    assert payload["zone"] == "ear"


def test_client_caches_and_renders_private_cards_only_for_persisted_visible_pets(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser card contract")
    repo_root = Path(__file__).parents[1]
    api_uri = (repo_root / "app/static/js/api.js").as_uri()
    figures_uri = (repo_root / "app/static/js/figures.js").as_uri()
    script = f"""
      import {{ apiMethods }} from {json.dumps(api_uri)};
      import {{ figureMethods }} from {json.dumps(figures_uri)};

      const makeCard = (id, marker, body) => ({{
        card_id: `${{marker}}_card`,
        species: "dog",
        coat: "red",
        svg: `<g id="${{marker}}"><g id="dog-eyes"></g></g>`,
        palette: {{ body, belly: "#aabbcc", point: "#ddeeff" }},
        geometry: {{}},
      }});
      const active = {{ id: "active_pet", species: "dog", coat: "red" }};
      const ceremony = {{ id: "ceremony_pet", species: "dog", coat: "red", pending: true }};
      const visitor = {{ id: "visitor_pet", species: "dog", coat: "red" }};
      const cards = {{
        active_pet: makeCard("active_pet", "private_active", "#111111"),
        ceremony_pet: makeCard("ceremony_pet", "private_ceremony", "#222222"),
        visitor_pet: makeCard("visitor_pet", "private_visitor", "#333333"),
      }};
      const requests = [];
      globalThis.fetch = async (url) => {{
        requests.push(url);
        const id = new URL(url, "https://woolroom.test").searchParams.get("pet");
        return {{ ok: true, json: async () => ({{ card: cards[id] }}) }};
      }};
      const publicPacks = {{
        dog: {{
          svg: '<g id="public_dog"><g id="dog-eyes"></g></g>',
          palettes: {{ red: {{ body: "#999999", belly: "#eeeeee", point: "#777777" }} }},
          geometry: {{}},
        }},
      }};
      const publicBefore = JSON.stringify(publicPacks);
      const context = {{
        pet: {{
          ...active,
          visit: {{ id: "visit_1", role: "host", visitor }},
        }},
        pets: [active, ceremony],
        guest: false,
        card: cards.active_pet,
        petCardCache: {{
          active_pet: {{ species: "dog", coat: "red", card: cards.active_pet }},
        }},
        _cardLoads: new Set(),
        _visitorArt: null,
        packs: publicPacks,
        ceremonyPet() {{ return this.pets.find((pet) => pet.pending) || null; }},
        ...apiMethods,
        ...figureMethods,
      }};
      context._syncVisiblePetCards();
      await new Promise((resolve) => setTimeout(resolve, 0));

      const rendered = {{
        active: context.petFigureSvg(),
        ceremony: context.ceremonyPreviewSvg(),
        ceremonyStyle: context.ceremonyPreviewCoatStyle(),
        visitor: context.visitorSvg(),
        initialAdoption: context.petPreviewSvg("dog"),
      }};
      context.pet = {{ ...active, visit: null }};
      context._syncVisiblePetCards();
      console.log(JSON.stringify({{
        requests,
        cacheKeys: Object.keys(context.petCardCache).sort(),
        rendered,
        publicUnchanged: JSON.stringify(publicPacks) === publicBefore,
      }}));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["requests"] == [
        "/api/card?pet=ceremony_pet",
        "/api/card?pet=visitor_pet",
    ]
    assert payload["cacheKeys"] == ["active_pet", "ceremony_pet"]
    assert "private_active" in payload["rendered"]["active"]
    assert "private_ceremony" in payload["rendered"]["ceremony"]
    assert "#222222" in payload["rendered"]["ceremonyStyle"]
    assert "private_visitor" in payload["rendered"]["visitor"]
    assert "pet-eyes-visitor" in payload["rendered"]["visitor"]
    assert "public_dog" in payload["rendered"]["initialAdoption"]
    assert "private_" not in payload["rendered"]["initialAdoption"]
    assert payload["publicUnchanged"] is True


def test_client_refreshes_a_pet_scoped_card_when_realtime_changes_its_subject(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser card contract")
    source = Path(__file__).parents[1] / "app/static/js/ws.js"
    module = tmp_path / "ws.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    module_uri = module.as_uri()
    api_uri = (Path(__file__).parents[1] / "app/static/js/api.js").as_uri()
    script = f"""
      import {{ wsMethods }} from {json.dumps(module_uri)};
      import {{ apiMethods }} from {json.dumps(api_uri)};
      const fetched = [];
      globalThis.fetch = async (url, options) => {{
        fetched.push([url, options]);
        const coat = url.includes("pet_b") ? "tuxedo" : "ash";
        return {{
          ok: true,
          json: async () => ({{ card: {{ card_id: "new", species: "cat", coat }} }}),
        }};
      }};
      const refreshContext = {{
        pet: {{ id: "pet_a", species: "cat", coat: "ash" }},
        pets: [],
        petCardCache: {{}},
        card: null,
        _cardLoads: new Set(),
        ...apiMethods,
      }};
      await wsMethods._refreshActiveCard.call(refreshContext, "pet_a", "cat", "ash");

      const eventContext = {{
        pet: {{ id: "pet_b", species: "cat", coat: "ash", participant_count: 2 }},
        pets: [],
        petCardCache: {{
          pet_b: {{
            species: "cat",
            coat: "ash",
            card: {{ card_id: "old", species: "cat", coat: "ash" }},
          }},
        }},
        card: {{ card_id: "old", species: "cat", coat: "ash" }},
        _cardLoads: new Set(),
        guest: true,
        ...apiMethods,
        _applyPetState(pet) {{
          this.pet = {{ ...this.pet, ...pet }};
          this._syncVisiblePetCards();
        }},
      }};
      wsMethods._onWs.call(eventContext, {{
        type: "pet_state",
        pet: {{ id: "pet_b", species: "cat", coat: "tuxedo", participant_count: 2 }},
      }});
      await new Promise((resolve) => setTimeout(resolve, 0));
      let staleApplied = false;
      const staleContext = {{
        pet: {{ id: "pet_c", species: "cat", coat: "ash" }},
        card: {{ card_id: "pet_c" }},
        guest: false,
        _applyPetState() {{ staleApplied = true; }},
      }};
      wsMethods._onWs.call(staleContext, {{
        type: "pet_state",
        pet: {{ id: "pet_a", species: "cat", coat: "ash" }},
      }});
      console.log(JSON.stringify({{
        fetched,
        card: refreshContext.card,
        eventCard: eventContext.card,
        eventEntry: eventContext.petCardCache.pet_b,
        staleApplied,
        stalePet: staleContext.pet.id,
      }}));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["fetched"] == [
        ["/api/card?pet=pet_a", {"credentials": "same-origin"}],
        ["/api/card?pet=pet_b", {"credentials": "same-origin"}],
    ]
    assert payload["card"] == {"card_id": "new", "species": "cat", "coat": "ash"}
    assert payload["eventCard"] == {
        "card_id": "new",
        "species": "cat",
        "coat": "tuxedo",
    }
    assert payload["eventEntry"]["coat"] == "tuxedo"
    assert payload["staleApplied"] is False
    assert payload["stalePet"] == "pet_c"


def test_client_atomically_swaps_cards_for_coat_and_room_changes(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser card contract")
    source = Path(__file__).parents[1] / "app/static/js/api.js"
    module = tmp_path / "api.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    module_uri = module.as_uri()
    script = f"""
      import {{ apiMethods }} from {json.dumps(module_uri)};
      const coatCard = {{ card_id: "coat_card", species: "cat", coat: "tuxedo" }};
      globalThis.fetch = async () => ({{
        ok: true,
        json: async () => ({{ coat: "tuxedo", card: coatCard }}),
      }});
      const coatContext = {{
        guest: false,
        pet: {{ id: "pet_a", species: "cat", coat: "ash" }},
        pets: [],
        petCardCache: {{}},
        _cardLoads: new Set(),
        card: {{ card_id: "old" }},
        ...apiMethods,
      }};
      await apiMethods.setCoat.call(coatContext, "tuxedo");

      const roomCard = {{ card_id: "room_card", species: "cat", coat: "tuxedo" }};
      const order = [];
      const roomContext = {{
        pet: {{ id: "pet_a", species: "cat", coat: "ash", visit: null }},
        pets: [],
        petCardCache: {{}},
        _cardLoads: new Set(),
        card: {{ card_id: "old" }},
        woolNotes: [], woolHearts: [], woolShelf: [], woolPatches: [], localRoomNotes: [],
        ...apiMethods,
        _woolRoomSwitched() {{}},
        _applyPetState() {{ order.push(this.card.card_id); }},
        connectWs() {{}},
      }};
      globalThis.localStorage = {{ setItem() {{}} }};
      apiMethods._applyRoomSwitch.call(
        roomContext,
        {{ id: "pet_b", species: "cat", coat: "tuxedo", visit: null }},
        roomCard,
      );
      console.log(JSON.stringify({{
        coat: coatContext.pet.coat,
        coatCard: coatContext.card.card_id,
        roomCard: roomContext.card.card_id,
        order,
      }}));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "coat": "tuxedo",
        "coatCard": "coat_card",
        "roomCard": "room_card",
        "order": ["room_card"],
    }


def test_client_prefers_a_matching_card_pronoun(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser card contract")
    source = Path(__file__).parents[1] / "app/static/js/wool.js"
    module = tmp_path / "wool.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    module_uri = module.as_uri()
    script = f"""
      import {{ sceneMethods }} from {json.dumps(module_uri)};
      const pet = {{ species: "cat", coat: "ash", pronoun: "he" }};
      const matching = sceneMethods._petPronoun.call({{
        pet,
        card: {{ species: "cat", coat: "ash", pronoun: "she" }},
      }});
      const stale = sceneMethods._petPronoun.call({{
        pet,
        card: {{ species: "cat", coat: "tuxedo", pronoun: "she" }},
      }});
      console.log(JSON.stringify({{ matching, stale }}));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"matching": "she", "stale": "he"}
