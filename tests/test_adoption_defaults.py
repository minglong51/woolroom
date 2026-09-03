from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from woolroom import AdoptionDefaults, create_app

FIXTURE_PACK = Path(__file__).parent / "fixtures" / "packs" / "pebble"


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _load_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pack_paths: str = "",
    adoption_defaults: AdoptionDefaults | None = None,
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'woolroom.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SITE_PASSWORD", "")
    monkeypatch.setenv("ADOPT_ALLOWLIST", "")
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.setenv("OPEN_SIGNUP", "true")
    monkeypatch.setenv("PACK_PATHS", pack_paths)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)

    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_scheduler", lambda: _DummyScheduler())
    return main.create_app(adoption_defaults=adoption_defaults)


def _join_partner(client: TestClient) -> None:
    owner_cookies = dict(client.cookies)
    invite = client.post("/api/invite")
    assert invite.status_code == 200
    client.cookies.clear()
    joined = client.get(urlparse(invite.json()["url"]).path, follow_redirects=False)
    assert joined.status_code == 303
    assert client.post("/api/start", json={"display_name": "Wren"}).status_code == 200
    client.cookies.clear()
    client.cookies.update(owner_cookies)


def test_public_defaults_are_cat_shaped() -> None:
    assert AdoptionDefaults().client_payload() == {
        "primary": {"species": "cat", "coat": "marmalade"},
        "secondary": {"species": "cat", "coat": "marmalade"},
    }

    configured = AdoptionDefaults(primary_coat="ash")
    assert create_app(adoption_defaults=configured).state.adoption_defaults is configured


def test_environment_maps_to_the_typed_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADOPT_PRIMARY_SPECIES", "pebble")
    monkeypatch.setenv("ADOPT_PRIMARY_COAT", "gray")
    monkeypatch.setenv("ADOPT_SECONDARY_SPECIES", "cat")
    monkeypatch.setenv("ADOPT_SECONDARY_COAT", "ash")
    configured = Settings(_env_file=None)
    assert configured.adoption_defaults == AdoptionDefaults(
        primary_species="pebble",
        primary_coat="gray",
        secondary_species="cat",
        secondary_coat="ash",
    )


def test_boot_validates_composition_defaults_against_loaded_packs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = AdoptionDefaults(primary_species="missing", primary_coat="gray")
    app = _load_app(tmp_path, monkeypatch, adoption_defaults=invalid)

    with (
        pytest.raises(ValueError, match="primary adoption species 'missing' is not registered"),
        TestClient(app),
    ):
        pass


def test_bundled_profiles_need_no_pack_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = AdoptionDefaults(
        primary_species="dog",
        primary_coat="red",
        secondary_species="pig",
        secondary_coat="rose",
    )
    app = _load_app(tmp_path, monkeypatch, adoption_defaults=configured)

    with TestClient(app) as client:
        assert client.get("/api/adoption-defaults").json() == configured.client_payload()
        assert set(client.get("/api/packs").json()) == {"dog", "pig"}


def test_composition_controls_both_adoptions_and_public_client_dto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = AdoptionDefaults(
        primary_species="pebble",
        primary_coat="gray",
        secondary_species="cat",
        secondary_coat="ash",
    )
    app = _load_app(
        tmp_path,
        monkeypatch,
        pack_paths=str(FIXTURE_PACK),
        adoption_defaults=configured,
    )

    with TestClient(app) as owner:
        defaults = owner.get("/api/adoption-defaults")
        assert defaults.status_code == 200
        assert defaults.json() == configured.client_payload()

        assert owner.post("/api/start", json={"display_name": "Ash"}).status_code == 200
        refused_first = owner.post(
            "/api/adopt",
            json={
                "name": "River",
                "quirks": ["content_sigher", "lean_in_greeter"],
                "species": "cat",
            },
        )
        assert refused_first.status_code == 422

        first = owner.post(
            "/api/adopt",
            json={"name": "River", "quirks": ["content_sigher", "lean_in_greeter"]},
        )
        assert first.status_code == 200
        assert first.json()["pet"]["species"] == "pebble"
        assert first.json()["pet"]["coat"] == "gray"

        _join_partner(owner)
        refused = owner.post(
            "/api/adopt-second",
            json={
                "name": "Moss",
                "quirk": "content_sigher",
                "species": "pebble",
                "coat": "gray",
            },
        )
        assert refused.status_code == 422

        second = owner.post(
            "/api/adopt-second",
            json={"name": "Moss", "quirk": "content_sigher"},
        )
        assert second.status_code == 200
        assert second.json()["pet"]["species"] == "cat"
        assert second.json()["pet"]["coat"] == "ash"


