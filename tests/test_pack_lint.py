"""pack lint contract tests (woolroom Phase 1d, pack format v1).

Covers `app/packs/lint.py` + `scripts/pack_lint.py`: the shipped example
pack lints with only its deliberate rig-extras WARN, the minimal fixture
behaves the same, the
rig-contract ERRORs (a figure missing `#dog-eyes` breaks poses in the
room), the sanitizer-drop WARN (cross-checked against the sanitizer's real
output so the mirror never drifts), the tiny-table ERROR (the one phrase
table with no per-cell fall-through — an incomplete one is a runtime
KeyError, not a sparse voice), `--strict` semantics, and the registry
isolation every pack test here practices.

Same registry-isolation hygiene as tests/test_packs.py: lint loads packs
through the real loader, so the autouse fixture snapshots and restores the
process-global registries — and one test asserts lint's OWN isolation
holds, since lint is specified as never mutating.
"""

from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import pytest
import yaml

import app.data.body_language as bl
import app.data.species as species_mod
import app.data.voice as voice_mod
from app.data.quirks_catalog import QUIRKS
from app.packs import LOADED_PACKS, PACK_ASSETS
from app.packs.lint import ERROR, WARN, lint_pack
from app.packs.sanitize import sanitize_svg

FIXTURE_PACK = Path(__file__).parent / "fixtures" / "packs" / "pebble"
SHIPPED_PACK = Path(__file__).parent.parent / "packs" / "pebble"

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import pack_lint  # noqa: E402  (scripts/ is not a package)


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


def _mutate_yaml(pack: Path, rel: str, mutate) -> None:
    path = pack / rel
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _finding(report, check: str):
    """The single finding for a check id (fails loudly if absent/duped)."""
    matches = [f for f in report.findings if f.check == check]
    assert len(matches) == 1, f"expected one {check!r} finding, got {matches}"
    return matches[0]


# ────────── the shipped + fixture packs ──────────


def test_shipped_pebble_lints_with_only_the_rig_extras_warning() -> None:
    report = lint_pack(SHIPPED_PACK)
    assert report.exit_code() == 0
    assert report.count(ERROR) == 0
    # The shipped example is deliberately minimal — a rock has no brush
    # strokes to draw — so its one WARN is the rig's optional layers.
    (warning,) = [f for f in report.findings if f.severity == WARN]
    assert warning.check == "rig extras [pebble]"
    assert report.exit_code(strict=True) == 1
    assert (report.name, report.version) == ("pebble", "0.1.0")


def test_pebble_lints_with_only_the_deliberate_extras_warning() -> None:
    report = lint_pack(FIXTURE_PACK)
    assert report.exit_code() == 0
    assert report.count(ERROR) == 0
    # The one WARN is the rig's optional layers — the honest verdict on a
    # deliberately minimal fixture (a rock has no brush strokes to draw).
    (warning,) = [f for f in report.findings if f.severity == WARN]
    assert warning.check == "rig extras [pebble]"
    assert ".brushstreak" in warning.message
    # ...which is exactly what flips the exit code under --strict.
    assert report.exit_code(strict=True) == 1


# ────────── rig contract errors ──────────


def test_figure_missing_dog_eyes_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    svg = (pack / "species" / "pebble.svg").read_text(encoding="utf-8")
    _write(pack, "species/pebble.svg", svg.replace(' id="dog-eyes"', ""))
    report = lint_pack(pack)
    finding = _finding(report, "rig structure [pebble]")
    assert finding.severity == ERROR
    assert "#dog-eyes" in finding.message
    assert report.exit_code() == 1


def test_figure_missing_an_eye_state_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    svg = (pack / "species" / "pebble.svg").read_text(encoding="utf-8")
    # The happy pose hides .eyes-open — with no .eyes-happy the figure is
    # eyeless exactly when the room is happiest. That is a broken pose.
    _write(pack, "species/pebble.svg", svg.replace('class="eyes-happy"', 'class="eyes-joyful"'))
    report = lint_pack(pack)
    finding = _finding(report, "rig eye states [pebble]")
    assert finding.severity == ERROR
    assert ".eyes-happy" in finding.message
    assert report.exit_code() == 1


def test_figure_without_coat_hooks_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    svg = (pack / "species" / "pebble.svg").read_text(encoding="utf-8")
    _write(pack, "species/pebble.svg", svg.replace('class="coat dog-body"', 'class="dog-body"'))
    report = lint_pack(pack)
    finding = _finding(report, "rig palette [pebble]")
    assert finding.severity == ERROR
    assert ".coat" in finding.message
    assert report.exit_code() == 1


