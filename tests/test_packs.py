"""Pack loader contract tests (woolroom Phase 1, pack format v1).

Covers `app/packs/loader.py` + `app/packs/sanitize.py`: the happy path
(species/phrases/quirk/voice register and are usable by the engine), every
fail-closed gate (each refuses with its named PackError subclass), the
empty-PACK_PATHS no-op, and the lifespan boot wiring.

Every test that loads a pack mutates the process-global registries
(SPECIES_REGISTRY, SPECIES_PHRASE_OVERLAYS, QUIRKS, CLIENT_VOICE); the
autouse fixture snapshots and restores them in place so the contract tests
that pin those tables (test_species_registry, test_quirk_registry,
test_phrase_golden) see today's exact state.
"""

from __future__ import annotations

import copy
import importlib
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.config import Settings
from app.data.quirks_catalog import QUIRKS
from app.engine import quirks as engine
from app.engine.mood import MoodState
from app.packs import (
    LOADED_PACKS,
    PACK_ASSETS,
    PackCollisionError,
    PackConfinementError,
    PackManifestError,
    PackPhraseError,
    PackQuirkError,
    PackSizeError,
    PackSpeciesError,
    PackSvgError,
    PackVocabError,
    PackVoiceError,
    load_pack,
    load_packs,
)
from app.packs.sanitize import SvgSanitizeError, sanitize_svg
from app.time import utc_now

FIXTURE_PACK = Path(__file__).parent / "fixtures" / "packs" / "pebble"

BUILTIN_QUIRK_IDS = set(QUIRKS)


@pytest.fixture(autouse=True)
def _restore_registries():
    snapshot = (
        copy.deepcopy(species_mod.SPECIES_REGISTRY),
        species_mod.SPECIES,
        copy.deepcopy(QUIRKS),
        copy.deepcopy(bl.SPECIES_PHRASE_OVERLAYS),
        copy.deepcopy(voice_mod.CLIENT_VOICE),
    )
    yield
    species_mod.SPECIES_REGISTRY.clear()
    species_mod.SPECIES_REGISTRY.update(snapshot[0])
    species_mod.SPECIES = snapshot[1]
    QUIRKS.clear()
    QUIRKS.update(snapshot[2])
    bl.SPECIES_PHRASE_OVERLAYS.clear()
    bl.SPECIES_PHRASE_OVERLAYS.update(snapshot[3])
    voice_mod.CLIENT_VOICE.clear()
    voice_mod.CLIENT_VOICE.update(snapshot[4])
    LOADED_PACKS.clear()
    PACK_ASSETS.clear()


def _copy_pack(tmp_path: Path, name: str = "pebble") -> Path:
    dst = tmp_path / name
    shutil.copytree(FIXTURE_PACK, dst)
    return dst


def _write(pack: Path, rel: str, content: str) -> None:
    (pack / rel).write_text(content, encoding="utf-8")


# ────────── happy path ──────────


