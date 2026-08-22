"""The pebble pack (packs/pebble) — the shipped example content pack.

Pins that the shipped pack passes every loader gate and registers what it
promises: the pebble species (temperament/coats/geometry/pronoun), its sparse
phrase overlay with designed fallthrough to the base tables, the sunbather
quirk (drivable through the engine interpreter), the voice additions, and the
figure assets held for client delivery — including the wool-rig class contract
the figure must keep (`.tailg`/`.headg`/`.earg`/`#dog-eyes`/…) so the room
animates it unchanged. (The cat is the builtin species, so the example pack is
a rock: the minimal shape that still exercises every pack surface.)

Same registry-isolation hygiene as tests/test_packs.py: loading a pack
mutates the process-global registries, so the autouse fixture snapshots and
restores them around every test here.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.data.quirks_catalog import QUIRKS
from app.engine import quirks as engine
from app.engine.mood import MoodState
from app.packs import LOADED_PACKS, PACK_ASSETS, client_pack_assets, load_pack
from app.time import utc_now

PEBBLE_PACK = Path(__file__).parent.parent / "packs" / "pebble"


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


def test_pebble_loads_clean_and_registers() -> None:
    record = load_pack(PEBBLE_PACK)

    assert record.name == "pebble"
    assert record.version == "0.1.0"
    assert record.fx_vocab_version == 1
    assert record.species == ["pebble"]
    assert record.quirks == ["sunbather"]
    assert record.overlays == ["pebble"]
    assert record.phrase_languages == {"pebble": "en"}
    assert LOADED_PACKS == [record]

    # Species registry: the pebble joins the builtin cat as itself.
    assert species_mod.SPECIES == ("cat", "pebble")
    entry = species_mod.SPECIES_REGISTRY["pebble"]
    assert entry["pronoun"] == "it"
    assert entry["phrase_overlay"] == "pebble"
    assert species_mod.coats_for("pebble") == ("gray",)
    temperament = species_mod.temperament_for("pebble")
    assert temperament["breed_archetype"] == "garden pebble"
    assert temperament["ignore_rate"] == 0.9


def test_pebble_figure_keeps_the_rig_class_contract() -> None:
    load_pack(PEBBLE_PACK)
    figure = PACK_ASSETS["pebble"]["figure"]
    # The exact handles the wool rig animates (figures.js class contract);
    # #dog-eyes is the singleton id the gaze binding owns, weird name and all.
    for marker in (
        'class="tailg"',
        'class="headg"',
        'class="earg"',
        'id="dog-eyes"',
        'class="eyes-open"',
        'class="eyes-happy"',
        'class="eyes-side"',
        'class="nap-eyes"',
        'class="one-eye-eyes"',
        "coat",
        "cream",
        "point",
        'class="dog-contact"',
    ):
        assert marker in figure, marker
    assert figure.startswith("<g>")

    # Client delivery shape: the /api/packs payload for the pebble.
    assets = client_pack_assets()
    assert set(assets) == {"pebble"}
    pebble = assets["pebble"]
    assert pebble["pronoun"] == "it"
    assert set(pebble["palettes"]) == {"gray"}
    assert pebble["palettes"]["gray"] == {
        "body": "#9a9a94",
        "belly": "#cfcfc8",
        "point": "#77776f",
    }
    assert set(pebble["geometry"]) == {"earBelow", "headBelow", "tail", "belly"}
    assert pebble["svg"] == PACK_ASSETS["pebble"]["figure"]


def test_pebble_overlay_serves_its_cells_and_falls_through() -> None:
    load_pack(PEBBLE_PACK)
    overlay = bl.SPECIES_PHRASE_OVERLAYS["pebble"]
    assert set(overlay) == {"body", "action", "spot", "tiny"}

    # Pinned cells serve the pebble's own lines…
    line = bl.fallback_phrase(10, 80, species="pebble", event_id=0)
    assert line in overlay["body"][("low", "content")]
    # …cells the overlay doesn't carry fall through to the shared tables —
    # sparse is the design, not a gap.
    assert ("med", "grumpy") not in overlay["body"]
    assert (
        bl.fallback_phrase(50, 20, species="pebble", event_id=1)
        in bl.BODY_LANGUAGE[("med", "grumpy")]
    )
    # A spot the overlay covers (head/low/content) vs one it doesn't.
    assert (
        bl.fallback_phrase(10, 80, "pet", spot="head", species="pebble", event_id=2)
        in overlay["spot"]["head"][("low", "content")]
    )
    assert (
        bl.fallback_phrase(10, 80, "pet", spot="ear", species="pebble", event_id=3)
        in bl.PET_SPOT_LANGUAGE["ear"][("low", "content")]
    )
    # The tiny table swaps WHOLE (no per-cell fall-through): a pebble's
    # speakable utterances come from its own bucket, never the cat's.
    utterance = bl.contextual_message_phrase(
        50, 80, "hi", 5, allow_utterance=True, species="pebble"
    )
    assert utterance == "..."  # the one speakable line in tiny/content
    assert utterance in overlay["tiny"]["content"]


def test_sunbather_registers_and_drives_the_engine() -> None:
    load_pack(PEBBLE_PACK)
    assert "sunbather" in QUIRKS

    # Action channel: a warm sit earns the line, the delta, and the fx…
    now = utc_now()
    old = MoodState(arousal=50, valence=70, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=52, valence=72, animation_state="sitting", last_drift_at=now)
    effect = engine.get_action_quirk_effect("pet", old, new, ["sunbather"], facts={}, now=now)
    assert effect is not None
    assert effect.text == "*accepts your warmth and, being a rock, returns none of it*"
    assert effect.scene_fx == {"mode": "petting", "duration_ms": 1500}
    assert effect.valence_delta == 1
    # …and its `when` gate actually gates: a cold pebble stays silent.
    cold = MoodState(arousal=50, valence=30, animation_state="sitting", last_drift_at=now)
    assert engine.get_action_quirk_effect("pet", cold, cold, ["sunbather"], now=now) is None


def test_pebble_voice_merges_without_clobbering_builtins() -> None:
    load_pack(PEBBLE_PACK)
    voice = voice_mod.CLIENT_VOICE
    assert voice["coat_labels"]["gray"] == "river gray"
    assert voice["coat_labels"]["tuxedo"] == "tuxedo"  # builtin labels survive
    assert voice["coat_labels"]["marmalade"] == "marmalade"
    assert voice["coat_labels"]["ash"] == "ash"
    assert voice["quirks"]["previews"]["sunbather"].startswith("finds the one")
    assert voice["quirks"]["moods"]["sunbather"] == "warm rock"
    assert "content_sigher" in voice["quirks"]["previews"]
    # The registry-driven coat lists stay in sync for the new species.
    assert voice["coats"]["pebble"] == ["gray"]
    assert voice["coats"]["cat"] == ["tuxedo", "marmalade", "ash"]
