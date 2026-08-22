"""Quirk behavior interpreter.

Quirk definitions are DATA (`app/data/quirks_catalog.py` behavior blocks);
this module is the generic condition-grammar interpreter that evaluates them
for all four channels — pose rig writes, action effects, scheduler effects,
and emitted events. No quirk ids appear here: adding or re-tuning a quirk is
a catalog edit, never an engine edit. The engine's side of the grammar is the
condition vocabulary (`CONDITION_EVALUATORS`), the pose write ops
(`POSE_WRITE_OPS`), and the base pose rule in `get_pose_detail`;
tests/test_quirk_registry.py pins the catalog against all three.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.data.quirks_catalog import QUIRKS
from app.engine.mood import MoodState
from app.time import local_now

@dataclass
class QuirkEvent:
    type: str
    data: dict[str, Any]


@dataclass
class ActionQuirkEffect:
    text: str
    is_utterance: bool = False
    arousal_delta: int = 0
    valence_delta: int = 0
    scene_fx: dict[str, Any] | None = None
    fact_updates: dict[str, str] | None = None


@dataclass
class SchedulerQuirkEffect:
    response_text: str | None = None
    scene_fx: dict[str, Any] | None = None
    fact_updates: dict[str, str] | None = None


def base_pose_detail() -> dict[str, Any]:
    """The neutral rig every render starts from — and the key contract quirk
    pose writes may touch (pinned by tests/test_quirk_registry.py)."""
    return {
        "body_lean": 0,
        "head_shift_y": 0,
        "ear_angle": "neutral",
        "eye_style": "normal",
        "tail_motion": "default",
        "focus_target": None,
    }


# ────────── the condition grammar ──────────
# A `when` dict ANDs its entries. In the evaluation context, `new` is the
# state being entered (the live pose for the pose channel) and `old` the one
# being left. The pose channel is a snapshot — old IS new there, so edge
# conditions simply never fire — and it has no facts or clock, keeping renders
# pure (fact/edge conditions are for the action/scheduler/events channels).

CONDITION_EVALUATORS: dict[str, Callable[[Any, dict[str, Any]], bool]] = {
    "action_in": lambda v, c: c["action"] in v,
    "state_in": lambda v, c: c["new"].animation_state in v,
    "state_not_in": lambda v, c: c["new"].animation_state not in v,
    "valence_gte": lambda v, c: c["new"].valence >= v,
    "valence_lt": lambda v, c: c["new"].valence < v,
    "arousal_gte": lambda v, c: c["new"].arousal >= v,
    "arousal_lt": lambda v, c: c["new"].arousal < v,
    "old_arousal_lt": lambda v, c: c["old"].arousal < v,
    "old_valence_lt": lambda v, c: c["old"].valence < v,
    "enters_state": lambda v, c: (
        c["old"].animation_state != v and c["new"].animation_state == v
    ),
    "fact_day_gate": lambda v, c: c["facts"].get(v) != c["today"],
    "fact_exists": lambda v, c: bool(c["facts"].get(v)),
    "any": lambda v, c: any(_matches(sub, c) for sub in v),
}


def _matches(when: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Every condition in a `when` dict holds. An unknown key raises KeyError
    loudly — the registry test keeps the catalog inside this vocabulary."""
    return all(
        CONDITION_EVALUATORS[key](value, ctx) for key, value in when.items()
    )


def _behavior_rules(channel: str, quirks: list[str]) -> list[dict[str, Any]]:
    """One channel's rules for the pet's quirks, flattened in catalog order
    and stable-sorted by `priority` (default 0, lower fires first). Unknown
    quirk ids are ignored, exactly as the if-chains ignored them."""
    rules = [
        rule
        for quirk_id, definition in QUIRKS.items()
        if quirk_id in quirks
        for rule in definition.get("behavior", {}).get(channel, [])
    ]
    rules.sort(key=lambda rule: rule.get("priority", 0))
    return rules


def _interpolate(value: Any, scope: dict[str, Any]) -> Any:
    """Fill `{...}` placeholders in strings (recursively through dicts) from
    the firing scope. A missing placeholder raises KeyError loudly."""
    if isinstance(value, str):
        return value.format_map(scope)
    if isinstance(value, dict):
        return {key: _interpolate(item, scope) for key, item in value.items()}
    return value


def _effect_scope(
    rule: dict[str, Any], facts: dict[str, str], today: str
) -> dict[str, Any]:
    """Interpolation scope for one firing: the pet's facts, `{today}`, and one
    random pick per declared choice. Choices resolve HERE — after the rule has
    matched — so a rule that doesn't fire never consumes the RNG stream, and
    one pick flows identically into text, scene_fx, and fact_updates.
    `random.choice` stays module-attribute access so tests can monkeypatch it.
    """
    scope: dict[str, Any] = {**facts, "today": today}
    for name, options in rule.get("choices", {}).items():
        scope[name] = random.choice(options)
    return scope


