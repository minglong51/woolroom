"""Woolroom content-pack loader — pack format v1.

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

Per-species art/geometry/palettes land in `PACK_ASSETS`; `LOADED_PACKS`
records what booted. `client_pack_assets()` shapes `PACK_ASSETS` for
`GET /api/packs`, the boot fetch figures.js resolves pack species from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.data import body_language as bl
from app.data.quirks_catalog import QUIRKS
from app.data.species import SPECIES_REGISTRY, register_species
from app.data.voice import CLIENT_VOICE
from app.engine import quirks as quirk_engine
from app.packs.sanitize import SvgSanitizeError, sanitize_svg
from app.room_contract import FX_MODES, FX_VOCAB_VERSION

# ────────── caps and vocabularies (the gates' numbers) ──────────

MAX_FILE_BYTES = 256 * 1024  # per pack file
MAX_PACK_BYTES = 1024 * 1024  # per pack, summed over the files we load
MAX_PROSE_CHARS = 500  # phrase lines, quirk text/labels, temperament copy
MAX_VOICE_CHARS = 1000  # voice.yaml string leaves

# pets.species / pets.coat are VARCHAR(16); quirk ids get the same charset
# with room to breathe. Ids come from file stems.
SPECIES_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")
QUIRK_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")

MANIFEST_KEYS = {"name", "version", "author", "license", "fx_vocab_version"}
SPECIES_KEYS = {"temperament", "pronoun", "coats", "geometry"}
TEMPERAMENT_KEYS = {"breed_archetype", "description", "traits", "ignore_rate"}
# The six trait slots of the builtin cat temperament — descriptive
# prompt fodder, so the shape is pinned exactly.
TRAIT_KEYS = {
    "warmth_baseline",
    "sociability",
    "energy",
    "curiosity",
    "expressiveness",
    "stubbornness",
}
COAT_COLOR_KEYS = {"body", "belly", "point"}  # figures.js PALETTES shape
GEOMETRY_KEYS = {"earBelow", "headBelow", "tail", "belly"}  # figures.js SPECIES_GEOMETRY
GEOMETRY_REGION_KEYS = {"tail": {"yAbove", "xAbove"}, "belly": {"yAbove", "xAbove", "xBelow"}}

OVERLAY_TABLES = {"body", "action", "spot", "tiny"}
# Arousal × valence cell buckets — mirrors body_language.bucket_arousal /
# bucket_valence; phrase YAML nests them as maps ("low": {"content": [...]})
# because YAML has no tuple keys.
AROUSAL_BUCKETS = frozenset({"low", "med", "high"})
VALENCE_BUCKETS = frozenset({"grumpy", "neutral", "content"})

QUIRK_KEYS = {"label", "description", "complexity", "hooks", "behavior"}
QUIRK_COMPLEXITIES = {"low", "medium", "high"}
BEHAVIOR_CHANNELS = {"pose", "action", "scheduler", "events"}
RULE_KEYS = {
    "when",
    "write",
    "text",
    "emit",
    "choices",
    "scene_fx",
    "fact_updates",
    "arousal_delta",
    "valence_delta",
    "priority",
}


# ────────── named errors (one per gate) ──────────


class PackError(RuntimeError):
    """Base class for every pack gate refusal."""


class PackManifestError(PackError):
    """pack.yaml missing, unparseable, or carrying wrong/unknown fields."""


class PackConfinementError(PackError):
    """Path escapes the pack dir, a symlink appears in the chain, or the
    pack path itself is not a real directory."""


class PackSizeError(PackError):
    """A file exceeds 256KB, or the pack's loaded files exceed 1MB."""


class PackSvgError(PackError):
    """Figure art fails the SVG sanitizer."""


class PackSpeciesError(PackError):
    """A species yaml/svg pair is malformed."""


class PackPhraseError(PackError):
    """A phrase overlay is malformed or targets an unknown species."""


class PackQuirkError(PackError):
    """A quirk definition steps outside the condition grammar."""


class PackVoiceError(PackError):
    """voice.yaml is not a plain string-keyed data mapping."""


class PackVocabError(PackError):
    """The pack was built against a newer fx vocabulary than this engine."""


