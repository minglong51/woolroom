"""pack lint — the authoring-side contract checker (pack format v1).

The loader (`app/packs/loader.py`) gates hard errors at boot;
`scripts/pack_render.py` renders the review board a human eyeballs. Lint
sits between (docs/design/woolroom-platform-2026-08-18.md §3.2: "the
contract-test suite IS the authoring tool"): it runs the pack through the
REAL loader (every gate; a refused pack is the first ERROR and stops the
run), then checks the authoring-quality contract the loader deliberately
does not:

- rig class contract on the sanitized figure — the classes/ids the wool
  rig animates (figures.js header, style.css consumers): structure
  (`.tailg`/`.headg`/`.earg`, exactly one `#dog-eyes`), all five eye-state
  subgroups (the room hides `.eyes-open` in the happy/side-eye/sleep
  poses — a missing subgroup renders the figure EYELESS there, so it is an
  ERROR, not a suggestion), and the `.coat`/`.cream`/`.point` palette
  hooks the inline `--dog-*` coat vars land on. The ambient layers
  (`.brushstreak`/`.touchfibers`/`.paw-dream`/`.dog-contact`) are
  nice-to-have: missing is a WARN.
- sanitizer drops — the elements/attributes `sanitize_svg` WOULD strip
  from the raw figure, named non-destructively, so an author is not
  confused when the loaded figure is thinner than the file they drew.
- geometry sanity — hitbox thresholds ordered, inside the 400×520 scene
  frame, and reachable from the room's `#dogzone` pettable rect
  (128,270,144,188). pack_render's hitbox overlay remains the eyeball
  check; lint catches the zones that CANNOT work.
- phrase overlay shape — per-cell fall-through is the designed mechanism
  for body/action/spot, so sparsity there is informational (WARN only when
  a species has no overlay, or an overlay pins zero body cells and its
  ambient voice is the cat's). The `tiny` table is the exception: the
  engine swaps it WHOLE (`body_language.py` `contextual_message_phrase` /
  `fallback_phrase` index `overlay["tiny"][valence]` directly), so a
  missing table, a missing valence bucket, or a bucket with no speakable
  (non-asterisk) line is a runtime crash, not a fall-through — ERROR.
- voice coverage — every pack coat id has a `coat_labels` entry, every
  pack quirk has `previews`/`moods` copy (the adopt screens fall back to
  generic lines without them) — WARN.
- manifest polish — author/license present (loader-required, so this PASS
  documents them; v1 has no `description` key — unknown manifest keys are
  a loader error, so the pack's paragraph lives in its README/comments).

Lint never mutates: the pack directory is only read, and the loader's
registry mutations (SPECIES_REGISTRY, SPECIES_PHRASE_OVERLAYS, QUIRKS,
CLIENT_VOICE, LOADED_PACKS, PACK_ASSETS) are snapshotted and restored
around the run so `lint_pack()` is safe to call in-process (tests, CI).

CLI: `scripts/pack_lint.py <pack-dir> [--strict]` — exit 1 on any ERROR
(and on WARN with `--strict`), 0 otherwise.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.data.quirks_catalog import QUIRKS
from app.packs.loader import (
    LOADED_PACKS,
    PACK_ASSETS,
    VALENCE_BUCKETS,
    PackError,
    PackRecord,
    load_pack,
)
from app.packs.sanitize import ALLOWED_ELEMENTS, _local

PASS = "PASS"
WARN = "WARN"
ERROR = "ERROR"

# The wool rig class contract (figures.js header; style.css flips the
# eye-state opacities and animates the structure classes). Rig checks run
# on the SANITIZED figure — the fragment the room actually injects.
RIG_STRUCTURE = {"tailg", "headg", "earg"}
RIG_EYE_STATES = {"eyes-open", "eyes-happy", "eyes-side", "nap-eyes", "one-eye-eyes"}
RIG_PALETTE = {"coat", "cream", "point"}
RIG_EXTRAS = {"brushstreak", "touchfibers", "paw-dream", "dog-contact"}
EYE_SINGLETON = "dog-eyes"

# The scene frame (wool.js pointer math) and the room's pettable rect
# (#dogzone, app/static/index.html:530) — the plausibility envelope for
# hitbox thresholds. Duplicated in scripts/pack_render.py's overlay.
SCENE_W, SCENE_H = 400, 520
DOGZONE = (128, 270, 144, 188)  # x, y, w, h


@dataclass
class LintFinding:
    """One check's outcome: severity + a one-line reason naming the why."""

    check: str
    severity: str
    message: str


