"""Contract test for the quirk registry: every quirk is catalog data the
interpreter (app/engine/quirks.py) recognizes, and every fx mode it names is
in the room contract. This is the pack-authoring gate in miniature — a quirk
that drifts outside the grammar fails here, not in production."""

from __future__ import annotations

import pytest

from app.data.quirks_catalog import QUIRKS, validate_quirks
from app.engine import quirks as engine
from app.engine.mood import MoodState
from app.room_contract import FX_MODES
from app.time import utc_now

KNOWN_QUIRK_IDS = {
    "hides_small_things",
    "fixated_watcher",
    "threshold_refuser",
    "content_sigher",
    "one_eye_napper",
    "lean_in_greeter",
    "zoomie_initiator",
    "side_eye_judge",
}

BEHAVIOR_CHANNELS = ("pose", "action", "scheduler", "events")


def _iter_rules():
    for quirk_id, definition in QUIRKS.items():
        behavior = definition.get("behavior", {})
        unknown_channels = set(behavior) - set(BEHAVIOR_CHANNELS)
        assert not unknown_channels, (
            f"{quirk_id} wires unknown behavior channels: {sorted(unknown_channels)}"
        )
        for channel, rules in behavior.items():
            assert isinstance(rules, list), f"{quirk_id}.{channel} must be a rule list"
            for rule in rules:
                yield quirk_id, channel, rule


def _condition_keys(when: dict):
    for key, value in when.items():
        yield key
        if key == "any":
            for sub in value:
                yield from _condition_keys(sub)


def test_every_registered_quirk_is_in_the_catalog_and_wired() -> None:
    """The catalog and the known quirk set are exactly coextensive, and every
    catalog entry carries real behavior wiring (not just a label)."""
    assert set(QUIRKS) == KNOWN_QUIRK_IDS
    for quirk_id, definition in QUIRKS.items():
        assert definition.get("behavior"), f"{quirk_id} has no behavior wiring"


def test_every_condition_key_is_recognized_by_the_interpreter() -> None:
    for quirk_id, channel, rule in _iter_rules():
        unknown = set(_condition_keys(rule["when"])) - set(engine.CONDITION_EVALUATORS)
        assert not unknown, (
            f"{quirk_id}.{channel} uses conditions the interpreter does not "
            f"recognize: {sorted(unknown)}"
        )


def test_every_scene_fx_mode_is_in_the_room_contract() -> None:
    for quirk_id, channel, rule in _iter_rules():
        scene_fx = rule.get("scene_fx")
        if scene_fx is not None:
            assert scene_fx["mode"] in FX_MODES, (
                f"{quirk_id}.{channel} fx mode {scene_fx['mode']!r} is not in FX_MODES"
            )


def test_pose_writes_stay_on_the_rig() -> None:
    rig_keys = set(engine.base_pose_detail())
    for quirk_id, channel, rule in _iter_rules():
        if channel != "pose":
            continue
        for key, spec in rule["write"].items():
            assert key in rig_keys, f"{quirk_id} writes unknown rig key {key!r}"
            if isinstance(spec, dict):
                unknown_ops = set(spec) - engine.POSE_WRITE_OPS
                assert not unknown_ops, (
                    f"{quirk_id} uses unknown pose write ops: {sorted(unknown_ops)}"
                )


def test_validate_quirks_enforces_exactly_two_known_ids() -> None:
    picked = ["content_sigher", "lean_in_greeter"]
    assert validate_quirks(picked) == picked
    with pytest.raises(ValueError, match="exactly 2"):
        validate_quirks(["content_sigher"])
    with pytest.raises(ValueError, match="exactly 2"):
        validate_quirks(["content_sigher", "lean_in_greeter", "side_eye_judge"])
    with pytest.raises(ValueError, match="Unknown quirks"):
        validate_quirks(["content_sigher", "flies"])


def test_threshold_refusal_still_beats_stash_on_a_low_mood_walk() -> None:
    """The one place rule order is not catalog order: both walk rules match
    (low old mood + day-gate open + alert new state), and the refusal must
    win — the if-chain order the `priority` field preserves."""
    now = utc_now()
    old = MoodState(arousal=30, valence=60, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=45, valence=64, animation_state="alert", last_drift_at=now)
    quirks = ["hides_small_things", "threshold_refuser"]

    effect = engine.get_action_quirk_effect("walk", old, new, quirks, facts={}, now=now)

    assert effect is not None
    assert effect.text == "*sits down at the threshold, and the doorway loses the argument*"
    assert effect.scene_fx == {"mode": "threshold_refusal", "duration_ms": 4400}