def test_stray_figure_ids_warn(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    svg = (pack / "species" / "pebble.svg").read_text(encoding="utf-8")
    _write(
        pack,
        "species/pebble.svg",
        svg.replace('class="tailg"', 'class="tailg" id="dogzone"'),
    )
    report = lint_pack(pack)
    finding = _finding(report, "figure ids [pebble]")
    assert finding.severity == WARN
    assert "#dogzone" in finding.message
    assert report.exit_code() == 0


# ────────── sanitizer drops (mirrors the sanitizer, cross-checked) ──────────


def test_droppable_figure_content_warns_and_matches_the_sanitizer(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    svg = (pack / "species" / "pebble.svg").read_text(encoding="utf-8")
    dirty = svg.replace(
        "<title>",
        '<text x="1" y="2">a label</text><circle cx="1" cy="2" r="3" onclick="x()"/><title>',
    )
    _write(pack, "species/pebble.svg", dirty)
    report = lint_pack(pack)
    finding = _finding(report, "sanitizer drops [pebble]")
    assert finding.severity == WARN
    assert "<text>" in finding.message  # element dropped with its subtree
    assert "onclick on <circle>" in finding.message  # attribute stripped
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1
    # The mirror never drifts from the sanitizer: what lint names is exactly
    # what sanitize_svg removes, and the pack still loads (the gate strips,
    # it does not reject).
    clean = sanitize_svg(dirty)
    assert "<text" not in clean and "onclick" not in clean


# ────────── phrase overlay shape ──────────


def test_partial_tiny_table_is_an_error_not_a_fallthrough(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    # The tiny table swaps WHOLE on the message path — a missing valence
    # bucket is a KeyError in the room, so lint errors where the loader
    # (correctly, for the sparse tables) waves the pack through.
    _mutate_yaml(pack, "phrases/pebble.yaml", lambda d: d["tiny"].pop("grumpy"))
    report = lint_pack(pack)
    finding = _finding(report, "overlay tiny [pebble]")
    assert finding.severity == ERROR
    assert "grumpy" in finding.message
    assert report.exit_code() == 1


def test_all_asterisk_tiny_bucket_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    # The utterance path filters *...* body lines out of tiny buckets; a
    # bucket with no speakable line would divide by zero.
    _mutate_yaml(
        pack,
        "phrases/pebble.yaml",
        lambda d: d["tiny"].update(grumpy=["*broods, mineral*"]),
    )
    report = lint_pack(pack)
    finding = _finding(report, "overlay tiny [pebble]")
    assert finding.severity == ERROR
    assert "speakable" in finding.message
    assert report.exit_code() == 1


def test_species_without_overlay_warns(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "phrases" / "pebble.yaml").unlink()
    report = lint_pack(pack)
    finding = _finding(report, "phrase overlay [pebble]")
    assert finding.severity == WARN
    assert "cat's voice" in finding.message
    assert report.exit_code() == 0


# ────────── geometry sanity ──────────


def test_geometry_outside_the_pettable_zone_warns(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["geometry"]["tail"].update(xAbove=300))
    report = lint_pack(pack)
    finding = _finding(report, "geometry [pebble]")
    assert finding.severity == WARN
    assert "tail" in finding.message
    assert report.exit_code() == 0


def test_inverted_ear_head_split_warns(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _mutate_yaml(pack, "species/pebble.yaml", lambda d: d["geometry"].update(earBelow=420))
    report = lint_pack(pack)
    finding = _finding(report, "geometry [pebble]")
    assert finding.severity == WARN
    assert "ear zone is empty" in finding.message


# ────────── loader gates run first ──────────


def test_a_gate_violation_is_the_first_and_only_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "pack.yaml").unlink()
    report = lint_pack(pack)
    (finding,) = report.findings
    assert finding.check == "load"
    assert finding.severity == ERROR
    assert "PackManifestError" in finding.message
    assert report.exit_code() == 1


# ────────── lint never mutates ──────────


def test_lint_restores_every_registry_the_loader_touches(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    before = (
        copy.deepcopy(species_mod.SPECIES_REGISTRY),
        species_mod.SPECIES,
        copy.deepcopy(QUIRKS),
        copy.deepcopy(bl.SPECIES_PHRASE_OVERLAYS),
        copy.deepcopy(voice_mod.CLIENT_VOICE),
    )
    lint_pack(pack)
    assert species_mod.SPECIES_REGISTRY == before[0]
    assert species_mod.SPECIES == before[1]
    assert QUIRKS == before[2]
    assert bl.SPECIES_PHRASE_OVERLAYS == before[3]
    assert voice_mod.CLIENT_VOICE == before[4]
    assert LOADED_PACKS == []
    assert PACK_ASSETS == {}
    # And the pack directory is untouched (read-only).
    assert {p.relative_to(pack) for p in pack.rglob("*")} == {
        p.relative_to(FIXTURE_PACK) for p in FIXTURE_PACK.rglob("*")
    }


# ────────── the CLI ──────────


def test_cli_reports_and_exit_codes(tmp_path: Path, capsys) -> None:
    assert pack_lint.main([str(SHIPPED_PACK)]) == 0
    out = capsys.readouterr().out
    assert "PASS  load — pebble v0.1.0" in out
    assert "12 PASS · 1 WARN · 0 ERROR — lints, with warnings" in out
    assert pack_lint.main([str(SHIPPED_PACK), "--strict"]) == 1

    pack = _copy_pack(tmp_path)
    assert pack_lint.main([str(pack)]) == 0
    assert pack_lint.main([str(pack), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "WARN  rig extras [pebble]" in out
    assert "lints, with warnings" in out

    (pack / "pack.yaml").unlink()
    assert pack_lint.main([str(pack)]) == 1
    out = capsys.readouterr().out
    assert "ERROR load" in out
    assert "FAILS lint" in out