@dataclass
class LintReport:
    """Every finding from one lint run, in check order."""

    path: Path
    name: str | None = None  # manifest name/version, when the load passed
    version: str | None = None
    findings: list[LintFinding] = field(default_factory=list)

    def add(self, check: str, severity: str, message: str) -> None:
        self.findings.append(LintFinding(check, severity, message))

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def exit_code(self, *, strict: bool = False) -> int:
        if self.count(ERROR):
            return 1
        if strict and self.count(WARN):
            return 1
        return 0


# ────────── registry isolation (lint never mutates) ──────────


@contextmanager
def _isolated_registries() -> Iterator[None]:
    """Snapshot/restore every table `load_pack` mutates, so linting leaves
    the process exactly as it found it (the same hygiene the pack tests
    apply around their loads)."""
    snapshot = (
        copy.deepcopy(species_mod.SPECIES_REGISTRY),
        species_mod.SPECIES,
        copy.deepcopy(QUIRKS),
        copy.deepcopy(bl.SPECIES_PHRASE_OVERLAYS),
        copy.deepcopy(voice_mod.CLIENT_VOICE),
    )
    try:
        yield
    finally:
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


# ────────── figure introspection (sanitized fragment) ──────────


def _figure_handles(figure: str) -> tuple[set[str], list[str]]:
    """Every class handle and every id in the sanitized figure fragment."""
    classes: set[str] = set()
    ids: list[str] = []
    for el in ET.fromstring(figure).iter():
        classes.update(el.get("class", "").split())
        if el.get("id"):
            ids.append(el.get("id", ""))
    return classes, ids


def _sanitize_drops(text: str) -> tuple[list[str], list[str]]:
    """What sanitize_svg would drop from the raw figure, non-destructively.

    Returns (dropped element tags, stripped attribute descriptions). The
    decisions mirror `sanitize._clean_element` exactly — the allowlist and
    `_local` come from the sanitizer itself, and the lint test cross-checks
    this list against the sanitizer's real output so the two never drift.
    """
    dropped: list[str] = []
    stripped: list[str] = []

    def walk(el: ET.Element) -> None:
        tag = _local(el.tag)
        if tag not in ALLOWED_ELEMENTS:
            dropped.append(tag)  # dropped WITH its subtree — do not descend
            return
        for key, value in el.attrib.items():
            name = _local(key)
            if name.startswith("on"):
                stripped.append(f"{name} on <{tag}>")
            elif name == "href":
                stripped.append(f"href on <{tag}>")
            elif name == "style" and "url(" in value.lower().replace(" ", ""):
                stripped.append(f"style url(...) on <{tag}>")
        for child in el:
            walk(child)

    walk(ET.fromstring(text))
    return dropped, stripped


# ────────── the checks (pure functions over gathered load results) ──────────