class PackCollisionError(PackError):
    """A pack id collides with builtin content or another pack."""


# ────────── loader state ──────────


@dataclass
class PackRecord:
    """What one successfully loaded pack registered (provenance + debug)."""

    name: str
    version: str
    author: str
    license: str
    fx_vocab_version: int
    path: str
    species: list[str] = field(default_factory=list)
    quirks: list[str] = field(default_factory=list)
    overlays: list[str] = field(default_factory=list)
    phrase_languages: dict[str, str] = field(default_factory=dict)


LOADED_PACKS: list[PackRecord] = []

# Species id -> {"pack", "coats" (hex palettes), "geometry" (hitboxes),
# "figure" (sanitized svg fragment)}. Served to the client by
# `client_pack_assets()` over GET /api/packs.
PACK_ASSETS: dict[str, dict[str, Any]] = {}


class _Budget:
    """Per-pack byte total across the files the loader actually reads."""

    def __init__(self) -> None:
        self.total = 0

    def add(self, size: int, rel: Path) -> None:
        self.total += size
        if self.total > MAX_PACK_BYTES:
            raise PackSizeError(
                f"pack exceeds the {MAX_PACK_BYTES}-byte cap at {rel} "
                f"({self.total} bytes of loaded content so far)"
            )


# ────────── small validators ──────────


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_id(value: str, pattern: re.Pattern[str], errcls: type[PackError]) -> None:
    if not pattern.fullmatch(value):
        raise errcls(
            f"id {value!r} must match {pattern.pattern!r} "
            "(lowercase snake, length-capped to the storage columns)"
        )