def test_happy_path_registers_everything() -> None:
    records = load_packs([str(FIXTURE_PACK)])

    (record,) = records
    assert record.name == "pebble"
    assert record.version == "0.1.0"
    assert record.fx_vocab_version == 1
    assert record.species == ["pebble"]
    assert record.quirks == ["sunbather"]
    assert record.overlays == ["pebble"]
    assert record.phrase_languages == {"pebble": "en"}
    assert LOADED_PACKS == [record]

    # Species registry.
    assert species_mod.SPECIES == ("cat", "pebble")
    entry = species_mod.SPECIES_REGISTRY["pebble"]
    assert entry["phrase_overlay"] == "pebble"
    assert entry["pronoun"] == "it"
    assert species_mod.coats_for("pebble") == ("gray",)
    assert species_mod.temperament_for("pebble")["breed_archetype"] == "garden pebble"
    assert species_mod.temperament_for("pebble")["ignore_rate"] == 0.9

    # Phrase overlay + fallthrough: pinned cells serve pebble lines, cells
    # the overlay doesn't carry fall through to the shared tables. The
    # process-local repeat guard is cleared first so the pins are
    # independent of which pebble cells earlier tests happened to serve.
    bl._last_served.clear()
    overlay = bl.SPECIES_PHRASE_OVERLAYS["pebble"]
    assert set(overlay) == {"body", "action", "spot", "tiny"}
    assert overlay["body"][("low", "content")] == [
        "*does nothing, beautifully*",
        "*radiates mineral calm*",
    ]
    assert bl.fallback_phrase(0, 80, species="pebble", event_id=0) == "*does nothing, beautifully*"
    assert (
        bl.fallback_phrase(50, 20, species="pebble", event_id=1)
        in bl.BODY_LANGUAGE[("med", "grumpy")]
    )

    # Quirk registered and drivable through the engine interpreter.
    assert "sunbather" in QUIRKS
    now = utc_now()
    old = MoodState(arousal=50, valence=70, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=52, valence=72, animation_state="sitting", last_drift_at=now)
    effect = engine.get_action_quirk_effect("pet", old, new, ["sunbather"], facts={}, now=now)
    assert effect is not None
    assert effect.text == "*accepts your warmth and, being a rock, returns none of it*"
    assert effect.scene_fx == {"mode": "petting", "duration_ms": 1500}
    assert effect.valence_delta == 1
    # ... and its `when` gate actually gates: a cold pebble stays silent.
    cold = MoodState(arousal=50, valence=30, animation_state="sitting", last_drift_at=now)
    assert engine.get_action_quirk_effect("pet", cold, cold, ["sunbather"], now=now) is None

    # Voice merge: pack keys added, builtin nested tables intact, registry
    # coat lists synced for the new species.
    assert voice_mod.CLIENT_VOICE["coat_labels"]["gray"] == "river gray"
    assert voice_mod.CLIENT_VOICE["coat_labels"]["marmalade"] == "marmalade"
    assert "content_sigher" in voice_mod.CLIENT_VOICE["quirks"]["previews"]
    assert voice_mod.CLIENT_VOICE["quirks"]["previews"]["sunbather"].startswith("finds the one")
    assert voice_mod.CLIENT_VOICE["quirks"]["moods"]["sunbather"] == "warm rock"
    assert voice_mod.CLIENT_VOICE["coats"]["pebble"] == ["gray"]
    assert voice_mod.CLIENT_VOICE["coats"]["cat"] == ["tuxedo", "marmalade", "ash"]

    # Pack assets held for the (next-slice) client delivery.
    assets = PACK_ASSETS["pebble"]
    assert assets["pack"] == "pebble"
    assert assets["coats"] == {"gray": {"body": "#9a9a94", "belly": "#cfcfc8", "point": "#77776f"}}
    assert assets["geometry"]["tail"] == {"yAbove": 444, "xAbove": 238}
    assert assets["figure"].startswith("<g>")
    assert 'class="tailg"' in assets["figure"]


def test_empty_pack_paths_is_a_noop() -> None:
    assert load_packs([]) == []
    assert species_mod.SPECIES == ("cat",)
    assert set(QUIRKS) == BUILTIN_QUIRK_IDS
    assert set(bl.SPECIES_PHRASE_OVERLAYS) == set()
    assert LOADED_PACKS == []


def test_pack_paths_env_parses_comma_separated(monkeypatch) -> None:
    monkeypatch.setenv("PACK_PATHS", "/a, /b ,, /c ")
    assert Settings(_env_file=None).pack_paths == ["/a", "/b", "/c"]
    monkeypatch.delenv("PACK_PATHS")
    assert Settings(_env_file=None).pack_paths == []


# ────────── boot wiring (lifespan loads packs before serving) ──────────


class _DummyScheduler:
    def shutdown(self, wait: bool = False) -> None:
        return None