def _check_rig(report: LintReport, species_id: str, figure: str) -> None:
    classes, ids = _figure_handles(figure)

    missing = sorted(RIG_STRUCTURE - classes)
    singleton = ids.count(EYE_SINGLETON)
    problems = []
    if missing:
        problems.append(f"missing {' '.join(f'.{c}' for c in missing)}")
    if singleton == 0:
        problems.append(
            f"no #{EYE_SINGLETON} — the gaze binding and the pose eye-transforms "
            "own that singleton id (weird name and all)"
        )
    elif singleton > 1:
        problems.append(
            f"#{EYE_SINGLETON} appears {singleton}x — it is a singleton: the visitor "
            "deidentify strips only the first, so a guest copy would duplicate the id"
        )
    if problems:
        report.add(f"rig structure [{species_id}]", ERROR, "; ".join(problems))
    else:
        report.add(
            f"rig structure [{species_id}]",
            PASS,
            ".tailg .headg .earg present, exactly one #dog-eyes",
        )

    missing = sorted(RIG_EYE_STATES - classes)
    if missing:
        report.add(
            f"rig eye states [{species_id}]",
            ERROR,
            f"missing {' '.join(f'.{c}' for c in missing)} — style.css hides .eyes-open in the "
            "happy/side-eye/sleep/one-eye poses, so the figure renders EYELESS there",
        )
    else:
        report.add(
            f"rig eye states [{species_id}]",
            PASS,
            "all five eye-state subgroups (open/happy/side/nap/one-eye)",
        )

    missing = sorted(RIG_PALETTE - classes)
    if missing:
        report.add(
            f"rig palette [{species_id}]",
            ERROR,
            f"missing {' '.join(f'.{c}' for c in missing)} — the inline --dog-body/--dog-belly/"
            "--dog-point coat vars land on those classes; a missing slot paints that coat "
            "color nowhere (every coat renders alike)",
        )
    else:
        report.add(
            f"rig palette [{species_id}]",
            PASS,
            ".coat .cream .point all used — every coat slot paints",
        )

    missing = sorted(RIG_EXTRAS - classes)
    if missing:
        report.add(
            f"rig extras [{species_id}]",
            WARN,
            f"no {' '.join(f'.{c}' for c in missing)} — optional layers (grooming streaks, "
            "touch fibers, the sleep paw-twitch, the contact shadow) won't render",
        )
    else:
        report.add(
            f"rig extras [{species_id}]",
            PASS,
            ".brushstreak .touchfibers .paw-dream .dog-contact all present",
        )

    stray = sorted({i for i in ids if i != EYE_SINGLETON})
    if stray:
        report.add(
            f"figure ids [{species_id}]",
            WARN,
            f"unexpected id(s) {' '.join(f'#{i}' for i in stray)} — builtin figures carry no id "
            f"but #{EYE_SINGLETON}; a stray id can collide with the room's own (#dogzone, #wool-scene)",
        )
    else:
        report.add(f"figure ids [{species_id}]", PASS, f"no id but #{EYE_SINGLETON}")


def _check_sanitizer_drops(report: LintReport, species_id: str, raw_svg: str) -> None:
    dropped, stripped = _sanitize_drops(raw_svg)
    if not dropped and not stripped:
        report.add(
            f"sanitizer drops [{species_id}]",
            PASS,
            f"nothing in species/{species_id}.svg is stripped — the file is what loads",
        )
        return
    parts = []
    if dropped:
        unique = " ".join(f"<{t}>" for t in sorted(set(dropped)))
        parts.append(f"elements {unique} (dropped with their subtrees)")
    if stripped:
        parts.append(f"attributes {', '.join(sorted(set(stripped)))}")
    report.add(
        f"sanitizer drops [{species_id}]",
        WARN,
        f"the sanitizer strips {'; '.join(parts)} — the loaded figure is thinner "
        "than the file you drew",
    )


