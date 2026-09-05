from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from woolroom import DEFAULT_SITE_IDENTITY, SiteIdentity, create_app


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0"/></svg>'
PNG = b"\x89PNG\r\n\x1a\nsynthetic"


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: SiteIdentity,
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-site-identity-secret")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", "private-word")
    monkeypatch.setenv("GUEST_ACCESS_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main.create_app(site_identity=identity)


def test_direct_hosting_uses_the_public_identity_default() -> None:
    app = create_app()
    assert app.title == "woolroom"
    assert app.state.site_identity is DEFAULT_SITE_IDENTITY
    assert app.state.site_asset_version.endswith(DEFAULT_SITE_IDENTITY.asset_version)


def test_direct_hosting_versions_bundled_assets_with_the_core_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _load_app(tmp_path, monkeypatch, DEFAULT_SITE_IDENTITY)
    version = app.state.site_asset_version

    with TestClient(app) as client:
        assert client.post("/api/guest-access").status_code == 200
        index = client.get("/")
        assert f"/static/favicon.svg?v={version}" in index.text
        assert f"/static/manifest.json?v={version}" in index.text
        icon = client.get(f"/static/favicon.svg?v={version}")
        assert icon.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_custom_identity_renders_escaped_copy_and_exact_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = SiteIdentity(
        name="Paws & Co.",
        description="one quiet dog < shared",
        access_heading="the wool room",
        access_note="a little dog lives here",
        guest_entry_label="step into the room",
        guest_disclosure="read-only · a fictional household",
        favicon_svg=SVG,
        apple_touch_icon_png=PNG,
    )
    app = _load_app(tmp_path, monkeypatch, identity)
    version = app.state.site_asset_version

    with TestClient(app) as client:
        access = client.get("/access")
        assert access.status_code == 200
        assert "Paws &amp; Co." in access.text
        assert "one quiet dog &lt; shared" in access.text
        assert "the wool room" in access.text
        assert "step into the room" in access.text
        assert "read-only · a fictional household" in access.text
        assert "{{SITE_" not in access.text

        assert client.post("/api/guest-access").status_code == 200
        index = client.get("/")
        assert index.status_code == 200
        assert "<title>Paws &amp; Co.</title>" in index.text
        assert "one quiet dog &lt; shared" in index.text
        assert "fresh Paws &amp; Co. — tap to refresh" in index.text
        assert f"/static/favicon.svg?v={version}" in index.text
        assert f"/static/manifest.json?v={version}" in index.text
        assert "{{SITE_" not in index.text

        favicon = client.get("/static/favicon.svg")
        apple_icon = client.get("/static/apple-touch-icon.png")
        manifest = client.get("/static/manifest.json")
        assert favicon.content == SVG
        assert apple_icon.content == PNG
        assert favicon.headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert manifest.headers["content-type"].startswith("application/manifest+json")
        assert manifest.json()["name"] == "Paws & Co."
        assert manifest.json()["description"] == "one quiet dog < shared"
        assert len(manifest.json()["icons"]) == 2
        assert all(version in icon["src"] for icon in manifest.json()["icons"])

        versioned_favicon = client.get(
            f"/static/favicon.svg?v={version}"
        )
        versioned_manifest = client.get(
            f"/static/manifest.json?v={version}"
        )
        assert versioned_favicon.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert versioned_manifest.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )


def test_identity_copy_is_not_reinterpreted_as_template_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = SiteIdentity(
        name="{{SITE_DESCRIPTION}}",
        guest_disclosure="literal __GUEST_OPEN__ marker",
    )
    app = _load_app(tmp_path, monkeypatch, identity)

    with TestClient(app) as client:
        access = client.get("/access")
        assert "{{SITE_DESCRIPTION}}" in access.text
        assert "literal __GUEST_OPEN__ marker" in access.text


@pytest.mark.parametrize(
    "identity",
    [
        lambda: SiteIdentity(name=""),
        lambda: SiteIdentity(name=" padded "),
        lambda: SiteIdentity(description="line\nbreak"),
        lambda: SiteIdentity(favicon_svg=SVG),
        lambda: SiteIdentity(favicon_svg=b"not svg", apple_touch_icon_png=PNG),
        lambda: SiteIdentity(favicon_svg=SVG, apple_touch_icon_png=b"not png"),
    ],
)
def test_identity_refuses_invalid_text_and_assets(identity) -> None:
    with pytest.raises(ValueError):
        identity()


@pytest.mark.parametrize(
    "svg",
    [
        b"<svg><script>alert(1)</script></svg>",
        b'<svg onload="alert(1)"><path d="M0 0"/></svg>',
        b'<svg><foreignObject><div>active html</div></foreignObject></svg>',
        b'<svg><a href="https://example.com"><path d="M0 0"/></a></svg>',
        b'<svg><path style="fill:url(https://example.com/pixel)"/></svg>',
        b'<?xml-stylesheet href="https://example.com/active.css"?><svg/>',
    ],
)
def test_identity_refuses_active_svg(svg: bytes) -> None:
    with pytest.raises(ValueError):
        SiteIdentity(favicon_svg=svg, apple_touch_icon_png=PNG)
