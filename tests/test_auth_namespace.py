from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from woolroom.auth import AuthNamespace

CUSTOM_AUTH = AuthNamespace(
    session_cookie="tenant_session",
    session_salt="tenant-session-v1",
    site_access_cookie="tenant_site_access",
    site_access_salt="tenant-site-access-v1",
    guest_access_cookie="tenant_guest_access",
    guest_access_salt="tenant-guest-access-v1",
    pending_invite_cookie="tenant_pending_invite",
)


def _custom_auth_values() -> dict[str, str]:
    return {
        "session_cookie": CUSTOM_AUTH.session_cookie,
        "session_salt": CUSTOM_AUTH.session_salt,
        "site_access_cookie": CUSTOM_AUTH.site_access_cookie,
        "site_access_salt": CUSTOM_AUTH.site_access_salt,
        "guest_access_cookie": CUSTOM_AUTH.guest_access_cookie,
        "guest_access_salt": CUSTOM_AUTH.guest_access_salt,
        "pending_invite_cookie": CUSTOM_AUTH.pending_invite_cookie,
    }


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    namespace: AuthNamespace | None = CUSTOM_AUTH,
    site_password: str = "",
    guest_enabled: bool = True,
):
    db_path = tmp_path / "auth-namespace.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("SECRET_KEY", "test-auth-namespace-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", site_password)
    monkeypatch.setenv("OPEN_SIGNUP", "true")
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true" if guest_enabled else "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    session_auth = importlib.import_module("app.auth.session")
    return main.create_app(auth_namespace=namespace), session_auth


def _start_and_adopt(client: TestClient) -> str:
    started = client.post("/api/start", json={"display_name": "Owner"})
    assert started.status_code == 200
    adopted = client.post(
        "/api/adopt",
        json={
            "name": "Fleece",
            "quirks": ["content_sigher", "lean_in_greeter"],
        },
    )
    assert adopted.status_code == 200
    return started.json()["user_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_cookie", ""),
        ("session_cookie", "unsafe cookie"),
        ("session_cookie", "x" * 129),
        ("session_salt", "unsafe salt"),
    ],
)
def test_auth_namespace_rejects_unsafe_values(field: str, value: str) -> None:
    values = _custom_auth_values()
    values[field] = value
    with pytest.raises(ValueError):
        AuthNamespace(**values)


@pytest.mark.parametrize(
    ("field", "duplicate_of"),
    [
        ("site_access_cookie", "session_cookie"),
        ("guest_access_salt", "session_salt"),
    ],
)
def test_auth_namespace_rejects_duplicate_credentials(
    field: str,
    duplicate_of: str,
) -> None:
    values = _custom_auth_values()
    values[field] = values[duplicate_of]
    with pytest.raises(ValueError):
        AuthNamespace(**values)


def test_custom_session_survives_premint_recovery_logout_and_websocket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_auth = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        user_id = _start_and_adopt(client)
        recovery_path = urlsplit(client.get("/api/recovery-url").json()["recovery_url"]).path

        client.cookies.clear()
        client.cookies.set(
            CUSTOM_AUTH.session_cookie,
            session_auth.sign(user_id, namespace=CUSTOM_AUTH),
        )
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["user"]["id"] == user_id
        assert client.cookies.get("woolroom_session") is None
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "pet_state"

        client.cookies.clear()
        client.cookies.set(
            CUSTOM_AUTH.session_cookie,
            session_auth.sign(user_id),
        )
        assert client.get("/api/me").json()["user"] is None
        with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as ws:
            ws.receive_json()
        assert exc_info.value.code == 1008

        client.cookies.clear()
        recovered = client.get(recovery_path, follow_redirects=False)
        assert recovered.status_code == 303
        assert client.cookies.get(CUSTOM_AUTH.session_cookie)
        assert client.cookies.get("woolroom_session") is None
        assert client.get("/api/me").json()["user"]["id"] == user_id

        logged_out = client.post("/api/logout")
        assert logged_out.status_code == 200
        assert client.cookies.get(CUSTOM_AUTH.session_cookie) is None
        assert logged_out.headers["set-cookie"].startswith(
            f"{CUSTOM_AUTH.session_cookie}="
        )


def test_custom_site_and_guest_credentials_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _load_app(tmp_path, monkeypatch, site_password="open-sesame")

    with TestClient(app) as client:
        granted_guest = client.post("/api/guest-access")
        assert granted_guest.status_code == 200
        guest_token = client.cookies.get(CUSTOM_AUTH.guest_access_cookie)
        assert guest_token
        assert client.cookies.get("woolroom_guest_access") is None
        assert client.get("/api/me").json()["guest"] is True

        client.cookies.clear()
        client.cookies.set(CUSTOM_AUTH.site_access_cookie, guest_token)
        assert client.get("/api/me").status_code == 401
        client.cookies.clear()

        granted_site = client.post(
            "/api/site-access",
            json={"password": "open-sesame"},
        )
        assert granted_site.status_code == 200
        site_token = client.cookies.get(CUSTOM_AUTH.site_access_cookie)
        assert site_token
        assert client.cookies.get("woolroom_site_access") is None
        assert client.cookies.get(CUSTOM_AUTH.guest_access_cookie) is None

        client.cookies.clear()
        client.cookies.set(CUSTOM_AUTH.guest_access_cookie, site_token)
        assert client.get("/api/me").status_code == 401


def test_custom_pending_invite_cookie_is_set_read_and_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _load_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        _start_and_adopt(client)
        invite = client.post("/api/invite")
        assert invite.status_code == 200
        invite_path = urlsplit(invite.json()["url"]).path

        client.cookies.clear()
        joined = client.get(invite_path, follow_redirects=False)
        assert joined.status_code == 303
        assert client.cookies.get(CUSTOM_AUTH.pending_invite_cookie)
        assert client.cookies.get("woolroom_pending_invite") is None

        pending = client.get("/api/me").json()["pending_invite"]
        assert pending is not None
        started = client.post("/api/start", json={"display_name": "Partner"})
        assert started.status_code == 200
        assert started.json()["joined_pet_id"] == pending["pet_id"]
        assert client.cookies.get(CUSTOM_AUTH.pending_invite_cookie) is None
        assert client.cookies.get(CUSTOM_AUTH.session_cookie)


def test_default_cookie_names_remain_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _load_app(
        tmp_path,
        monkeypatch,
        namespace=None,
        site_password="open-sesame",
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/site-access",
            json={"password": "open-sesame"},
        ).status_code == 200
        assert client.cookies.get("woolroom_site_access")

        _start_and_adopt(client)
        assert client.cookies.get("woolroom_session")
        invite_path = urlsplit(client.post("/api/invite").json()["url"]).path

        client.cookies.delete("woolroom_session")
        assert client.get(invite_path, follow_redirects=False).status_code == 303
        assert client.cookies.get("woolroom_pending_invite")

        assert client.post("/api/guest-access").status_code == 200
        assert client.cookies.get("woolroom_guest_access")
