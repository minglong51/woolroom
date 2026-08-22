"""Temporary in-process scene effects.

These effects are intentionally short-lived, cosmetic hints that make recent
quirk behavior visible in the shared scene without introducing schema churn.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.data.body_language import bucket_arousal, bucket_valence
from app.time import utc_now

_scene_fx: dict[str, dict[str, Any]] = {}
_scene_events: dict[str, list[dict[str, Any]]] = {}

ACTION_SCENE_FX: dict[str, dict[str, Any]] = {
    "greet": {"mode": "greet", "duration_ms": 2600},
    "pet": {"mode": "petting", "duration_ms": 2400},
    "feed": {"mode": "kibble", "duration_ms": 5600},
    "walk": {"mode": "leash_tug", "duration_ms": 5600},
    "call": {"mode": "call_ring", "duration_ms": 3400},
    "message": {"mode": "message_ping", "duration_ms": 2600},
    # play → zoomie: the pet tears around the room. ~5.5s gives enough time
    # for the user to register the running motion before it settles back to
    # idle. Same fx mode as the zoomie_initiator quirk uses.
    "play": {"mode": "zoomie", "duration_ms": 5500},
}


# Mood-specific overrides: action × arousal_bucket × valence_bucket → fx.
# Looked up before the generic ACTION_SCENE_FX so a grumpy pet doesn't read the
# same as a content one. Keep entries narrow — generic action fx covers the rest.
MOOD_ACTION_SCENE_FX: dict[tuple[str, str, str], dict[str, Any]] = {
    ("pet", "low", "grumpy"): {"mode": "flinch_away", "duration_ms": 1800},
    ("pet", "med", "grumpy"): {"mode": "flinch_away", "duration_ms": 1800},
    ("pet", "high", "grumpy"): {"mode": "flinch_away", "duration_ms": 1800},
    ("pet", "low", "content"): {"mode": "petting_melt", "duration_ms": 2800},
    ("message", "med", "neutral"): {"mode": "head_tilt", "duration_ms": 2200},
    ("message", "high", "neutral"): {"mode": "head_tilt", "duration_ms": 2200},
    ("message", "med", "content"): {"mode": "head_tilt", "duration_ms": 2200},
    # Note: previously these high-arousal "call" cases were overridden to
    # alert_perk, but that path's deltas are smaller than the Phase 1
    # call_ring response. A user pressing "call" expects the same visible
    # summoning response regardless of mood — let the LLM-generated text
    # carry the mood nuance, not the FX magnitude. Default call_ring path
    # applies for all moods now.
    ("greet", "low", "content"): {"mode": "sigh_settle", "duration_ms": 2400},
    ("greet", "low", "neutral"): {"mode": "sigh_settle", "duration_ms": 2400},
}


def default_action_scene_fx(action: str) -> dict[str, Any] | None:
    payload = ACTION_SCENE_FX.get(action)
    return dict(payload) if payload else None


def mood_action_scene_fx(
    action: str, arousal: int, valence: int
) -> dict[str, Any] | None:
    key = (action, bucket_arousal(arousal), bucket_valence(valence))
    payload = MOOD_ACTION_SCENE_FX.get(key)
    return dict(payload) if payload else None


def set_scene_fx(
    pet_id: str,
    *,
    mode: str,
    duration_ms: int,
    item: str | None = None,
    **extra: Any,
) -> None:
    expires_at = utc_now() + timedelta(milliseconds=duration_ms)
    payload: dict[str, Any] = {
        "mode": mode,
        "duration_ms": duration_ms,
        "expires_at": expires_at,
    }
    if item:
        payload["item"] = item
    payload.update(extra)
    _scene_fx[pet_id] = payload


def compile_scene_plan(
    action: str | None,
    modifiers: list[dict[str, Any]],
    variant: str | None = None,
) -> list[dict[str, Any]]:
    replacement_modes = {
        "ignored",
        "threshold_refusal",
        "lean_in",
        "sigh_settle",
        "flinch_away",
        "petting",
        "petting_melt",
        "petting_head",
        "petting_ear",
        "petting_tail",
        "petting_belly",
    }
    replacement_index: int | None = None
    for index, modifier in enumerate(modifiers):
        mode = modifier.get("mode")
        if (
            mode == "ignored"
            or (action == "walk" and mode == "threshold_refusal")
            or (action == "greet" and mode in {"lean_in", "sigh_settle"})
            or (action == "pet" and mode in replacement_modes)
            or (action == "call" and mode == "side_eye")
        ):
            replacement_index = index
            break

    plan: list[dict[str, Any]] = []
    if replacement_index is not None:
        plan.append({**modifiers[replacement_index], "relation": "replace"})
    elif action:
        base = default_action_scene_fx(action) or {}
        plan.append({
            "mode": "zoomie" if action == "play" and variant == "zoomie" else f"action:{action}",
            "relation": "base",
            "duration_ms": int(base.get("duration_ms", 0)),
        })

    for index, modifier in enumerate(modifiers):
        if index == replacement_index:
            continue
        mode = modifier.get("mode")
        if mode == "head_tilt" or (action == "message" and mode == "side_eye"):
            relation = "overlay"
        else:
            relation = "after"
        plan.append({**modifier, "relation": relation})
    return plan


def scene_plan_duration(plan: list[dict[str, Any]]) -> int:
    primary = [
        int(step.get("duration_ms", 0))
        for step in plan
        if step.get("relation") in {"base", "replace"}
    ]
    overlays = [
        int(step.get("duration_ms", 0))
        for step in plan
        if step.get("relation") == "overlay"
    ]
    after = sum(
        int(step.get("duration_ms", 0))
        for step in plan
        if step.get("relation") == "after"
    )
    return max(primary + overlays, default=0) + after


def build_scene_event(
    *,
    event_id: str,
    action: str | None,
    actor_user_id: str | None,
    animation_state: str,
    modifiers: list[dict[str, Any]] | None = None,
    origin_id: str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    normalized = [dict(modifier) for modifier in (modifiers or []) if modifier]
    plan = compile_scene_plan(action, normalized, variant)
    duration_ms = scene_plan_duration(plan)
    payload: dict[str, Any] = {
        "id": event_id,
        "action": action,
        "actor_user_id": actor_user_id,
        "started_at": f"{utc_now().isoformat(timespec='milliseconds')}Z",
        "duration_ms": duration_ms,
        "remaining_ms": duration_ms,
        "animation_state": animation_state,
        "modifiers": normalized,
        "plan": plan,
    }
    if origin_id:
        payload["origin_id"] = origin_id
    if variant:
        payload["variant"] = variant
    return payload


def record_scene_event(pet_id: str, event: dict[str, Any]) -> None:
    now = utc_now()
    active = [
        stored
        for stored in _scene_events.get(pet_id, [])
        if stored["expires_at"] > now
    ]
    stored = dict(event)
    stored["expires_at"] = now + timedelta(
        milliseconds=max(0, int(event.get("duration_ms", 0)))
    )
    active.append(stored)
    _scene_events[pet_id] = active[-8:]


def current_scene_events(pet_id: str) -> list[dict[str, Any]]:
    now = utc_now()
    active = [
        stored
        for stored in _scene_events.get(pet_id, [])
        if stored["expires_at"] > now
    ]
    if not active:
        _scene_events.pop(pet_id, None)
        return []
    _scene_events[pet_id] = active
    payloads: list[dict[str, Any]] = []
    for stored in active:
        payload = {k: v for k, v in stored.items() if k != "expires_at"}
        payload["remaining_ms"] = int(
            (stored["expires_at"] - now).total_seconds() * 1000
        )
        payloads.append(payload)
    return payloads


def current_scene_fx(pet_id: str) -> dict[str, Any] | None:
    fx = _scene_fx.get(pet_id)
    if not fx:
        return None
    remaining_ms = int((fx["expires_at"] - utc_now()).total_seconds() * 1000)
    if remaining_ms <= 0:
        _scene_fx.pop(pet_id, None)
        return None
    payload = {k: v for k, v in fx.items() if k != "expires_at"}
    payload["remaining_ms"] = remaining_ms
    return payload