POSE_WRITE_OPS = frozenset({"set", "min", "set_if_default"})


def _apply_pose_write(detail: dict[str, Any], key: str, spec: Any) -> None:
    """One pose write: a bare value is a `set`; a one-entry dict is a merge op."""
    if isinstance(spec, dict):
        op, value = next(iter(spec.items()))
    else:
        op, value = "set", spec
    if op == "set":
        detail[key] = value
    elif op == "min":
        detail[key] = min(detail[key], value)
    elif op == "set_if_default":
        if detail[key] == base_pose_detail()[key]:
            detail[key] = value
    else:
        raise ValueError(f"unknown pose write op: {op!r}")


def get_pose_detail(
    arousal: int,
    valence: int,
    animation_state: str,
    quirks: list[str],
) -> dict[str, Any]:
    detail = base_pose_detail()
    pose = SimpleNamespace(
        arousal=arousal, valence=valence, animation_state=animation_state
    )
    ctx: dict[str, Any] = {
        "action": None,
        "old": pose,
        "new": pose,
        "facts": {},
        "today": None,
    }
    for rule in _behavior_rules("pose", quirks):
        if _matches(rule["when"], ctx):
            for key, spec in rule["write"].items():
                _apply_pose_write(detail, key, spec)
    # Base engine rule, not a quirk: high arousal wags any tail no quirk
    # has claimed.
    if arousal >= 70 and detail["tail_motion"] == "default":
        detail["tail_motion"] = "fast"
    return detail


def get_pose_detail_for_pet(pet: Any) -> dict[str, Any]:
    return get_pose_detail(
        arousal=pet.mood_arousal,
        valence=pet.mood_valence,
        animation_state=pet.animation_state,
        quirks=pet.quirks or [],
    )


def get_action_quirk_effect(
    action: str,
    old_state: MoodState,
    new_state: MoodState,
    quirks: list[str],
    facts: dict[str, str] | None = None,
    now: datetime | None = None,
) -> ActionQuirkEffect | None:
    facts = facts or {}
    now = now or local_now()
    today = now.strftime("%Y-%m-%d")
    ctx: dict[str, Any] = {
        "action": action,
        "old": old_state,
        "new": new_state,
        "facts": facts,
        "today": today,
    }
    for rule in _behavior_rules("action", quirks):
        if _matches(rule["when"], ctx):
            scope = _effect_scope(rule, facts, today)
            return ActionQuirkEffect(
                text=_interpolate(rule["text"], scope),
                arousal_delta=rule.get("arousal_delta", 0),
                valence_delta=rule.get("valence_delta", 0),
                scene_fx=(
                    _interpolate(rule["scene_fx"], scope)
                    if "scene_fx" in rule
                    else None
                ),
                fact_updates=(
                    _interpolate(rule["fact_updates"], scope)
                    if "fact_updates" in rule
                    else None
                ),
            )
    return None


def get_scheduler_quirk_effect(
    old_state: MoodState,
    new_state: MoodState,
    quirks: list[str],
    facts: dict[str, str] | None = None,
    now: datetime | None = None,
) -> SchedulerQuirkEffect | None:
    facts = facts or {}
    now = now or local_now()
    today = now.strftime("%Y-%m-%d")
    ctx: dict[str, Any] = {
        "action": None,
        "old": old_state,
        "new": new_state,
        "facts": facts,
        "today": today,
    }
    for rule in _behavior_rules("scheduler", quirks):
        if _matches(rule["when"], ctx):
            scope = _effect_scope(rule, facts, today)
            return SchedulerQuirkEffect(
                response_text=(
                    _interpolate(rule["text"], scope) if "text" in rule else None
                ),
                scene_fx=(
                    _interpolate(rule["scene_fx"], scope)
                    if "scene_fx" in rule
                    else None
                ),
                fact_updates=(
                    _interpolate(rule["fact_updates"], scope)
                    if "fact_updates" in rule
                    else None
                ),
            )
    return None


def get_quirk_events(
    old_state: MoodState,
    new_state: MoodState,
    quirks: list[str]
) -> list[QuirkEvent]:
    ctx: dict[str, Any] = {
        "action": None,
        "old": old_state,
        "new": new_state,
        "facts": {},
        "today": None,
    }
    events = []
    for rule in _behavior_rules("events", quirks):
        if _matches(rule["when"], ctx):
            emit = rule["emit"]
            events.append(QuirkEvent(type=emit["type"], data=dict(emit["data"])))
    return events