def test_client_uses_the_public_defaults_for_species_and_coats() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    api_uri = (repo_root / "app" / "static" / "js" / "api.js").as_uri()
    figures_uri = (repo_root / "app" / "static" / "js" / "figures.js").as_uri()
    quirks_uri = (repo_root / "app" / "static" / "js" / "quirks.js").as_uri()
    script = f"""
      import {{ apiMethods }} from {json.dumps(api_uri)};
      import {{ figureMethods }} from {json.dumps(figures_uri)};
      import {{ quirkMethods }} from {json.dumps(quirks_uri)};
      globalThis.fetch = async (url) => ({{
        ok: true,
        json: async () => ({{
          primary: {{ species: "dog", coat: "red" }},
          secondary: {{ species: "pig", coat: "rose" }},
        }}),
      }});
      const context = {{
        adoptionDefaults: {{
          primary: {{ species: "cat", coat: "marmalade" }},
          secondary: {{ species: "cat", coat: "marmalade" }},
        }},
        pickedCoat: "marmalade",
        secondCoat: "marmalade",
        pet: null,
        packs: {{
          dog: {{
            palettes: {{
              red: {{ body: "#b9673f", belly: "#ead0ac", point: "#4b352c" }},
              cream: {{ body: "#dbc7a5", belly: "#f2e5ca", point: "#766354" }},
            }},
          }},
          pig: {{
            palettes: {{
              pink: {{ body: "#e9aab4", belly: "#f7d7da", point: "#c87987" }},
              rose: {{ body: "#c9828c", belly: "#eabfc3", point: "#a95f6b" }},
            }},
          }},
        }},
        voice: {{
          coats: {{ dog: ["red", "cream"], pig: ["pink", "rose"] }},
          coat_labels: {{ red: "red", cream: "cream", pink: "pink", rose: "rose" }},
        }},
        ...figureMethods,
        ...quirkMethods,
      }};
      await apiMethods.loadAdoptionDefaults.call(context);
      const result = {{
        primarySpecies: context.primaryAdoptionSpecies(),
        secondarySpecies: context.secondaryAdoptionSpecies(),
        primaryCoats: context.coatOptions().map((coat) => coat.id),
        secondaryCoats: context.secondCoatOptions().map((coat) => coat.id),
        pickedCoat: context.pickedCoat,
        secondCoat: context.secondCoat,
        redSwatch: context.previewCoatStyle("dog", "red"),
        roseSwatch: context.previewCoatStyle("pig", "rose"),
      }};
      process.stdout.write(JSON.stringify(result));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "primarySpecies": "dog",
        "secondarySpecies": "pig",
        "primaryCoats": ["red", "cream"],
        "secondaryCoats": ["pink", "rose"],
        "pickedCoat": "red",
        "secondCoat": "rose",
        "redSwatch": "--dog-body:#b9673f; --dog-belly:#ead0ac; --dog-point:#4b352c",
        "roseSwatch": "--dog-body:#c9828c; --dog-belly:#eabfc3; --dog-point:#a95f6b",
    }

    index = (repo_root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert ':style="previewCoatStyle(primaryAdoptionSpecies(), c.id)"' in index
    assert (
        ':style="previewCoatStyle(pet?.species || primaryAdoptionSpecies(), c.id)"'
        in index
    )
    assert ':style="previewCoatStyle(secondaryAdoptionSpecies(), c.id)"' in index

    style = (repo_root / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert '.landing-room[data-coat="ash"]' in style
    for palette_class in ("coat", "cream", "point"):
        assert f".landing-room .{palette_class}" in style