def _check_str(value: Any, what: str, errcls: type[PackError], cap: int = MAX_PROSE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise errcls(f"{what} must be a non-empty string")
    if len(value) > cap:
        raise errcls(f"{what} exceeds the {cap}-char prose cap")
    return value


def _check_exact_keys(node: Any, keys: set[str], what: str, errcls: type[PackError]) -> dict:
    if not isinstance(node, dict):
        raise errcls(f"{what} must be a mapping")
    unknown = set(node) - keys
    missing = keys - set(node)
    if unknown:
        raise errcls(f"{what} has unknown keys: {sorted(unknown)}")
    if missing:
        raise errcls(f"{what} is missing keys: {sorted(missing)}")
    return node


# ────────── file access: confinement + caps + safe YAML ──────────


def _confine_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if root.is_symlink():
        raise PackConfinementError(f"pack path is a symlink: {path}")
    if not root.exists():
        raise PackConfinementError(f"pack directory does not exist: {path}")
    if not root.is_dir():
        raise PackConfinementError(f"pack path is not a directory: {path}")
    return root.resolve()


def _read_pack_file(root: Path, rel: Path, budget: _Budget, errcls: type[PackError]) -> str:
    """Read one pack file under the confinement + size gates.

    Every path component between the pack root and the file must be real
    (no symlinks), the resolved path must stay inside the root, and the
    byte caps hold per file and per pack.
    """
    candidate = root / rel
    node = candidate
    while node != root:
        if node.is_symlink():
            raise PackConfinementError(f"symlink refused inside pack: {rel}")
        node = node.parent
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise PackConfinementError(f"file resolves outside the pack dir: {rel}")
    if not resolved.is_file():
        raise errcls(f"required pack file is missing: {rel}")
    size = resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise PackSizeError(f"{rel} is {size} bytes (cap {MAX_FILE_BYTES})")
    budget.add(size, rel)
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise errcls(f"cannot read {rel}: {exc}") from exc


def _load_yaml(root: Path, rel: Path, budget: _Budget, errcls: type[PackError]) -> dict:
    text = _read_pack_file(root, rel, budget, errcls)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise errcls(f"{rel} is not valid (safe) YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise errcls(f"{rel} must be a YAML mapping")
    return data


def _iter_subdir(root: Path, name: str, suffix: str) -> list[Path]:
    subdir = root / name
    if not subdir.exists():
        return []
    return sorted(subdir.glob(f"*{suffix}"))


# ────────── pack.yaml ──────────


def _load_manifest(root: Path, budget: _Budget) -> dict[str, Any]:
    rel = Path("pack.yaml")
    if not (root / rel).exists():
        raise PackManifestError(f"{root}: pack.yaml is required")
    data = _check_exact_keys(
        _load_yaml(root, rel, budget, PackManifestError),
        MANIFEST_KEYS,
        "pack.yaml",
        PackManifestError,
    )
    for key in ("name", "author", "license"):
        _check_str(data[key], f"pack.yaml {key!r}", PackManifestError)
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, (str, int, float)):
        raise PackManifestError("pack.yaml 'version' must be a string or number")
    fx = data["fx_vocab_version"]
    if not _is_int(fx) or fx < 0:
        raise PackManifestError("pack.yaml 'fx_vocab_version' must be a non-negative integer")
    if fx > FX_VOCAB_VERSION:
        raise PackVocabError(
            f"pack {data['name']!r} was built against fx vocabulary v{fx}; "
            f"this engine speaks v{FX_VOCAB_VERSION}"
        )
    return data


# ────────── species/<id>.yaml + species/<id>.svg ──────────

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _validate_temperament(species_id: str, node: Any) -> dict:
    what = f"species {species_id!r} temperament"
    data = _check_exact_keys(node, TEMPERAMENT_KEYS, what, PackSpeciesError)
    _check_str(data["breed_archetype"], f"{what} 'breed_archetype'", PackSpeciesError)
    _check_str(data["description"], f"{what} 'description'", PackSpeciesError)
    traits = _check_exact_keys(data["traits"], TRAIT_KEYS, f"{what} traits", PackSpeciesError)
    for key, value in traits.items():
        _check_str(value, f"{what} trait {key!r}", PackSpeciesError, cap=100)
    ignore_rate = data["ignore_rate"]
    if not _is_number(ignore_rate) or not 0 <= ignore_rate <= 1:
        raise PackSpeciesError(f"{what} 'ignore_rate' must be a number in [0, 1]")
    return data


def _validate_coats(species_id: str, node: Any) -> dict[str, dict[str, str]]:
    what = f"species {species_id!r} coats"
    if not isinstance(node, dict) or not node:
        raise PackSpeciesError(f"{what} must be a non-empty mapping of coat id to colors")
    for coat_id, colors in node.items():
        if not isinstance(coat_id, str):
            raise PackSpeciesError(f"{what}: coat ids must be strings")
        _check_id(coat_id, SPECIES_ID_RE, PackSpeciesError)
        color_map = _check_exact_keys(
            colors, COAT_COLOR_KEYS, f"{what} {coat_id!r}", PackSpeciesError
        )
        for slot, value in color_map.items():
            if not isinstance(value, str) or not _HEX_COLOR_RE.fullmatch(value):
                raise PackSpeciesError(
                    f"{what} {coat_id!r} {slot!r} must be a #rrggbb hex color, got {value!r}"
                )
    return node


def _validate_geometry(species_id: str, node: Any) -> dict:
    what = f"species {species_id!r} geometry"
    data = _check_exact_keys(node, GEOMETRY_KEYS, what, PackSpeciesError)
    for key in ("earBelow", "headBelow"):
        if not _is_number(data[key]):
            raise PackSpeciesError(f"{what} {key!r} must be a number")
    for region, keys in GEOMETRY_REGION_KEYS.items():
        sub = _check_exact_keys(data[region], keys, f"{what} {region!r}", PackSpeciesError)
        for key, value in sub.items():
            if not _is_number(value):
                raise PackSpeciesError(f"{what} {region!r} {key!r} must be a number")
    return data


def _load_species(root: Path, budget: _Budget) -> dict[str, dict[str, Any]]:
    """Validate every species yaml + its paired, sanitized svg figure."""
    found: dict[str, dict[str, Any]] = {}
    for path in _iter_subdir(root, "species", ".yaml"):
        species_id = path.stem
        _check_id(species_id, SPECIES_ID_RE, PackSpeciesError)
        rel = path.relative_to(root)
        data = _check_exact_keys(
            _load_yaml(root, rel, budget, PackSpeciesError),
            SPECIES_KEYS,
            f"species {species_id!r}",
            PackSpeciesError,
        )
        temperament = _validate_temperament(species_id, data["temperament"])
        coats = _validate_coats(species_id, data["coats"])
        geometry = _validate_geometry(species_id, data["geometry"])
        pronoun = _check_str(
            data["pronoun"], f"species {species_id!r} pronoun", PackSpeciesError, cap=16
        )
        found[species_id] = {
            "entry": {
                "temperament": temperament,
                "coats": tuple(coats),
                "pronoun": pronoun,
                # wired at registration from the pack's phrase files
                "phrase_overlay": None,
            },
            "coats": coats,
            "geometry": geometry,
        }
    for path in _iter_subdir(root, "species", ".svg"):
        species_id = path.stem
        rel = path.relative_to(root)
        if species_id not in found:
            raise PackSpeciesError(f"figure art {rel} has no matching species yaml in this pack")
        raw = _read_pack_file(root, rel, budget, PackSvgError)
        try:
            found[species_id]["figure"] = sanitize_svg(raw)
        except SvgSanitizeError as exc:
            raise PackSvgError(f"{rel}: {exc}") from exc
    for species_id in found:
        if "figure" not in found[species_id]:
            raise PackSpeciesError(
                f"species {species_id!r} is missing its figure art (species/{species_id}.svg)"
            )
    return found


# ────────── phrases/<id>.yaml ──────────


def _validate_lines(node: Any, what: str) -> list[str]:
    if not isinstance(node, list) or not node:
        raise PackPhraseError(f"{what} must be a non-empty list of phrase lines")
    for line in node:
        _check_str(line, f"{what} line", PackPhraseError)
    return list(node)


def _validate_cells(node: Any, what: str) -> dict[tuple[str, str], list[str]]:
    """{arousal: {valence: [lines]}} → the phrasebook's (arousal, valence) keys."""
    if not isinstance(node, dict) or not node:
        raise PackPhraseError(f"{what} must nest arousal bucket → valence bucket → lines")
    cells: dict[tuple[str, str], list[str]] = {}
    for arousal, valences in node.items():
        if arousal not in AROUSAL_BUCKETS:
            raise PackPhraseError(
                f"{what}: unknown arousal bucket {arousal!r} (one of {sorted(AROUSAL_BUCKETS)})"
            )
        if not isinstance(valences, dict) or not valences:
            raise PackPhraseError(f"{what} {arousal!r} must map valence buckets to lines")
        for valence, lines in valences.items():
            if valence not in VALENCE_BUCKETS:
                raise PackPhraseError(
                    f"{what}: unknown valence bucket {valence!r} (one of {sorted(VALENCE_BUCKETS)})"
                )
            cells[(arousal, valence)] = _validate_lines(lines, f"{what} {arousal!r}/{valence!r}")
    return cells


def _load_phrases(
    root: Path, budget: _Budget, pack_species: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    overlays: dict[str, dict[str, Any]] = {}
    for path in _iter_subdir(root, "phrases", ".yaml"):
        species_id = path.stem
        _check_id(species_id, SPECIES_ID_RE, PackPhraseError)
        rel = path.relative_to(root)
        if species_id not in pack_species and species_id not in SPECIES_REGISTRY:
            raise PackPhraseError(
                f"phrase overlay {rel} targets unknown species {species_id!r} "
                "(overlays may only extend builtin species or this pack's own)"
            )
        data = _load_yaml(root, rel, budget, PackPhraseError)
        unknown = set(data) - (OVERLAY_TABLES | {"language"})
        if unknown:
            raise PackPhraseError(f"{rel} has unknown keys: {sorted(unknown)}")
        language = data.get("language", "en")
        _check_str(language, f"{rel} 'language'", PackPhraseError, cap=16)
        tables: dict[str, Any] = {}
        if "body" in data:
            tables["body"] = _validate_cells(data["body"], f"{rel} 'body'")
        for table, known_keys in (("action", bl.ACTION_LANGUAGE), ("spot", bl.PET_SPOT_LANGUAGE)):
            if table not in data:
                continue
            node = data[table]
            if not isinstance(node, dict) or not node:
                raise PackPhraseError(f"{rel} {table!r} must be a non-empty mapping")
            sub: dict[str, dict[tuple[str, str], list[str]]] = {}
            for key, cells in node.items():
                if key not in known_keys:
                    raise PackPhraseError(
                        f"{rel} {table!r}: unknown {table} key {key!r} "
                        f"(one of {sorted(known_keys)})"
                    )
                sub[key] = _validate_cells(cells, f"{rel} {table!r} {key!r}")
            tables[table] = sub
        if "tiny" in data:
            node = data["tiny"]
            if not isinstance(node, dict) or not node:
                raise PackPhraseError(f"{rel} 'tiny' must map valence buckets to lines")
            tiny: dict[str, list[str]] = {}
            for valence, lines in node.items():
                if valence not in VALENCE_BUCKETS:
                    raise PackPhraseError(
                        f"{rel} 'tiny': unknown valence bucket {valence!r} "
                        f"(one of {sorted(VALENCE_BUCKETS)})"
                    )
                tiny[valence] = _validate_lines(lines, f"{rel} 'tiny' {valence!r}")
            tables["tiny"] = tiny
        if not tables:
            raise PackPhraseError(f"{rel} carries no phrase tables")
        overlays[species_id] = {"tables": tables, "language": language}
    return overlays


# ────────── quirks/<id>.yaml ──────────


def _condition_keys(when: dict[str, Any]) -> Iterable[str]:
    for key, value in when.items():
        yield key
        if key == "any" and isinstance(value, list):
            for sub in value:
                if isinstance(sub, dict):
                    yield from _condition_keys(sub)


def _validate_when(quirk_id: str, channel: str, rule: dict[str, Any]) -> None:
    when = rule.get("when")
    if not isinstance(when, dict) or not when:
        raise PackQuirkError(f"quirk {quirk_id!r}.{channel}: every rule needs a 'when' mapping")
    unknown = set(_condition_keys(when)) - set(quirk_engine.CONDITION_EVALUATORS)
    if unknown:
        raise PackQuirkError(
            f"quirk {quirk_id!r}.{channel} uses conditions outside the grammar: {sorted(unknown)}"
        )


def _validate_scene_fx(quirk_id: str, channel: str, rule: dict[str, Any]) -> None:
    scene_fx = rule["scene_fx"]
    where = f"quirk {quirk_id!r}.{channel} scene_fx"
    if not isinstance(scene_fx, dict):
        raise PackQuirkError(f"{where} must be a mapping")
    mode = scene_fx.get("mode")
    if mode not in FX_MODES:
        raise PackQuirkError(f"{where} mode {mode!r} is not in the fx vocabulary (FX_MODES)")
    duration = scene_fx.get("duration_ms")
    if duration is not None and (not _is_int(duration) or duration <= 0):
        raise PackQuirkError(f"{where} 'duration_ms' must be a positive integer")


def _validate_quirk_rule(quirk_id: str, channel: str, rule: Any) -> None:
    where = f"quirk {quirk_id!r}.{channel}"
    if not isinstance(rule, dict):
        raise PackQuirkError(f"{where}: every rule must be a mapping")
    unknown = set(rule) - RULE_KEYS
    if unknown:
        raise PackQuirkError(f"{where} has unknown rule keys: {sorted(unknown)}")
    _validate_when(quirk_id, channel, rule)
    if channel == "pose":
        write = rule.get("write")
        if not isinstance(write, dict) or not write:
            raise PackQuirkError(f"{where}: pose rules need a non-empty 'write' mapping")
        rig_keys = set(quirk_engine.base_pose_detail())
        for key, spec in write.items():
            if key not in rig_keys:
                raise PackQuirkError(f"{where} writes unknown rig key {key!r}")
            if isinstance(spec, dict):
                if len(spec) != 1:
                    raise PackQuirkError(f"{where} rig key {key!r}: merge specs carry one op")
                unknown_ops = set(spec) - quirk_engine.POSE_WRITE_OPS
                if unknown_ops:
                    raise PackQuirkError(
                        f"{where} uses unknown pose write ops: {sorted(unknown_ops)}"
                    )
    if channel == "action":
        _check_str(rule.get("text"), f"{where} 'text'", PackQuirkError)
    if "text" in rule:
        _check_str(rule["text"], f"{where} 'text'", PackQuirkError)
    if channel == "events":
        emit = rule.get("emit")
        if not isinstance(emit, dict):
            raise PackQuirkError(f"{where}: events rules need an 'emit' mapping")
        _check_str(emit.get("type"), f"{where} emit 'type'", PackQuirkError, cap=64)
        if not isinstance(emit.get("data"), dict):
            raise PackQuirkError(f"{where} emit 'data' must be a mapping")
    if "scene_fx" in rule:
        _validate_scene_fx(quirk_id, channel, rule)
    for delta in ("arousal_delta", "valence_delta", "priority"):
        if delta in rule and not _is_int(rule[delta]):
            raise PackQuirkError(f"{where} {delta!r} must be an integer")
    if "choices" in rule:
        choices = rule["choices"]
        if not isinstance(choices, dict) or not choices:
            raise PackQuirkError(f"{where} 'choices' must be a non-empty mapping")
        for name, options in choices.items():
            if not isinstance(options, list) or not options:
                raise PackQuirkError(f"{where} choice {name!r} must be a non-empty list")
            for option in options:
                _check_str(option, f"{where} choice {name!r} option", PackQuirkError)
    if "fact_updates" in rule:
        updates = rule["fact_updates"]
        if not isinstance(updates, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in updates.items()
        ):
            raise PackQuirkError(f"{where} 'fact_updates' must map strings to strings")


def _load_quirks(root: Path, budget: _Budget) -> dict[str, dict[str, Any]]:
    defs: dict[str, dict[str, Any]] = {}
    for path in _iter_subdir(root, "quirks", ".yaml"):
        quirk_id = path.stem
        _check_id(quirk_id, QUIRK_ID_RE, PackQuirkError)
        rel = path.relative_to(root)
        data = _load_yaml(root, rel, budget, PackQuirkError)
        unknown = set(data) - QUIRK_KEYS
        if unknown:
            raise PackQuirkError(f"{rel} has unknown keys: {sorted(unknown)}")
        _check_str(data.get("label"), f"{rel} 'label'", PackQuirkError)
        _check_str(data.get("description"), f"{rel} 'description'", PackQuirkError)
        if data.get("complexity") not in QUIRK_COMPLEXITIES:
            raise PackQuirkError(f"{rel} 'complexity' must be one of {sorted(QUIRK_COMPLEXITIES)}")
        hooks = data.get("hooks")
        if hooks is not None and (
            not isinstance(hooks, list) or not all(isinstance(h, str) and h for h in hooks)
        ):
            raise PackQuirkError(f"{rel} 'hooks' must be a list of hook names")
        behavior = data.get("behavior")
        if not isinstance(behavior, dict) or not behavior:
            raise PackQuirkError(f"{rel} needs a non-empty 'behavior' mapping")
        unknown_channels = set(behavior) - BEHAVIOR_CHANNELS
        if unknown_channels:
            raise PackQuirkError(
                f"{rel} wires unknown behavior channels: {sorted(unknown_channels)}"
            )
        for channel, rules in behavior.items():
            if not isinstance(rules, list) or not rules:
                raise PackQuirkError(f"{rel}.{channel} must be a non-empty rule list")
            for rule in rules:
                _validate_quirk_rule(quirk_id, channel, rule)
        defs[quirk_id] = data
    return defs


# ────────── voice.yaml ──────────


def _check_voice_node(node: Any, what: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise PackVoiceError(f"{what}: keys must be strings, got {key!r}")
            _check_voice_node(value, f"{what}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _check_voice_node(value, f"{what}[{i}]")
    elif isinstance(node, str):
        if len(node) > MAX_VOICE_CHARS:
            raise PackVoiceError(f"{what} exceeds the {MAX_VOICE_CHARS}-char voice cap")
    elif node is not None and not isinstance(node, (int, float, bool)):
        raise PackVoiceError(f"{what} must be plain data (mapping/list/scalar)")


def _load_voice(root: Path, budget: _Budget) -> dict[str, Any] | None:
    rel = Path("voice.yaml")
    if not (root / rel).exists():
        return None
    data = _load_yaml(root, rel, budget, PackVoiceError)
    _check_voice_node(data, "voice.yaml")
    return data


def _deep_merge(target: dict, extra: dict) -> None:
    """Recursive merge: dicts merge key by key, anything else is overwritten.

    A pack ADDS voice keys (its coat labels, its quirk previews) without
    restating — and never clobbering — the builtin nested tables.
    """
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


# ────────── the load itself ──────────


def load_pack(path: str | Path) -> PackRecord:
    """Validate + register one pack directory. Any gate violation raises a
    named PackError subclass; validation completes before any registration,
    so a refused pack registers nothing."""
    root = _confine_root(path)
    budget = _Budget()
    manifest = _load_manifest(root, budget)
    species = _load_species(root, budget)
    overlays = _load_phrases(root, budget, species)
    quirks = _load_quirks(root, budget)
    voice = _load_voice(root, budget)

    # Collision gate, checked across everything BEFORE any mutation: ids
    # never override builtin content or another (already loaded) pack.
    for species_id in species:
        if species_id in SPECIES_REGISTRY:
            raise PackCollisionError(
                f"species {species_id!r} collides with an already-registered species"
            )
    for species_id in overlays:
        if species_id in bl.SPECIES_PHRASE_OVERLAYS:
            raise PackCollisionError(f"phrase overlay for {species_id!r} already exists")
    for quirk_id in quirks:
        if quirk_id in QUIRKS:
            raise PackCollisionError(
                f"quirk {quirk_id!r} collides with an already-registered quirk"
            )

    for species_id, spec in species.items():
        entry = dict(spec["entry"])
        entry["phrase_overlay"] = species_id if species_id in overlays else None
        register_species(species_id, entry)
        # CLIENT_VOICE carries the registry's coat lists for the client
        # (voice.py computed the builtin ones at import); keep it in sync.
        CLIENT_VOICE.setdefault("coats", {})[species_id] = list(entry["coats"])
        PACK_ASSETS[species_id] = {
            "pack": manifest["name"],
            "coats": spec["coats"],
            "geometry": spec["geometry"],
            "figure": spec["figure"],
        }
    for species_id, overlay in overlays.items():
        bl.SPECIES_PHRASE_OVERLAYS[species_id] = overlay["tables"]
        registry_entry = SPECIES_REGISTRY.get(species_id)
        if registry_entry is not None and registry_entry.get("phrase_overlay") is None:
            registry_entry["phrase_overlay"] = species_id
    QUIRKS.update(quirks)
    if voice:
        _deep_merge(CLIENT_VOICE, voice)

    record = PackRecord(
        name=manifest["name"],
        version=str(manifest["version"]),
        author=manifest["author"],
        license=manifest["license"],
        fx_vocab_version=manifest["fx_vocab_version"],
        path=str(root),
        species=sorted(species),
        quirks=sorted(quirks),
        overlays=sorted(overlays),
        phrase_languages={sid: o["language"] for sid, o in overlays.items()},
    )
    LOADED_PACKS.append(record)
    return record


def load_packs(paths: Iterable[str]) -> list[PackRecord]:
    """Load every pack dir in order (PACK_PATHS order). Empty → no-op."""
    return [load_pack(path) for path in paths]


def client_pack_assets() -> dict[str, dict[str, Any]]:
    """The GET /api/packs payload: per pack-loaded species, the figure
    assets figures.js needs to render it — sanitized `svg` fragment, coat
    `palettes` (the PALETTES shape), hitbox `geometry` (the SPECIES_GEOMETRY
    shape), and `pronoun`. The builtin cat is never in PACK_ASSETS (the
    client carries it), so the map is empty when no packs loaded."""
    return {
        species_id: {
            "svg": assets["figure"],
            "palettes": assets["coats"],
            "geometry": assets["geometry"],
            "pronoun": SPECIES_REGISTRY[species_id]["pronoun"],
        }
        for species_id, assets in PACK_ASSETS.items()
    }
