from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path

import pytest

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.data.quirks_catalog import QUIRKS
from app.packs import LOADED_PACKS, PACK_ASSETS, client_pack_assets, load_pack
from app.packs.lint import ERROR, WARN, lint_pack

PIG_PACK = Path(__file__).parent.parent / "packs" / "pig"
MOOD_CELLS = {
    (arousal, valence)
    for arousal in ("low", "med", "high")
    for valence in ("grumpy", "neutral", "content")
}


@pytest.fixture(autouse=True)
def _restore_registries() -> Iterator[None]:
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


def test_pig_pack_lints_strict_clean() -> None:
    report = lint_pack(PIG_PACK)

    assert report.exit_code(strict=True) == 0
    assert report.count(ERROR) == 0
    assert report.count(WARN) == 0
    assert (report.name, report.version) == ("pig", "0.1.0")


def test_pig_pack_registers_public_species_and_assets() -> None:
    record = load_pack(PIG_PACK)

    assert record.name == "pig"
    assert record.species == ["pig"]
    assert record.overlays == ["pig"]
    assert record.quirks == []
    assert record.phrase_languages == {"pig": "en"}

    entry = species_mod.SPECIES_REGISTRY["pig"]
    assert entry["pronoun"] == "it"
    assert entry["phrase_overlay"] == "pig"
    assert species_mod.coats_for("pig") == ("pink", "rose", "truffle")
    assert species_mod.temperament_for("pig")["ignore_rate"] == 0.12

    pig = client_pack_assets()["pig"]
    assert pig["pronoun"] == "it"
    assert set(pig["palettes"]) == {"pink", "rose", "truffle"}
    assert pig["svg"] == PACK_ASSETS["pig"]["figure"]


def test_pig_overlay_has_no_cat_fallthrough_cells() -> None:
    load_pack(PIG_PACK)
    overlay = bl.SPECIES_PHRASE_OVERLAYS["pig"]

    assert set(overlay["body"]) == MOOD_CELLS
    assert set(overlay["action"]) == set(bl.ACTION_LANGUAGE)
    assert all(set(cells) == MOOD_CELLS for cells in overlay["action"].values())
    assert set(overlay["spot"]) == set(bl.PET_SPOT_LANGUAGE)
    assert all(set(cells) == MOOD_CELLS for cells in overlay["spot"].values())
    assert set(overlay["tiny"]) == {"grumpy", "neutral", "content"}

    pinned = len(overlay["body"])
    pinned += sum(len(cells) for cells in overlay["action"].values())
    pinned += sum(len(cells) for cells in overlay["spot"].values())
    pinned += len(overlay["tiny"])
    expected = 9 + 9 * len(bl.ACTION_LANGUAGE) + 9 * len(bl.PET_SPOT_LANGUAGE) + 3
    assert pinned == expected == 120


def test_pig_phrase_and_figure_content_is_distribution_safe() -> None:
    phrase_text = (PIG_PACK / "phrases" / "pig.yaml").read_text(encoding="utf-8")
    figure_text = (PIG_PACK / "species" / "pig.svg").read_text(encoding="utf-8")

    assert phrase_text == phrase_text.lower()
    assert "<!--" not in figure_text
    assert "<metadata" not in figure_text
    assert figure_text.count('id="dog-eyes"') == 1
