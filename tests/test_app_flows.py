from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch,
    site_password: str = "",
    adopt_allowlist: str = "",
    admin_token: str = "",
    open_signup: bool = True,
) -> object:
    db_path = tmp_path / "woolroom-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("ADOPT_ALLOWLIST", adopt_allowlist)
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("OPEN_SIGNUP", "true" if open_signup else "false")
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


def test_coat_defaults_choice_and_change(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client, TestClient(app) as other_client:
        # Default: adopting without a coat keeps the marmalade wool.
        client.post("/api/start", json={"display_name": "Ash"})
        adopt = client.post(
            "/api/adopt",
            json={"name": "Purl", "quirks": ["content_sigher", "lean_in_greeter"]},
        )
        assert adopt.status_code == 200
        assert adopt.json()["pet"]["coat"] == "marmalade"
        assert client.get("/api/me").json()["pet"]["coat"] == "marmalade"

        # Change after adoption — the retroactive path for older pets.
        changed = client.put("/api/coat", json={"coat": "ash"})
        assert changed.status_code == 200
        assert changed.json()["coat"] == "ash"
        assert client.get("/api/me").json()["pet"]["coat"] == "ash"

        # A fresh adopter can pick a coat at adoption time.
        other_client.post("/api/start", json={"display_name": "Wren"})
        adopt2 = other_client.post(
            "/api/adopt",
            json={
                "name": "Miso",
                "quirks": ["content_sigher", "lean_in_greeter"],
                "coat": "tuxedo",
            },
        )
        assert adopt2.status_code == 200
        assert adopt2.json()["pet"]["coat"] == "tuxedo"
        assert other_client.get("/api/me").json()["pet"]["coat"] == "tuxedo"


def test_coat_rejects_invalid_values(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")

        adopt = client.post(
            "/api/adopt",
            json={
                "name": "Miso",
                "quirks": ["content_sigher", "lean_in_greeter"],
                "coat": "purple",
            },
        )
        assert adopt.status_code == 422

        changed = client.put("/api/coat", json={"coat": "purple"})
        assert changed.status_code == 422
        # The failed writes left the coat alone.
        assert client.get("/api/me").json()["pet"]["coat"] == pet.get("coat", "marmalade")


def test_websocket_connect_pushes_initial_pet_state(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")

        with client.websocket_connect("/ws") as ws:
            event = ws.receive_json()

        assert event["type"] == "pet_state"
        assert event["pet"]["id"] == pet["id"]
        assert event["pet"]["name"] == "Purl"
        assert event["pet"]["participant_count"] == 1
        assert "pose_detail" in event["pet"]
        assert "shared_trace" in event["pet"]


def test_invite_join_links_second_human_to_same_pet(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        owner_pet = _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        assert invite.status_code == 200
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        assert joined.headers["location"] == "/"
        assert joiner_client.cookies.get("woolroom_pending_invite")

        started = joiner_client.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200
        assert not joiner_client.cookies.get("woolroom_pending_invite")

        joiner_me = joiner_client.get("/api/me")
        assert joiner_me.status_code == 200
        joiner_payload = joiner_me.json()
        assert joiner_payload["user"]["display_name"] == "Partner"
        assert joiner_payload["pet"]["id"] == owner_pet["id"]
        assert joiner_payload["pet"]["participant_count"] == 2
        assert joiner_payload["pet"]["household_names"] == ["Ash", "Partner"]
        assert "pose_detail" in joiner_payload["pet"]
        assert "shared_trace" in joiner_payload["pet"]

        owner_me = owner_client.get("/api/me")
        assert owner_me.status_code == 200
        owner_payload = owner_me.json()
        assert owner_payload["pet"]["id"] == owner_pet["id"]
        assert owner_payload["pet"]["participant_count"] == 2
        assert owner_payload["pet"]["household_names"] == ["Ash", "Partner"]


def test_invite_link_303_body_carries_og_tags(tmp_path: Path, monkeypatch) -> None:
    """The invite URL is the one moment presented to the second human —
    pasted into a chat it should unfurl, so the 303 body carries OG meta
    (browsers still just follow Location; behavior unchanged)."""
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        assert joined.headers["location"] == "/"
        body = joined.text
        assert "og:title" in body and "Purl" in body
        assert "og:description" in body
        assert "og:image' content='http://testserver/static/apple-touch-icon.png" in body
        assert "twitter:card' content='summary" in body


def test_start_auto_joins_pending_invite(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        owner_pet = _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        assert joiner_client.cookies.get("woolroom_pending_invite")

        started = joiner_client.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200
        start_payload = started.json()
        assert start_payload["joined_pet_id"] == owner_pet["id"]
        assert start_payload["pending_invite_error"] is None
        assert not joiner_client.cookies.get("woolroom_pending_invite")

        joiner_me = joiner_client.get("/api/me")
        assert joiner_me.status_code == 200
        joiner_payload = joiner_me.json()
        assert joiner_payload["pet"]["id"] == owner_pet["id"]
        assert joiner_payload["pet"]["participant_count"] == 2


def test_api_me_exposes_pending_invite_summary_before_start(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        assert joiner_client.cookies.get("woolroom_pending_invite")

        me = joiner_client.get("/api/me")
        assert me.status_code == 200
        payload = me.json()
        assert payload["user"] is None
        assert payload["pet"] is None
        assert payload["pending_invite"]["pet_name"] == "Purl"
        assert payload["pending_invite"]["adopted_by"] == "Ash"


def test_recovery_link_rebinds_same_user_on_new_client(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")
        me = client.get("/api/me")
        assert me.status_code == 200
        payload = me.json()
        recovery_path = urlparse(
            client.get("/api/recovery-url").json()["recovery_url"]
        ).path
        user_id = payload["user"]["id"]

        client.cookies.clear()

        recovery = client.get(recovery_path, follow_redirects=False)
        assert recovery.status_code == 303
        assert recovery.headers["location"] == "/"
        assert client.cookies.get("woolroom_session")

        rebound_me = client.get("/api/me")
        assert rebound_me.status_code == 200
        rebound_payload = rebound_me.json()
        assert rebound_payload["user"]["id"] == user_id
        assert rebound_payload["pet"]["id"] == pet["id"]
        assert "pose_detail" in rebound_payload["pet"]
        assert "shared_trace" in rebound_payload["pet"]


def test_adopt_rejected_when_user_not_in_allowlist(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, adopt_allowlist="some-other-user-id")

    with TestClient(app) as client:
        start = client.post("/api/start", json={"display_name": "Stranger"})
        assert start.status_code == 200

        adopt = client.post(
            "/api/adopt",
            json={"name": "Purl", "quirks": ["content_sigher", "lean_in_greeter"]},
        )
        assert adopt.status_code == 403


def test_adopt_allowed_when_user_in_allowlist(tmp_path: Path, monkeypatch) -> None:
    # Seed run: mint a user, capture id + session cookie. Same SECRET_KEY + DB.
    seed_app = _load_app(tmp_path, monkeypatch)
    with TestClient(seed_app) as client:
        start = client.post("/api/start", json={"display_name": "Ash"})
        user_id = start.json()["user_id"]
        session_cookie = client.cookies.get("woolroom_session")
        assert session_cookie

    # Reload app with that user_id in the allowlist; reuse session cookie.
    gated_app = _load_app(tmp_path, monkeypatch, adopt_allowlist=user_id)
    with TestClient(gated_app) as client:
        client.cookies.set("woolroom_session", session_cookie)
        adopt = client.post(
            "/api/adopt",
            json={"name": "Purl", "quirks": ["content_sigher", "lean_in_greeter"]},
        )
        assert adopt.status_code == 200


def test_admin_regenerate_recovery_returns_fresh_url(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, admin_token="secret-admin-token")

    with TestClient(app) as setup_client, TestClient(app) as caller:
        # Seed a user + pet.
        _start_and_adopt(setup_client, "Wren", "Purl")
        before_me = setup_client.get("/api/me").json()
        before_url = setup_client.get("/api/recovery-url").json()["recovery_url"]
        original_user_id = before_me["user"]["id"]

        # Admin requests fresh recovery for display_name "Wren".
        resp = caller.post(
            "/admin/regenerate-recovery",
            headers={"X-Admin-Token": "secret-admin-token"},
            json={"display_name": "Wren"},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["user_id"] == original_user_id
        assert results[0]["is_participant"] is True
        new_url = results[0]["recovery_url"]
        assert new_url != before_url

        # The new URL works for an unauthenticated client.
        with TestClient(app) as fresh:
            new_path = urlparse(new_url).path
            resp = fresh.get(new_path, follow_redirects=False)
            assert resp.status_code == 303
            assert fresh.cookies.get("woolroom_session")
            me = fresh.get("/api/me").json()
            assert me["user"]["id"] == original_user_id

        # Additive: the OLD URL still works too — regenerate doesn't revoke.
        with TestClient(app) as old_client:
            old_path = urlparse(before_url).path
            resp = old_client.get(old_path, follow_redirects=False)
            assert resp.status_code == 303
            assert old_client.cookies.get("woolroom_session")


def test_admin_revoke_recovery_invalidates_all_user_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, admin_token="secret-admin-token")

    with TestClient(app) as setup_client, TestClient(app) as caller:
        _start_and_adopt(setup_client, "Wren", "Purl")
        first_url = setup_client.get("/api/recovery-url").json()["recovery_url"]
        # Mint a second URL so we know revoke kills BOTH.
        resp = caller.post(
            "/admin/regenerate-recovery",
            headers={"X-Admin-Token": "secret-admin-token"},
            json={"display_name": "Wren"},
        )
        second_url = resp.json()["results"][0]["recovery_url"]

        # Revoke all of them.
        resp = caller.post(
            "/admin/revoke-recovery",
            headers={"X-Admin-Token": "secret-admin-token"},
            json={"display_name": "Wren"},
        )
        assert resp.status_code == 200
        assert resp.json()["revoked_count"] >= 2

        # Both old URLs now 404.
        for url in (first_url, second_url):
            with TestClient(app) as fresh:
                resp = fresh.get(urlparse(url).path, follow_redirects=False)
                assert resp.status_code == 404


def test_admin_regenerate_recovery_rejects_bad_token(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, admin_token="real-token")

    with TestClient(app) as client:
        _start_and_adopt(client, "Wren", "Purl")

    with TestClient(app) as caller:
        resp = caller.post(
            "/admin/regenerate-recovery",
            headers={"X-Admin-Token": "wrong-token"},
            json={"display_name": "Wren"},
        )
        assert resp.status_code == 403

        resp = caller.post(
            "/admin/regenerate-recovery",
            json={"display_name": "Wren"},
        )
        assert resp.status_code == 403


def test_admin_regenerate_recovery_disabled_when_token_empty(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, admin_token="")

    with TestClient(app) as client:
        _start_and_adopt(client, "Wren", "Purl")

    with TestClient(app) as caller:
        resp = caller.post(
            "/admin/regenerate-recovery",
            headers={"X-Admin-Token": ""},
            json={"display_name": "Wren"},
        )
        assert resp.status_code == 403


def test_recovery_link_with_matching_session_is_silent(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")
        recovery_path = urlparse(
            client.get("/api/recovery-url").json()["recovery_url"]
        ).path

        before = client.cookies.get("woolroom_session")

        recovery = client.get(recovery_path, follow_redirects=False)
        assert recovery.status_code == 303
        assert recovery.headers["location"] == "/"

        after = client.cookies.get("woolroom_session")
        assert before == after


def test_recovery_link_with_other_user_session_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as ash, TestClient(app) as wren:
        _start_and_adopt(ash, "Ash", "Purl")
        ash_recovery = urlparse(ash.get("/api/recovery-url").json()["recovery_url"]).path

        wren.post("/api/start", json={"display_name": "Wren"})

        attempt = wren.get(ash_recovery, follow_redirects=False)
        assert attempt.status_code == 409


def test_stale_invite_for_existing_participant_is_silent_redirect(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as ash, TestClient(app) as wren:
        _start_and_adopt(ash, "Ash", "Purl")
        invite_url = ash.post("/api/invite").json()["url"]
        invite_path = urlparse(invite_url).path

        # Wren joins via the invite, burning it.
        wren.get(invite_path, follow_redirects=False)
        wren.post("/api/start", json={"display_name": "Wren"})

        # She comes back later and clicks the same (now-stale) link.
        revisit = wren.get(invite_path, follow_redirects=False)
        assert revisit.status_code == 303
        assert revisit.headers["location"] == "/"


def test_aliases_persist_and_apply_to_room_notes(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        joiner_client.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        joiner_client.post("/api/start", json={"display_name": "Wren"})

        # Ash sets an alias for Wren.
        resp = owner_client.put(
            "/api/aliases",
            json={"partner_aliases": {"Wren": "wrennie"}},
        )
        assert resp.status_code == 200
        assert resp.json()["partner_aliases"] == {"Wren": "wrennie"}

        # Wren does something.
        wren_user_id = joiner_client.get("/api/me").json()["user"]["id"]
        joiner_client.post("/api/action", json={"type": "pet"})

        # Ash sees room notes that USE his alias for Wren, not her raw name.
        me = owner_client.get("/api/me").json()
        notes = me["pet"]["room_notes"]
        wren_notes = [n for n in notes if n["user_id"] == wren_user_id]
        assert wren_notes, "Wren's pet action should appear in Ash's room_notes"
        assert any("wrennie" in n["line"] for n in wren_notes)
        assert all("Wren" not in n["line"] for n in wren_notes)


def test_start_without_pending_invite_is_rejected_in_invite_only_mode(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, open_signup=False)
    with TestClient(app) as client:
        # Fresh-deployment bootstrap: the very first human is admitted even in
        # invite-only mode (a new self-hosted room is otherwise unreachable).
        first = client.post("/api/start", json={"display_name": "Ash"})
        assert first.status_code == 200
        # Any existing user closes the gate.
        client.cookies.clear()
        resp = client.post("/api/start", json={"display_name": "Wren"})
        assert resp.status_code == 403


def test_api_me_reports_effective_signup_openness(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, open_signup=False)
    with TestClient(app) as client:
        # Fresh DB: the landing page must show the begin form, so /api/me
        # mirrors the /api/start bootstrap.
        assert client.get("/api/me").json()["open_signup"] is True
        assert client.post("/api/start", json={"display_name": "Ash"}).status_code == 200
        client.cookies.clear()
        assert client.get("/api/me").json()["open_signup"] is False


def test_start_via_invite_works_in_invite_only_mode(
    tmp_path: Path, monkeypatch
) -> None:
    # Two app instances over the same DB: setup mints a pet + invite with
    # open_signup on; the test client runs under invite-only mode and must
    # produce a VALID invite cookie to /api/start.
    setup_app = _load_app(tmp_path, monkeypatch, open_signup=True)
    with TestClient(setup_app) as owner:
        _start_and_adopt(owner, "Ash", "Purl")
        invite_url = owner.post("/api/invite").json()["url"]
    gated_app = _load_app(tmp_path, monkeypatch, open_signup=False)
    with TestClient(gated_app) as joiner:
        joiner.get(urlparse(invite_url).path, follow_redirects=False)
        resp = joiner.post("/api/start", json={"display_name": "Wren"})
        assert resp.status_code == 200
        assert resp.json()["user_id"]


def test_start_with_bogus_invite_cookie_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch, open_signup=False)
    with TestClient(app) as client:
        client.cookies.set("woolroom_pending_invite", "this-is-not-a-real-token")
        resp = client.post("/api/start", json={"display_name": "Wren"})
        assert resp.status_code == 403


def test_recovery_link_persists_across_uses(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")
        original_path = urlparse(
            client.get("/api/recovery-url").json()["recovery_url"]
        ).path

        client.cookies.clear()

        first = client.get(original_path, follow_redirects=False)
        assert first.status_code == 303

        rebound_me = client.get("/api/me")
        assert rebound_me.status_code == 200
        # Persistence check now reads the dedicated endpoint — /api/me no
        # longer carries the credential.
        assert "recovery_url" not in rebound_me.json()
        assert (
            urlparse(client.get("/api/recovery-url").json()["recovery_url"]).path
            == original_path
        )

        client.cookies.clear()

        second = client.get(original_path, follow_redirects=False)
        assert second.status_code == 303
        assert client.cookies.get("woolroom_session")


def test_greet_action_uses_lean_in_greeter_response(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")

        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            assert initial["type"] == "pet_state"

            acted = client.post("/api/action", json={"type": "greet"})
            assert acted.status_code == 200

            state_event = ws.receive_json()
            response_event = ws.receive_json()

        assert state_event["type"] == "pet_state"
        assert response_event["type"] == "response"
        assert response_event["text"] == "*leans its whole shoulder into your shin and calls that hello*"
        assert response_event["is_utterance"] is False
        assert state_event["pet"]["shared_trace"]["event_type"] == "greet"


def test_action_http_response_includes_pet_and_response(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")

        acted = client.post("/api/action", json={"type": "greet"})
        assert acted.status_code == 200

        payload = acted.json()
        assert payload["ok"] is True
        assert payload["pet"]["shared_trace"]["event_type"] == "greet"
        assert payload["response"]["text"] == "*leans its whole shoulder into your shin and calls that hello*"
        assert payload["response"]["is_utterance"] is False
        assert payload["response"]["action"] == "greet"


def test_second_human_sees_recent_trace_from_other_person(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        started = joiner_client.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200

        acted = owner_client.post("/api/action", json={"type": "walk"})
        assert acted.status_code == 200

        joiner_me = joiner_client.get("/api/me")
        assert joiner_me.status_code == 200
        payload = joiner_me.json()
        trace = payload["pet"]["shared_trace"]
        assert trace is not None
        assert trace["event_type"] == "walk"
        assert trace["display_name"] == "Ash"
        assert trace["user_id"] != payload["user"]["id"]
        cue = payload["pet"]["shared_trace_cue"]
        assert cue is not None
        assert cue["mode"] == "leash"
        assert cue["event_type"] == "walk"
        assert cue["intensity"] in {"strong", "soft", "faint"}


def test_partner_traces_dedup_by_event_type_and_exclude_self(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        joiner_client.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        joiner_client.post("/api/start", json={"display_name": "Wren"})

        # Ash does the same kind of action twice + a different one. From
        # Wren's view, both kinds should show up once each — most-recent wins.
        owner_client.post("/api/action", json={"type": "pet"})
        owner_client.post("/api/action", json={"type": "walk"})
        owner_client.post("/api/action", json={"type": "pet"})
        # Wren's own action MUST NOT appear in her partner_traces.
        joiner_client.post("/api/action", json={"type": "greet"})

        payload = joiner_client.get("/api/me").json()
        traces = payload["pet"]["partner_traces"]
        types = [t["event_type"] for t in traces]
        assert sorted(types) == ["pet", "walk"]
        assert all(t["user_id"] != payload["user"]["id"] for t in traces)


def test_partner_absence_minutes_is_zero_after_partner_action(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        joiner_client.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
        joiner_client.post("/api/start", json={"display_name": "Wren"})

        # No partner action yet → None.
        before = joiner_client.get("/api/me").json()
        assert before["pet"]["partner_absence_minutes"] is None

        owner_client.post("/api/action", json={"type": "feed"})

        after = joiner_client.get("/api/me").json()
        absence = after["pet"]["partner_absence_minutes"]
        assert absence is not None
        assert absence < 1


def test_second_human_sees_recent_room_notes(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        _start_and_adopt(owner_client, "Ash", "Purl")
        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        started = joiner_client.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200

        assert owner_client.post("/api/action", json={"type": "feed"}).status_code == 200
        assert owner_client.post("/api/action", json={"type": "walk"}).status_code == 200

        joiner_me = joiner_client.get("/api/me")
        assert joiner_me.status_code == 200
        notes = joiner_me.json()["pet"]["room_notes"]
        assert len(notes) >= 2
        assert notes[0]["event_type"] == "walk"
        assert notes[0]["display_name"] == "Ash"
        assert "Ash" in notes[0]["line"]
        assert any(note["event_type"] == "feed" for note in notes)


def test_site_access_gate_requires_password_before_app_session(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="open sesame")

    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 303
        assert root.headers["location"].startswith("/access?next=")

        me = client.get("/api/me")
        assert me.status_code == 401
        assert me.json()["detail"] == "site access required"

        wrong = client.post("/api/site-access", json={"password": "wrong"})
        assert wrong.status_code == 401

        granted = client.post("/api/site-access", json={"password": "open sesame"})
        assert granted.status_code == 200
        assert client.cookies.get("woolroom_site_access")

        landing = client.get("/")
        assert landing.status_code == 200
        assert "woolroom" in landing.text.lower()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", "/"),
        ("next=%2Fhealthz", "/healthz"),
        ("next=%2Fjoin%2Ftoken%3Fvia%3Dinvite", "/join/token?via=invite"),
        ("next=relative", "/"),
        ("next=https%3A%2F%2Fevil.example", "/"),
        ("next=%2F%2Fevil.example", "/"),
        ("next=%2F%5Cevil.example", "/"),
        ("next=%2Fbad%0D%0ALocation%3Aevil", "/"),
        ("next=%2F.%2F%2Fevil.example", "/.//evil.example"),
        ("next=%2F%252e%2F%2Fevil.example", "/%2e//evil.example"),
        ("next=%2Fhealthz&next=https%3A%2F%2Fevil.example", "/"),
        ("next=https%3A%2F%2Fevil.example&next=%2Fhealthz", "/"),
    ],
)
def test_site_access_continuation_is_local(
    tmp_path: Path, monkeypatch, query: str, expected: str
) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="open sesame")

    with TestClient(app) as client:
        granted = client.post("/api/site-access", json={"password": "open sesame"})
        assert granted.status_code == 200

        path = f"/access?{query}" if query else "/access"
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == expected


def test_site_access_throttles_failures_per_ip(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="open sesame")

    with TestClient(app) as client:
        # Successes never count against the bucket.
        assert client.post("/api/site-access", json={"password": "open sesame"}).status_code == 200
        codes = [
            client.post("/api/site-access", json={"password": f"wrong{i}"}).status_code
            for i in range(7)
        ]
        assert codes == [401] * 5 + [429, 429]
        # Once tripped, even the right password waits out the window — the
        # throttle is on attempts, not on wrongness.
        assert client.post("/api/site-access", json={"password": "open sesame"}).status_code == 429


def test_site_access_cookie_persists_for_private_flow(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="open sesame")

    with TestClient(app) as client:
        granted = client.post("/api/site-access", json={"password": "open sesame"})
        assert granted.status_code == 200

        pet = _start_and_adopt(client, "Ash", "Purl")
        assert pet["name"] == "Purl"

        me = client.get("/api/me")
        assert me.status_code == 200
        payload = me.json()
        assert payload["user"]["display_name"] == "Ash"


def test_site_access_logout_clears_outer_gate_cookie(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch, site_password="open sesame")

    with TestClient(app) as client:
        granted = client.post("/api/site-access", json={"password": "open sesame"})
        assert granted.status_code == 200
        assert client.cookies.get("woolroom_site_access")

        logged_out = client.post("/api/site-access/logout")
        assert logged_out.status_code == 200

        root = client.get("/", follow_redirects=False)
        assert root.status_code == 303
        assert root.headers["location"].startswith("/access?next=")


async def test_concurrent_invite_requests_reuse_one_live_token(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    from app.storage.db import SessionLocal
    from app.storage.models import MagicLink
    from app.storage import repo
    from sqlalchemy import select

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")
        gate = asyncio.Event()

        async def _make_invite() -> str:
            async with SessionLocal() as session:
                await gate.wait()
                link = await repo.get_or_create_invite(session, pet["id"])
                await asyncio.sleep(0.05)
                await session.commit()
                return link.token

        first = asyncio.create_task(_make_invite())
        second = asyncio.create_task(_make_invite())
        await asyncio.sleep(0.01)
        gate.set()

        token_a, token_b = await asyncio.gather(first, second)
        assert token_a == token_b

        async with SessionLocal() as session:
            result = await session.execute(
                select(MagicLink).where(
                    MagicLink.pet_id == pet["id"],
                    MagicLink.purpose == "invite",
                    MagicLink.used_at.is_(None),
                )
            )
            invites = list(result.scalars())
            assert len(invites) == 1


async def test_core_facts_seed_on_adoption_and_extend_on_invite(
    tmp_path: Path, monkeypatch
) -> None:
    """adopted_by starts with the adopter, then includes the second human after join."""
    app = _load_app(tmp_path, monkeypatch)

    from app.memory import core as core_memory
    from app.storage.db import SessionLocal

    async def _facts_for(pet_id: str) -> dict[str, str]:
        async with SessionLocal() as session:
            return await core_memory.all_facts(session, pet_id)

    with TestClient(app) as owner_client, TestClient(app) as joiner_client:
        owner_pet = _start_and_adopt(owner_client, "Ash", "Purl")

        facts = await _facts_for(owner_pet["id"])
        assert facts["adopted_by"] == "Ash"
        assert "adopted_on" in facts

        invite = owner_client.post("/api/invite")
        invite_path = urlparse(invite.json()["url"]).path

        joiner_client.post("/api/start", json={"display_name": "Partner"})
        joined = joiner_client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303

        facts_after = await _facts_for(owner_pet["id"])
        assert facts_after["adopted_by"] == "Ash, Partner"


async def test_walk_action_records_first_walk_day_once(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    from app.memory import core as core_memory
    from app.storage.db import SessionLocal

    async def _facts(pet_id: str) -> dict[str, str]:
        async with SessionLocal() as session:
            return await core_memory.all_facts(session, pet_id)

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")

        # Before any walk: no first_walk_day fact.
        facts_before = await _facts(pet["id"])
        assert "first_walk_day" not in facts_before

        r = client.post("/api/action", json={"type": "walk"})
        assert r.status_code == 200

        facts_after = await _facts(pet["id"])
        assert "first_walk_day" in facts_after
        first_walk = facts_after["first_walk_day"]

        # Second walk does not overwrite.
        r2 = client.post("/api/action", json={"type": "walk"})
        assert r2.status_code == 200
        facts_final = await _facts(pet["id"])
        assert facts_final["first_walk_day"] == first_walk


async def test_first_walk_creates_exactly_one_moment(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    from app.storage.db import SessionLocal
    from app.storage.models import Moment
    from sqlalchemy import select

    import app.memory.moments as moments_module

    monkeypatch.setattr(moments_module.random, "random", lambda: 1.0)

    async def _walk_moments(pet_id: str) -> list[Moment]:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Moment).where(Moment.pet_id == pet_id, Moment.event_type == "walk")
            )
            return list(result.scalars())

    with TestClient(app) as client:
        pet = _start_and_adopt(client, "Ash", "Purl")

        first = client.post("/api/action", json={"type": "walk"})
        assert first.status_code == 200
        assert len(await _walk_moments(pet["id"])) == 1

        second = client.post("/api/action", json={"type": "walk"})
        assert second.status_code == 200
        assert len(await _walk_moments(pet["id"])) == 1


async def test_hides_small_things_walk_sets_scene_fx_and_memory(
    tmp_path: Path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)

    from app.memory import core as core_memory
    from app.storage.db import SessionLocal
    import app.engine.quirks as quirks_module

    monkeypatch.setattr(quirks_module.random, "choice", lambda items: "a bottle cap")

    async def _facts(pet_id: str) -> dict[str, str]:
        async with SessionLocal() as session:
            return await core_memory.all_facts(session, pet_id)

    with TestClient(app) as client:
        client.post("/api/start", json={"display_name": "Ash"})
        adopt = client.post(
            "/api/adopt",
            json={"name": "Purl", "quirks": ["hides_small_things", "lean_in_greeter"]},
        )
        assert adopt.status_code == 200
        pet = adopt.json()["pet"]

        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            assert initial["type"] == "pet_state"

            acted = client.post("/api/action", json={"type": "walk"})
            assert acted.status_code == 200

            state_event = ws.receive_json()
            response_event = ws.receive_json()

        assert state_event["type"] == "pet_state"
        assert state_event["pet"]["scene_fx"]["mode"] == "stash"
        assert state_event["pet"]["scene_fx"]["item"] == "a bottle cap"
        assert response_event["type"] == "response"
        assert "bottle cap" in response_event["text"]

        facts = await _facts(pet["id"])
        assert facts["hidden_thing"] == "a bottle cap"


def test_plain_action_sets_default_scene_fx(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")

        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            assert initial["type"] == "pet_state"

            acted = client.post("/api/action", json={"type": "walk"})
            assert acted.status_code == 200
            assert acted.json()["pet"]["scene_fx"]["mode"] == "leash_tug"

            state_event = ws.receive_json()
            response_event = ws.receive_json()

        assert state_event["type"] == "pet_state"
        assert state_event["pet"]["scene_fx"]["mode"] == "leash_tug"
        assert response_event["type"] == "response"
        assert response_event["action"] == "walk"


def test_origin_line_appears_in_api_me_after_adoption(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")
        me = client.get("/api/me")
        assert me.status_code == 200
        origin_line = me.json()["pet"]["origin_line"]
        assert origin_line is not None
        assert "Ash" in origin_line
        assert "living with" in origin_line


def test_feed_action_sates_the_hunger_cue(tmp_path: Path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client, "Ash", "Purl")

        # A fresh, never-fed cat reads as hungry — the bowl calls.
        before = client.get("/api/me").json()["pet"]
        assert before["hungry"] is True
        assert before["fed_minutes_ago"] is None

        acted = client.post("/api/action", json={"type": "feed"})
        assert acted.status_code == 200
        payload = acted.json()["pet"]
        assert payload["hungry"] is False
        assert payload["fed_minutes_ago"] == 0


def test_fed_minutes_ago_computes_hunger() -> None:
    from datetime import timedelta

    from app.runtime.pet_state import HUNGRY_AFTER_MINUTES, _fed_minutes_ago
    from app.time import utc_now

    stale = (
        f"{(utc_now() - timedelta(minutes=HUNGRY_AFTER_MINUTES + 30)).isoformat(timespec='seconds')}Z"
    )
    assert _fed_minutes_ago({"last_fed_at": stale}) >= HUNGRY_AFTER_MINUTES
    fresh = f"{utc_now().isoformat(timespec='seconds')}Z"
    assert _fed_minutes_ago({"last_fed_at": fresh}) == 0
    assert _fed_minutes_ago({}) is None
    assert _fed_minutes_ago({"last_fed_at": "not-a-timestamp"}) is None