def _check_geometry(report: LintReport, species_id: str, g: dict[str, Any]) -> None:
    zx, zy, zw, zh = DOGZONE
    zx2, zy2 = zx + zw, zy + zh
    ear, head = g["earBelow"], g["headBelow"]
    tail, belly = g["tail"], g["belly"]
    problems = []
    if ear >= head:
        problems.append(f"earBelow {ear} >= headBelow {head} — the ear zone is empty")
    if belly["xAbove"] >= belly["xBelow"]:
        problems.append(
            f"belly xAbove {belly['xAbove']} >= xBelow {belly['xBelow']} — the belly zone is empty"
        )
    for label, value, hi in (
        ("earBelow", ear, SCENE_H),
        ("headBelow", head, SCENE_H),
        ("tail.yAbove", tail["yAbove"], SCENE_H),
        ("tail.xAbove", tail["xAbove"], SCENE_W),
        ("belly.yAbove", belly["yAbove"], SCENE_H),
        ("belly.xAbove", belly["xAbove"], SCENE_W),
        ("belly.xBelow", belly["xBelow"], SCENE_W),
    ):
        if not 0 <= value <= hi:
            problems.append(f"{label} {value} lies outside the {SCENE_W}x{SCENE_H} scene frame")
    if ear <= zy:
        problems.append(
            f"earBelow {ear} is at/above the pettable zone's top (y={zy}) — no touch can land on an ear"
        )
    if head <= zy:
        problems.append(
            f"headBelow {head} is at/above the pettable zone's top (y={zy}) — the head zone is unreachable"
        )
    if tail["xAbove"] >= zx2 or tail["yAbove"] >= zy2:
        problems.append(
            f"the tail zone starts at/beyond the pettable zone's far edge "
            f"(x>={tail['xAbove']} vs {zx2}, y>={tail['yAbove']} vs {zy2}) — the tail is unpettable"
        )
    if belly["yAbove"] >= zy2 or belly["xAbove"] >= zx2 or belly["xBelow"] <= zx:
        problems.append(
            "the belly rect misses the pettable zone entirely — the belly is untouchable"
        )
    if problems:
        report.add(f"geometry [{species_id}]", WARN, "; ".join(problems))
    else:
        report.add(
            f"geometry [{species_id}]",
            PASS,
            "zones ordered, inside the 400x520 frame, and reachable from the #dogzone rect "
            "(pack_render's hitbox overlay is the eyeball check)",
        )


def _check_overlay(report: LintReport, species_id: str, overlay: dict[str, Any] | None) -> None:
    if overlay is None:
        report.add(
            f"phrase overlay [{species_id}]",
            WARN,
            "no phrase overlay — every line falls through to the base tables, so the "
            "species speaks with the cat's voice (fine for a draft; a voice is what "
            "makes a species itself)",
        )
        return

    # tiny completeness — the one table with NO per-cell fall-through.
    tiny = overlay.get("tiny")
    if tiny is None:
        report.add(
            f"overlay tiny [{species_id}]",
            ERROR,
            "the overlay carries no tiny table — the engine swaps the tiny table WHOLE "
            '(body_language.py indexes overlay["tiny"][valence] directly), so the first '
            "message reply raises KeyError instead of falling through",
        )
    else:
        missing = sorted(VALENCE_BUCKETS - set(tiny))
        unspeakable = sorted(
            v
            for v, lines in tiny.items()
            if all(line.strip().startswith("*") and line.strip().endswith("*") for line in lines)
        )
        problems = []
        if missing:
            problems.append(
                f"missing valence bucket(s) {' '.join(missing)} — tiny swaps whole, so a "
                f"{'/'.join(missing)} message reply raises KeyError"
            )
        if unspeakable:
            problems.append(
                f"bucket(s) {' '.join(unspeakable)} have no speakable (non-*...*) line — the "
                "utterance path filters asterisk body lines and would divide by zero"
            )
        if problems:
            report.add(f"overlay tiny [{species_id}]", ERROR, "; ".join(problems))
        else:
            report.add(
                f"overlay tiny [{species_id}]",
                PASS,
                "tiny carries all three valence buckets, each with a speakable line "
                "(the one table that swaps whole)",
            )

    # sparsity — informational; body/action/spot fall through per cell by design.
    pinned = len(overlay.get("body", {}))
    pinned += sum(len(cells) for cells in overlay.get("action", {}).values())
    pinned += sum(len(cells) for cells in overlay.get("spot", {}).values())
    pinned += len(tiny or {})
    universe = 9 + 9 * len(bl.ACTION_LANGUAGE) + 9 * len(bl.PET_SPOT_LANGUAGE) + 3
    if not overlay.get("body"):
        report.add(
            f"overlay sparsity [{species_id}]",
            WARN,
            "pins no body cells — the ambient fallback voice (the lines heard most) is "
            "entirely the base dog tables",
        )
    else:
        report.add(
            f"overlay sparsity [{species_id}]",
            PASS,
            f"pins {pinned} of {universe} phrase cells; the rest fall through to the base "
            "tables by design",
        )