def _boot_app(tmp_path: Path, monkeypatch, pack_paths_env: str | None):
    """Fresh app modules + lifespan, mirroring test_app_flows._load_app."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/woolroom-test.db")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("ENV", "dev")
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


def test_lifespan_loads_packs_from_pack_paths(tmp_path: Path, monkeypatch) -> None:
    main = _boot_app(tmp_path, monkeypatch, str(FIXTURE_PACK))
    calls = []
    monkeypatch.setattr(main, "load_packs", lambda paths: calls.append(list(paths)) or [])
    with TestClient(main.create_app()):
        pass
    assert calls == [[str(FIXTURE_PACK)]]


def test_lifespan_default_pack_paths_is_empty_noop(tmp_path: Path, monkeypatch) -> None:
    main = _boot_app(tmp_path, monkeypatch, None)
    calls = []
    monkeypatch.setattr(main, "load_packs", lambda paths: calls.append(list(paths)) or [])
    with TestClient(main.create_app()):
        pass
    assert calls == [[]]


def test_lifespan_refuses_to_boot_on_a_gate_violation(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "not-a-pack"
    bad.mkdir()
    main = _boot_app(tmp_path, monkeypatch, str(bad))
    # _boot_app reimports app.* fresh; the lifespan's PackManifestError is the
    # FRESH module's class, not the one this test file imported at collection.
    fresh_loader = sys.modules["app.packs.loader"]
    with pytest.raises(fresh_loader.PackManifestError, match="pack.yaml is required"):
        with TestClient(main.create_app()):
            pass


# ────────── manifest + vocabulary gates ──────────


def test_manifest_is_required(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "pack.yaml").unlink()
    with pytest.raises(PackManifestError, match="pack.yaml is required"):
        load_pack(pack)


@pytest.mark.parametrize(
    "manifest",
    [
        "name: pebble\nversion: 0.1.0\nauthor: t\nfx_vocab_version: 1\n",  # no license
        "name: pebble\nversion: 0.1.0\nauthor: t\nlicense: MIT\n",  # no fx vocab
        "name: pebble\nversion: 0.1.0\nauthor: t\nlicense: MIT\nfx_vocab_version: '1'\n",
        "name: pebble\nversion: 0.1.0\nauthor: t\nlicense: MIT\nfx_vocab_version: -1\n",
        "name: ''\nversion: 0.1.0\nauthor: t\nlicense: MIT\nfx_vocab_version: 1\n",
        "name: pebble\nversion: 0.1.0\nauthor: t\nlicense: MIT\nfx_vocab_version: 1\nhomepage: x\n",
    ],
    ids=[
        "missing-license",
        "missing-fx-vocab",
        "fx-vocab-string",
        "fx-vocab-negative",
        "empty-name",
        "unknown-key",
    ],
)
def test_manifest_field_gates(tmp_path: Path, manifest: str) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "pack.yaml", manifest)
    with pytest.raises(PackManifestError):
        load_pack(pack)


def test_fx_vocab_newer_than_engine_is_refused_loudly(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(
        pack,
        "pack.yaml",
        "name: pebble\nversion: 0.1.0\nauthor: t\nlicense: MIT\nfx_vocab_version: 99\n",
    )
    with pytest.raises(PackVocabError, match="fx vocabulary v99"):
        load_pack(pack)


def test_manifest_must_be_safe_yaml(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "pack.yaml", "!!python/object/new:os.system ['echo hi']\n")
    with pytest.raises(PackManifestError, match="not valid \\(safe\\) YAML"):
        load_pack(pack)


def test_manifest_must_be_a_mapping(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "pack.yaml", "- just\n- a\n- list\n")
    with pytest.raises(PackManifestError, match="must be a YAML mapping"):
        load_pack(pack)


# ────────── confinement + size gates ──────────


def test_pack_dir_must_exist() -> None:
    with pytest.raises(PackConfinementError, match="does not exist"):
        load_pack("/no/such/pack/dir")


def test_pack_path_must_be_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")
    with pytest.raises(PackConfinementError, match="not a directory"):
        load_pack(not_a_dir)


def test_symlinked_pack_file_is_refused(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("temperament: {}\n")
    target = pack / "species" / "pebble.yaml"
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(PackConfinementError, match="symlink refused"):
        load_pack(pack)


def test_symlinked_pack_subdir_is_refused(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    real_dir = tmp_path / "real-quirks"
    real_dir.mkdir()
    shutil.copy(pack / "quirks" / "sunbather.yaml", real_dir / "sunbather.yaml")
    shutil.rmtree(pack / "quirks")
    (pack / "quirks").symlink_to(real_dir)
    with pytest.raises(PackConfinementError, match="symlink refused"):
        load_pack(pack)


def test_per_file_size_cap(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "species/pebble.svg", "<g>" + " " * (256 * 1024) + "</g>")
    with pytest.raises(PackSizeError, match="cap 262144"):
        load_pack(pack)


def test_per_pack_size_cap(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    filler = (
        "label: Filler\ndescription: filler\ncomplexity: low\nbehavior:\n"
        "  pose:\n    - when: {state_in: [sitting]}\n      write: {body_lean: 1}\n"
        "# " + "x" * (210 * 1024) + "\n"
    )
    for i in range(5):  # 5 × ~210KB, each under the per-file cap, over 1MB together
        _write(pack, f"quirks/filler_{i}.yaml", filler)
    with pytest.raises(PackSizeError, match="exceeds the 1048576-byte cap"):
        load_pack(pack)


# ────────── SVG gates (sanitize + confinement of art) ──────────


def test_svg_gate_strips_the_dangerous_and_keeps_the_art(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(
        pack,
        "species/pebble.svg",
        """<g onload="alert(1)" onclick="x()">
  <script>alert(1)</script>
  <foreignObject><div>html</div></foreignObject>
  <image href="https://evil.example/x.png"/>
  <use xlink:href="https://evil.example/x.svg#y" xmlns:xlink="http://www.w3.org/1999/xlink"/>
  <a href="https://evil.example"><rect width="1" height="1"/></a>
  <text>not in the allowlist</text>
  <circle cx="1" cy="2" r="3" style="fill: url(https://evil.example)" fill="#9a9a94"/>
  <ellipse cx="4" cy="5" rx="6" ry="7" fill="#cfcfc8" style="stroke-width: 2"/>
