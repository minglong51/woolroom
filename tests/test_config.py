"""Household-shape config: pair-shaped by design, fail-closed at validation.

woolroom Phase 0 (docs/design/woolroom-extraction-2026-08-17.md §3.3): the
two-human couple is a semantic, not a default. HOUSEHOLD_SIZE is pinned to 2 —
N-human is a future designed mode (family mode, woolroom v2), not a config
value. Rooms and quirk picks are tunable but never below 1.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(**({"_env_file": None} | overrides))


def test_defaults_preserve_pair_shape() -> None:
    s = _settings()
    assert s.household_size == 2
    assert s.max_rooms_per_household == 2
    assert s.quirk_pick_count == 2


@pytest.mark.parametrize("size", [0, 1, 3])
def test_household_size_is_pinned_to_two(size: int) -> None:
    with pytest.raises(ValueError, match="pair-shaped by design"):
        _settings(household_size=size)


def test_boot_refuses_non_pair_household_env(monkeypatch) -> None:
    """The env var reaches the same gate: boot (config import) fails loudly."""
    monkeypatch.setenv("HOUSEHOLD_SIZE", "3")
    sys.modules.pop("app.config", None)
    with pytest.raises(ValueError, match="pair-shaped by design"):
        importlib.import_module("app.config")


def test_rooms_and_quirk_picks_refuse_zero() -> None:
    with pytest.raises(ValueError, match="MAX_ROOMS_PER_HOUSEHOLD must be at least 1"):
        _settings(max_rooms_per_household=0)
    with pytest.raises(ValueError, match="QUIRK_PICK_COUNT must be at least 1"):
        _settings(quirk_pick_count=0)


def test_rooms_and_quirk_picks_accept_other_counts() -> None:
    s = _settings(max_rooms_per_household=3, quirk_pick_count=1)
    assert s.max_rooms_per_household == 3
    assert s.quirk_pick_count == 1
