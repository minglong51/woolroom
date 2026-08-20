"""Pins the room contract: server mapping, ws.js fallback, and fx vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.room_contract import FX_MODES, FX_VOCAB_VERSION, TRACE_CUE_MAP
from app.runtime.shared_trace import build_trace_scene_cue

REPO_ROOT = Path(__file__).resolve().parents[1]

FRESHNESSES = ("fresh", "recent", "earlier")


@pytest.mark.parametrize("event_type", sorted(TRACE_CUE_MAP))
@pytest.mark.parametrize("freshness", FRESHNESSES)
def test_build_trace_scene_cue_matches_contract(event_type: str, freshness: str) -> None:
    """build_trace_scene_cue stays lossless after the TRACE_CUE_MAP move."""
    trace = {
        "event_type": event_type,
        "freshness": freshness,
        "user_id": "partner-id",
        "display_name": "Partner",
        "created_at": "2026-08-19T00:00:00",
        "event_id": 7,
    }
    cue = build_trace_scene_cue(trace, "viewer-id")
    assert cue is not None
    assert cue["mode"] == TRACE_CUE_MAP[event_type]["mode"]
    assert cue["anchor"] == TRACE_CUE_MAP[event_type]["anchor"]


def _parse_ws_fallback_mapping() -> dict[str, dict[str, str]]:
    """Parse the `_deriveSharedTraceCue` fallback object literal out of ws.js.

    Tolerant of whitespace, strict about the block existing and parsing
    non-empty: if the literal's shape changes this fails loudly — that is
    the point (the drift-killer).
    """
    source = (REPO_ROOT / "app/static/js/ws.js").read_text()
    block = re.search(
        r"_deriveSharedTraceCue\(trace\).*?const mapping = \{(.*?)\};",
        source,
        re.S,
    )
    assert block, "ws.js _deriveSharedTraceCue mapping block not found — shape changed?"
    entries = re.findall(
        r'(\w+):\s*\{\s*mode:\s*"([^"]+)",\s*anchor:\s*"([^"]+)"\s*\}',
        block.group(1),
    )
    assert entries, "ws.js fallback mapping parsed empty — shape changed?"
    return {event: {"mode": mode, "anchor": anchor} for event, mode, anchor in entries}


def test_ws_fallback_matches_trace_cue_map() -> None:
    fallback = _parse_ws_fallback_mapping()
    assert set(fallback) == set(TRACE_CUE_MAP), (
        f"ws.js fallback events {sorted(fallback)} != TRACE_CUE_MAP events "
        f"{sorted(TRACE_CUE_MAP)}"
    )
    for event_type, expected in TRACE_CUE_MAP.items():
        assert fallback[event_type] == expected, (
            f"ws.js fallback drifted for {event_type!r}: "
            f"{fallback[event_type]} != {expected}"
        )


def test_scene_fx_modes_exist_in_registry() -> None:
    """Every fx mode referenced in scene_fx.py is a registered FX_MODES entry."""
    source = (REPO_ROOT / "app/runtime/scene_fx.py").read_text()
    referenced = set(re.findall(r'"mode":\s*"([a-z_]+)"', source))
    referenced |= set(re.findall(r'mode == "([a-z_]+)"', source))
    for inline_set in re.findall(r"mode in (\{[^}]*\})", source):
        referenced |= set(re.findall(r'"([a-z_]+)"', inline_set))
    block = re.search(r"replacement_modes = \{(.*?)\}", source, re.S)
    assert block, "replacement_modes set not found in scene_fx.py — shape changed?"
    referenced |= set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert referenced, "no fx modes parsed from scene_fx.py — shape changed?"
    unknown = referenced - set(FX_MODES)
    assert not unknown, (
        f"scene_fx.py references modes missing from FX_MODES: {sorted(unknown)}"
    )


def test_trace_cue_modes_exist_in_registry() -> None:
    cue_modes = {cue["mode"] for cue in TRACE_CUE_MAP.values()}
    unknown = cue_modes - set(FX_MODES)
    assert not unknown, f"TRACE_CUE_MAP modes missing from FX_MODES: {sorted(unknown)}"


def test_fx_vocab_version_is_positive_int() -> None:
    assert isinstance(FX_VOCAB_VERSION, int)
    assert FX_VOCAB_VERSION > 0
