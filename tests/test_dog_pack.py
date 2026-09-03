from __future__ import annotations

import copy
from pathlib import Path

import pytest

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.data.quirks_catalog import QUIRKS
from app.packs import LOADED_PACKS, PACK_ASSETS, client_pack_assets, load_pack
from app.packs.lint import ERROR, WARN, lint_pack

DOG_PACK = Path(__file__).parent.parent / "packs" / "dog"
CELLS = {
    (arousal, valence)
    for arousal in ("low", "med", "high")
    for valence in ("grumpy", "neutral", "content")
}
ACTIONS = {"call", "feed", "greet", "message", "pet", "play", "walk"}
SPOTS = {"belly", "body", "ear", "head", "tail"}


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


def test_dog_pack_lints_strict_clean() -> None:
    report = lint_pack(DOG_PACK)

    assert report.count(ERROR) == 0
    assert report.count(WARN) == 0
    assert report.exit_code(strict=True) == 0
    assert (report.name, report.version) == ("dog", "0.1.0")


def test_dog_pack_registers_a_complete_public_profile() -> None:
    record = load_pack(DOG_PACK)

    assert record.name == "dog"
    assert record.species == ["dog"]
    assert record.overlays == ["dog"]
    assert record.quirks == []
    assert record.phrase_languages == {"dog": "en"}
    assert species_mod.coats_for("dog") == ("red", "sesame", "black_tan", "cream")
    assert species_mod.pronoun_for("dog") == "it"
    temperament = species_mod.temperament_for("dog")
    assert temperament["breed_archetype"] == "independent companion dog"
    assert temperament["ignore_rate"] == 0.33

    overlay = bl.SPECIES_PHRASE_OVERLAYS["dog"]
    assert set(overlay["body"]) == CELLS
    assert set(overlay["action"]) == ACTIONS
    assert all(set(cells) == CELLS for cells in overlay["action"].values())
    assert set(overlay["spot"]) == SPOTS
    assert all(set(cells) == CELLS for cells in overlay["spot"].values())
    assert set(overlay["tiny"]) == {"grumpy", "neutral", "content"}
    assert all(any(not line.startswith("*") for line in lines) for lines in overlay["tiny"].values())


def test_dog_pack_exposes_generic_rig_assets() -> None:
    load_pack(DOG_PACK)

    dog = client_pack_assets()["dog"]
    assert dog["pronoun"] == "it"
    assert set(dog["palettes"]) == {"red", "sesame", "black_tan", "cream"}
    assert dog["palettes"]["red"] == {
        "body": "#b9673f",
        "belly": "#ead0ac",
        "point": "#4b352c",
    }
    assert set(dog["geometry"]) == {"earBelow", "headBelow", "tail", "belly"}
    assert dog["svg"] == PACK_ASSETS["dog"]["figure"]
    assert "<!--" not in dog["svg"]
    assert "<title" not in dog["svg"]
    assert "<desc" not in dog["svg"]