</g>""",
    )
    load_pack(pack)
    figure = PACK_ASSETS["pebble"]["figure"]
    for banned in (
        "<script",
        "foreignObject",
        "<image",
        "<use",
        "<a ",
        "onload",
        "onclick",
        "href",
        "url(",
        "<text",
    ):
        assert banned not in figure
    assert 'r="3"' in figure  # the circle survives, minus its style
    assert "stroke-width: 2" in figure  # harmless style survives
    assert figure.startswith("<g>")


def test_svg_unparseable_is_rejected(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "species/pebble.svg", "<g><unclosed></g>")
    with pytest.raises(PackSvgError, match="does not parse as XML"):
        load_pack(pack)


def test_svg_root_must_be_a_fragment(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "species/pebble.svg", "<html><body>x</body></html>")
    with pytest.raises(PackSvgError, match="root must be a single <g>"):
        load_pack(pack)


def test_sanitize_svg_unit() -> None:
    assert sanitize_svg("<g><circle r='1'/></g>") == '<g><circle r="1" /></g>'
    with pytest.raises(SvgSanitizeError):
        sanitize_svg("not xml at all <<<")
    # Namespaced documents collapse to local names (no xmlns in the output).
    out = sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><g><rect width="2" height="2"/></g></svg>'
    )
    assert out == '<svg><g><rect width="2" height="2" /></g></svg>'


# ────────── species gates ──────────


def _mutate_yaml(pack: Path, rel: str, mutate) -> None:
    """Load one pack yaml file, apply `mutate(dict)`, write it back."""
    path = pack / rel
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_species_temperament_must_mirror_the_registry_shape(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["temperament"].pop("ignore_rate"))
    with pytest.raises(PackSpeciesError, match="missing keys.*ignore_rate"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(
        pack, "species/pebble.yaml", lambda d: d["temperament"]["traits"].pop("stubbornness")
    )
    with pytest.raises(PackSpeciesError, match="missing keys.*stubbornness"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "c")
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["temperament"].update(ignore_rate=1.5))
    with pytest.raises(PackSpeciesError, match="ignore_rate.*\\[0, 1\\]"):
        load_pack(pack)


def test_species_coats_are_hex_validated(tmp_path: Path) -> None:
    for i, bad_color in enumerate(("#fff", "gray", "#9a9a9")):
        pack = _copy_pack(tmp_path / f"p{i}")
        _mutate_yaml(
            pack,
            "species/pebble.yaml",
            lambda d: d["coats"]["gray"].update(body=bad_color),
        )
        with pytest.raises(PackSpeciesError, match="#rrggbb"):
            load_pack(pack)

    pack = _copy_pack(tmp_path / "extra")
    _mutate_yaml(
        pack,
        "species/pebble.yaml",
        lambda d: d["coats"]["gray"].update(outline="#000000"),
    )
    with pytest.raises(PackSpeciesError, match="unknown keys.*outline"):
        load_pack(pack)


def test_species_geometry_must_mirror_species_geometry(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["geometry"].pop("belly"))
    with pytest.raises(PackSpeciesError, match="missing keys.*belly"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["geometry"]["tail"].pop("xAbove"))
    with pytest.raises(PackSpeciesError, match="missing keys.*xAbove"):
        load_pack(pack)


def test_species_id_must_fit_the_storage_column(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    shutil.copy(pack / "species" / "pebble.yaml", pack / "species" / "1rock.yaml")
    with pytest.raises(PackSpeciesError, match="must match"):
        load_pack(pack)


def test_species_needs_exactly_one_figure(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "species" / "pebble.svg").unlink()
    with pytest.raises(PackSpeciesError, match="missing its figure art"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    shutil.copy(pack / "species" / "pebble.svg", pack / "species" / "rock.svg")
    with pytest.raises(PackSpeciesError, match="no matching species yaml"):
        load_pack(pack)


def test_species_id_collision_with_builtin_is_a_boot_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    shutil.copy(pack / "species" / "pebble.yaml", pack / "species" / "cat.yaml")
    shutil.copy(pack / "species" / "pebble.svg", pack / "species" / "cat.svg")
    with pytest.raises(PackCollisionError, match="'cat' collides"):
        load_pack(pack)


def test_species_collision_across_packs_is_a_boot_error(tmp_path: Path) -> None:
    first = _copy_pack(tmp_path, "first")
    second = _copy_pack(tmp_path, "second")
    with pytest.raises(PackCollisionError, match="'pebble' collides"):
        load_packs([str(first), str(second)])


# ────────── quirk gates ──────────


def test_quirk_conditions_stay_inside_the_grammar(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(
        pack,
        "quirks/sunbather.yaml",
        lambda d: d["behavior"]["action"][0]["when"].update(flies=True),
    )
    with pytest.raises(PackQuirkError, match="outside the grammar.*flies"):
        load_pack(pack)


def test_quirk_scene_fx_modes_stay_inside_the_vocabulary(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(
        pack,
        "quirks/sunbather.yaml",
        lambda d: d["behavior"]["action"][0].update(scene_fx={"mode": "laser_show"}),
    )
    with pytest.raises(PackQuirkError, match="'laser_show' is not in the fx vocabulary"):
        load_pack(pack)


def test_quirk_channels_pose_writes_and_ops_are_pinned(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(
        pack,
        "quirks/sunbather.yaml",
        lambda d: d["behavior"].update(dance=[{"when": {"state_in": ["sitting"]}, "text": "*x*"}]),
    )
    with pytest.raises(PackQuirkError, match="unknown behavior channels.*dance"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(
        pack,
        "quirks/sunbather.yaml",
        lambda d: d["behavior"].update(
            pose=[{"when": {"state_in": ["sitting"]}, "write": {"wings": 1}}]
        ),
    )
    with pytest.raises(PackQuirkError, match="unknown rig key 'wings'"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "c")
    _mutate_yaml(
        pack,
        "quirks/sunbather.yaml",
        lambda d: d["behavior"].update(
            pose=[{"when": {"state_in": ["sitting"]}, "write": {"body_lean": {"max": 3}}}]
        ),
    )
    with pytest.raises(PackQuirkError, match="unknown pose write ops.*max"):
        load_pack(pack)


def test_quirk_shape_gates(tmp_path: Path) -> None:
    # action rules need text
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "quirks/sunbather.yaml", lambda d: d["behavior"]["action"][0].pop("text"))
    with pytest.raises(PackQuirkError, match="'text' must be a non-empty string"):
        load_pack(pack)

    # complexity is the catalog's low/medium/high
    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(pack, "quirks/sunbather.yaml", lambda d: d.update(complexity="galactic"))
    with pytest.raises(PackQuirkError, match="'complexity' must be one of"):
        load_pack(pack)

    # no unknown top-level keys
    pack = _copy_pack(tmp_path / "c")
    _mutate_yaml(pack, "quirks/sunbather.yaml", lambda d: d.update(script="definitely not code"))
    with pytest.raises(PackQuirkError, match="unknown keys.*script"):
        load_pack(pack)

    # unsafe yaml never constructs objects
    pack = _copy_pack(tmp_path / "d")
    _write(pack, "quirks/evil.yaml", "!!python/object/new:os.system ['echo hi']\n")
    with pytest.raises(PackQuirkError, match="not valid \\(safe\\) YAML"):
        load_pack(pack)


def test_quirk_id_collision_with_builtin_is_a_boot_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    shutil.copy(pack / "quirks" / "sunbather.yaml", pack / "quirks" / "content_sigher.yaml")
    with pytest.raises(PackCollisionError, match="'content_sigher' collides"):
        load_pack(pack)


# ────────── phrase gates ──────────


def test_phrase_buckets_are_the_phrasebook_buckets(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["body"].update(stunned={"content": ["*x*"]}),
    )
    with pytest.raises(PackPhraseError, match="unknown arousal bucket 'stunned'"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["tiny"].update(ecstatic=["!"]),
    )
    with pytest.raises(PackPhraseError, match="unknown valence bucket 'ecstatic'"):
        load_pack(pack)


def test_phrase_action_and_spot_keys_are_known(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["action"].update(dance={"low": {"content": ["*x*"]}}),
    )
    with pytest.raises(PackPhraseError, match="unknown action key 'dance'"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["spot"].update(wing={"low": {"content": ["*x*"]}}),
    )
    with pytest.raises(PackPhraseError, match="unknown spot key 'wing'"):
        load_pack(pack)


def test_phrase_lines_are_non_empty_prose_capped_lists(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "phrases/pebble.yaml", lambda d: d["tiny"].update(content=[]))
    with pytest.raises(PackPhraseError, match="non-empty list of phrase lines"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["body"]["low"].update(content=["x" * 501]),
    )
    with pytest.raises(PackPhraseError, match="prose cap"):
        load_pack(pack)


def test_phrase_overlay_must_target_a_known_species(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    shutil.copy(pack / "phrases" / "pebble.yaml", pack / "phrases" / "rock.yaml")
    with pytest.raises(PackPhraseError, match="unknown species 'rock'"):
        load_pack(pack)


def test_phrase_overlay_needs_at_least_one_table(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "phrases/pebble.yaml", "language: en\n")
    with pytest.raises(PackPhraseError, match="carries no phrase tables"):
        load_pack(pack)


def test_phrase_overlay_collision_is_a_boot_error(tmp_path: Path) -> None:
    # An overlay key is claimed the moment a pack registers it: a second pack
    # whose phrases/ targets the SAME (now pack-registered) species trips the
    # collision gate — packs add voices, they never restate one. The builtin
    # cat carries no overlay, so the collision has to come from another pack.
    first = _copy_pack(tmp_path, "first")
    second = _copy_pack(tmp_path, "second")
    # Give the second pack its own species (rock) so the SPECIES collision
    # gate stays quiet and the overlay gate is the one that fires.
    (second / "species" / "pebble.yaml").rename(second / "species" / "rock.yaml")
    (second / "species" / "pebble.svg").rename(second / "species" / "rock.svg")
    with pytest.raises(PackCollisionError, match="overlay for 'pebble' already exists"):
        load_packs([str(first), str(second)])


# ────────── voice gates ──────────


def test_voice_yaml_must_be_plain_data(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write(pack, "voice.yaml", "- just\n- a\n- list\n")
    with pytest.raises(PackVoiceError, match="must be a YAML mapping"):
        load_pack(pack)

    pack = _copy_pack(tmp_path / "b")
    _write(pack, "voice.yaml", "coat_labels:\n  1: numeric key\n")
    with pytest.raises(PackVoiceError, match="keys must be strings"):
        load_pack(pack)
