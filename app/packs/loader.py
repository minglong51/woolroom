"""Woolroom public content-pack loader — pack format v1.

Loads species/quirk/phrase/voice content packs from LOCAL directories at
boot (`app/main.py` lifespan, before any request is served), behind
fail-closed sanitization gates — the loader-side half of "packs are data,
never code" (docs/design/woolroom-platform-2026-08-18.md §3.1 + its
pressure-test outcome; there is no runtime download path, `PACK_PATHS`
names local dirs only). Default `PACK_PATHS` is empty: the loader is a
no-op and behavior is byte-identical to today.

Pack layout (all ids come from file stems, all YAML via `yaml.safe_load`):

    <pack-dir>/
      pack.yaml          # required: name, version, author, license,
                         # fx_vocab_version (int, <= FX_VOCAB_VERSION)
      species/<id>.yaml  # temperament / pronoun / coats / hitbox geometry
      species/<id>.svg   # figure art: one <g> fragment, sanitized
      phrases/<id>.yaml  # optional phrase overlay for species <id>
                         # (builtin or same-pack), top-level `language:`
      quirks/<id>.yaml   # optional quirk definitions in the exact
                         # condition grammar of app/data/quirks_catalog.py
      voice.yaml         # optional CLIENT_VOICE additions (recursive merge)

Registration is boot-only: the loader mutates SPECIES_REGISTRY (via
`register_species`), SPECIES_PHRASE_OVERLAYS, the QUIRKS catalog, and
CLIENT_VOICE once at startup; those tables are frozen again before
serving. Every gate fails closed: any violation raises a named PackError
subclass and refuses boot. A pack is validated fully BEFORE anything is
registered, so a refused pack never leaves half-registered content behind
(an earlier pack in PACK_PATHS stays registered — boot is refused anyway).

Every pack loaded here is public distribution content. Private site adapters
return bounded card projections instead of mutating these registries.
Per-species art/geometry/palettes land in `PACK_ASSETS`; `LOADED_PACKS`
records what booted. `client_pack_assets()` shapes `PACK_ASSETS` for
`GET /api/packs`, the boot fetch figures.js resolves pack species from.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from woolpack.contract import DEFAULT_ENVIRONMENT, PackEnvironment
from woolpack.sanitize import SvgSanitizeError, sanitize_svg
from woolpack.validation import (
    AROUSAL_BUCKETS,
    BEHAVIOR_CHANNELS,
    COAT_COLOR_KEYS,
    GEOMETRY_KEYS,
    GEOMETRY_REGION_KEYS,
    MANIFEST_KEYS,
    MAX_FILE_BYTES,
    MAX_PACK_BYTES,
    MAX_PROSE_CHARS,
    MAX_VOICE_CHARS,
    OVERLAY_TABLES,
    QUIRK_COMPLEXITIES,
    QUIRK_ID_RE,
    QUIRK_KEYS,
    RULE_KEYS,
    SPECIES_ID_RE,
    SPECIES_KEYS,
    TEMPERAMENT_KEYS,
    TRAIT_KEYS,
    VALENCE_BUCKETS,
    PackCollisionError,
    PackConfinementError,
    PackError,
    PackManifestError,
    PackPhraseError,
    PackQuirkError,
    PackRecord,
    PackSizeError,
    PackSpeciesError,
    PackSvgError,
    PackVocabError,
    PackVoiceError,
    ValidatedPack,
    validate_pack,
)

from app.data import body_language as bl
from app.data.quirks_catalog import QUIRKS
from app.data.species import SPECIES_REGISTRY, register_species
from app.data.voice import CLIENT_VOICE
from app.engine import quirks as quirk_engine
from app.room_contract import FX_MODES, FX_VOCAB_VERSION, QUIRK_EMIT_TYPES

__all__ = [
    "AROUSAL_BUCKETS",
    "BEHAVIOR_CHANNELS",
    "COAT_COLOR_KEYS",
    "DEFAULT_ENVIRONMENT",
    "GEOMETRY_KEYS",
    "GEOMETRY_REGION_KEYS",
    "LOADED_PACKS",
    "MANIFEST_KEYS",
    "MAX_FILE_BYTES",
    "MAX_PACK_BYTES",
    "MAX_PROSE_CHARS",
    "MAX_VOICE_CHARS",
    "OVERLAY_TABLES",
    "PACK_ASSETS",
    "QUIRK_COMPLEXITIES",
    "QUIRK_ID_RE",
    "QUIRK_KEYS",
    "RULE_KEYS",
    "SPECIES_ID_RE",
    "SPECIES_KEYS",
    "TEMPERAMENT_KEYS",
    "TRAIT_KEYS",
    "VALENCE_BUCKETS",
    "PackCollisionError",
    "PackConfinementError",
    "PackEnvironment",
    "PackError",
    "PackManifestError",
    "PackPhraseError",
    "PackQuirkError",
    "PackRecord",
    "PackSizeError",
    "PackSpeciesError",
    "PackSvgError",
    "PackVocabError",
    "PackVoiceError",
    "SvgSanitizeError",
    "ValidatedPack",
    "client_pack_assets",
    "load_pack",
    "load_packs",
    "pack_environment",
    "sanitize_svg",
    "validate_pack",
]

LOADED_PACKS: list[PackRecord] = []
PACK_ASSETS: dict[str, dict[str, Any]] = {}


def pack_environment() -> PackEnvironment:
    return PackEnvironment(
        fx_vocab_version=FX_VOCAB_VERSION,
        fx_modes=frozenset(FX_MODES),
        quirk_emit_types=frozenset(QUIRK_EMIT_TYPES),
        action_ids=frozenset(bl.ACTION_LANGUAGE),
        spot_ids=frozenset(bl.PET_SPOT_LANGUAGE),
        condition_ids=frozenset(quirk_engine.CONDITION_EVALUATORS),
        pose_keys=frozenset(quirk_engine.base_pose_detail()),
        pose_write_ops=frozenset(quirk_engine.POSE_WRITE_OPS),
        species_ids=frozenset(SPECIES_REGISTRY),
        overlay_ids=frozenset(bl.SPECIES_PHRASE_OVERLAYS),
        quirk_ids=frozenset(QUIRKS),
        coat_label_ids=frozenset(CLIENT_VOICE.get("coat_labels", {})),
    )


def _deep_merge(target: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def load_pack(path: str | Path) -> PackRecord:
    validated = validate_pack(path, environment=pack_environment())
    record = validated.record

    for species_id, spec in validated.species.items():
        entry = dict(spec["entry"])
        entry["phrase_overlay"] = species_id if species_id in validated.overlays else None
        register_species(species_id, entry)
        CLIENT_VOICE.setdefault("coats", {})[species_id] = list(entry["coats"])
        PACK_ASSETS[species_id] = {
            "pack": record.name,
            "coats": spec["coats"],
            "geometry": spec["geometry"],
            "figure": spec["figure"],
        }
    for species_id, overlay in validated.overlays.items():
        bl.SPECIES_PHRASE_OVERLAYS[species_id] = overlay["tables"]
        registry_entry = SPECIES_REGISTRY.get(species_id)
        if registry_entry is not None and registry_entry.get("phrase_overlay") is None:
            registry_entry["phrase_overlay"] = species_id
    QUIRKS.update(validated.quirks)
    if validated.voice:
        _deep_merge(CLIENT_VOICE, validated.voice)

    LOADED_PACKS.append(record)
    return record


def load_packs(paths: Iterable[str]) -> list[PackRecord]:
    return [load_pack(path) for path in paths]


def client_pack_assets() -> dict[str, dict[str, Any]]:
    return {
        species_id: {
            "svg": assets["figure"],
            "palettes": assets["coats"],
            "geometry": assets["geometry"],
            "pronoun": SPECIES_REGISTRY[species_id]["pronoun"],
        }
        for species_id, assets in PACK_ASSETS.items()
    }