# ────────── the run ──────────


def lint_pack(path: str | Path) -> LintReport:
    """Lint one pack directory. The load gates run first (a PackError is the
    initial ERROR and ends the run); every later check is a pure function of
    what the isolated load registered. Never writes, never mutates registries."""
    report = LintReport(path=Path(path))
    with _isolated_registries():
        try:
            record: PackRecord = load_pack(path)
        except PackError as exc:
            report.add(
                "load",
                ERROR,
                f"{type(exc).__name__}: {exc} — the loader refuses this pack; fix the gate "
                "before any authoring check can run",
            )
            return report
        report.name = record.name
        report.version = record.version
        report.add(
            "load",
            PASS,
            f"{record.name} v{record.version} passes every loader gate "
            f"(species: {', '.join(record.species) or 'none'}; "
            f"quirks: {', '.join(record.quirks) or 'none'}; "
            f"overlays: {', '.join(record.overlays) or 'none'})",
        )
        report.add(
            "manifest",
            PASS,
            f"author {record.author} · license {record.license} · fx vocab "
            f"v{record.fx_vocab_version} (v1 has no description key — unknown manifest "
            "keys are a loader error)",
        )

        # Gather what the pure checks need BEFORE the isolation restores:
        # PACK_ASSETS values are fresh per load (references survive the
        # clear); CLIENT_VOICE coverage must be evaluated here, pre-restore.
        root = Path(record.path)
        per_species = {sid: PACK_ASSETS[sid] for sid in record.species}
        raw_svgs = {
            sid: (root / "species" / f"{sid}.svg").read_text(encoding="utf-8")
            for sid in record.species
        }
        overlays = {
            sid: bl.SPECIES_PHRASE_OVERLAYS.get(sid)
            for sid in set(record.species) | set(record.overlays)
        }

        coat_labels = voice_mod.CLIENT_VOICE.get("coat_labels", {})
        missing_labels = [
            f"{sid}:{coat}"
            for sid in record.species
            for coat in per_species[sid]["coats"]
            if coat not in coat_labels
        ]
        previews = voice_mod.CLIENT_VOICE.get("quirks", {}).get("previews", {})
        moods = voice_mod.CLIENT_VOICE.get("quirks", {}).get("moods", {})
        missing_previews = [q for q in record.quirks if q not in previews]
        missing_moods = [q for q in record.quirks if q not in moods]

    for sid in record.species:
        _check_rig(report, sid, per_species[sid]["figure"])
        _check_sanitizer_drops(report, sid, raw_svgs[sid])
        _check_geometry(report, sid, per_species[sid]["geometry"])
    for sid in sorted(overlays):
        _check_overlay(report, sid, overlays[sid])

    if missing_labels:
        report.add(
            "voice coats",
            WARN,
            f"no coat_labels entry for {', '.join(missing_labels)} — the coat picker "
            "shows the raw id",
        )
    else:
        report.add("voice coats", PASS, "every pack coat id has a coat_labels entry")
    quirk_gaps = []
    if missing_previews:
        quirk_gaps.append(f"no preview copy for {', '.join(missing_previews)}")
    if missing_moods:
        quirk_gaps.append(f"no mood label for {', '.join(missing_moods)}")
    if quirk_gaps:
        report.add(
            "voice quirks",
            WARN,
            "; ".join(quirk_gaps) + " — the adopt screens fall back to the generic habit lines",
        )
    else:
        report.add(
            "voice quirks",
            PASS,
            "every pack quirk has preview + mood copy" if record.quirks else "no pack quirks",
        )
    return report
