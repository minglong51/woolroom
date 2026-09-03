"""Shared builder for the 'pet_state' payload used by REST, WS initial push,
and scheduler broadcasts. One place so adding a field doesn't mean chasing
three call sites."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.webapp import channel
from app.data.species import pronoun_for
from app.engine.aging import (
    pet_age_years,
    life_stage,
    render_scale,
    stage_proportions,
)
from app.engine.quirks import get_pose_detail_for_pet
from app.memory import core as core_memory
from app.runtime import visits
from app.runtime.scene_fx import current_scene_events, current_scene_fx
from app.runtime.shared_trace import build_trace_scene_cue
from app.storage import repo
from app.storage.models import Pet
from app.time import utc_now

# A cat eats roughly twice a day; past six hours the bowl starts calling.
HUNGRY_AFTER_MINUTES = 360


def _household_names(facts: dict[str, str]) -> list[str]:
    adopted_by = facts.get("adopted_by", "")
    if not adopted_by:
        return []
    return [name.strip() for name in adopted_by.split(",") if name.strip()]


def _fed_minutes_ago(facts: dict[str, str]) -> int | None:
    raw = facts.get("last_fed_at")
    if not raw:
        return None
    try:
        fed_at = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
    return max(0, int((utc_now() - fed_at).total_seconds() // 60))


# Public alias for the rooms summary in /api/me.
fed_minutes_ago = _fed_minutes_ago


async def build_scene_payload(
    session: AsyncSession,
    pet: Pet,
    *,
    current_user_id: str | None = None,
) -> dict:
    """Full pet_state dict. Requires an open session for participant/trace/fact queries."""
    facts = await core_memory.all_facts(session, pet.id)
    viewer_aliases: dict[str, str] = {}
    if current_user_id:
        from app.storage.models import User as _User
        viewer = await session.get(_User, current_user_id)
        if viewer and isinstance(viewer.partner_aliases, dict):
            viewer_aliases = viewer.partner_aliases
    shared_trace = await repo.recent_shared_trace(session, pet.id)
    room_notes = await repo.recent_room_notes(
        session,
        pet.id,
        current_user_id=current_user_id,
        limit=3,
        viewer_aliases=viewer_aliases,
    )
    partner_traces = await repo.recent_partner_traces(
        session,
        pet.id,
        current_user_id=current_user_id,
    )
    partner_trace_cues = [
        cue for cue in (
            build_trace_scene_cue(t, current_user_id) for t in partner_traces
        ) if cue
    ]
    absence_minutes = await repo.partner_absence_minutes(
        session,
        pet.id,
        current_user_id=current_user_id,
    )
    viewer_minutes = await repo.viewer_last_action_minutes(
        session,
        pet.id,
        current_user_id=current_user_id,
    )
    together_recent = (
        viewer_minutes is not None
        and absence_minutes is not None
        and viewer_minutes < 60
        and absence_minutes < 60
    )
    lopsided_hours = None
    if viewer_minutes is not None and absence_minutes is not None:
        gap = abs(viewer_minutes - absence_minutes)
        # Only flag lopsided when one side has been silent for > 4h AND the
        # gap between the two sides is at least 2h — a fresh interaction by
        # one partner shouldn't immediately flip the room "lopsided".
        if max(viewer_minutes, absence_minutes) > 240 and gap > 120:
            lopsided_hours = max(viewer_minutes, absence_minutes) // 60
    couple_rhythm = {
        "viewer_minutes": viewer_minutes,
        "partner_minutes": absence_minutes,
        "together_recent": together_recent,
        "lopsided_hours": lopsided_hours,
    }
    stage = life_stage(pet.adopted_at)
    fed_minutes = _fed_minutes_ago(facts)
    sibling = await repo.get_household_sibling(session, pet)
    visit = visits.visit_for(pet.id)
    visit_block: dict | None = None
    if visit is not None:
        if visit["role"] == "host":
            visitor = await repo.get_pet(session, visit["visitor_pet_id"])
            if visitor is not None:
                visit_block = {
                    **visit,
                    "visitor": {
                        "id": visitor.id,
                        "name": visitor.name,
                        "species": visitor.species,
                        "coat": visitor.coat,
                        "animation_state": visitor.animation_state,
                        "render_scale": render_scale(life_stage(visitor.adopted_at)),
                    },
                }
        else:
            host = await repo.get_pet(session, visit["host_pet_id"])
            visit_block = {
                **visit,
                "host_name": host.name if host else None,
            }
    return {
        "id": pet.id,
        "name": pet.name,
        "species": pet.species,
        "pronoun": pronoun_for(pet.species),
        "quirks": pet.quirks,
        "coat": pet.coat,
        "pose_detail": get_pose_detail_for_pet(pet),
        "animation_state": pet.animation_state,
        "mood_arousal": pet.mood_arousal,
        "mood_valence": pet.mood_valence,
        "adopted_at": pet.adopted_at.isoformat() if pet.adopted_at else None,
        "life_stage": stage,
        "pet_age_years": round(pet_age_years(pet.adopted_at), 2),
        "render_scale": render_scale(stage),
        "stage_proportions": stage_proportions(stage),
        # The hunger cue: never-fed counts as hungry (a fresh pet discovering
        # the bowl is the onboarding nudge, not an error state).
        "fed_minutes_ago": fed_minutes,
        "hungry": fed_minutes is None or fed_minutes >= HUNGRY_AFTER_MINUTES,
        "participant_count": await repo.participant_count(session, pet.id),
        "online_count": channel.online_count(pet.id),
        # The door in the wall: the sibling room's heartbeat (who/what lives
        # there, awake or asleep, anyone visiting it right now).
        "sibling": (
            {
                "id": sibling.id,
                "name": sibling.name,
                "species": sibling.species,
                "coat": sibling.coat,
                "animation_state": sibling.animation_state,
                "online_count": channel.online_count(sibling.id),
            }
            if sibling is not None
            else None
        ),
        "visit": visit_block,
        "household_names": _household_names(facts),
        "shared_trace": shared_trace,
        "room_notes": room_notes,
        "shared_trace_cue": build_trace_scene_cue(shared_trace, current_user_id),
        "partner_traces": partner_traces,
        "partner_trace_cues": partner_trace_cues,
        "return_cue": next(
            (
                cue
                for cue in partner_trace_cues
                if cue.get("intensity") in {"strong", "soft"}
            ),
            None,
        ),
        "partner_absence_minutes": absence_minutes,
        "couple_rhythm": couple_rhythm,
        "viewer_partner_aliases": viewer_aliases,
        "origin_line": core_memory.origin_line(facts, pet.adopted_at),
        "hidden_thing": facts.get("hidden_thing"),
        "scene_fx": current_scene_fx(pet.id),
        "scene_events": current_scene_events(pet.id),
        # Per-deploy version string. Client compares against the value it
        # booted with; mismatch shows a soft "refresh for the new room"
        # pill so long-lived open tabs catch up without a hard refresh.
        "app_version": _app_version(),
    }


def _app_version() -> str:
    # Lazy import so this module stays importable without app.main loaded.
    try:
        from app.main import APP_VERSION
        return APP_VERSION
    except Exception:
        return "dev"


# ────────── read-only guest scene ──────────

# Fields copied verbatim into the guest DTO. Nested scene state has its own
# allowlists below; every other full-scene field is private by default.
GUEST_SCENE_VALUE_KEYS = frozenset({
    "id",
    "name",
    "species",
    "pronoun",
    "quirks",
    "coat",
    "pose_detail",
    "animation_state",
    "mood_arousal",
    "mood_valence",
    "adopted_at",
    "life_stage",
    "pet_age_years",
    "render_scale",
    "stage_proportions",
    "fed_minutes_ago",
    "hungry",
    "app_version",
})

GUEST_SCENE_EVENT_KEYS = frozenset({
    "id",
    "action",
    "started_at",
    "duration_ms",
    "remaining_ms",
    "animation_state",
    "variant",
})
GUEST_SCENE_STEP_KEYS = frozenset({"mode", "duration_ms", "relation"})
GUEST_SCENE_FX_KEYS = frozenset({"mode", "duration_ms", "remaining_ms", "event_id"})
GUEST_VISIT_KEYS = frozenset({"id", "role"})
GUEST_VISITOR_KEYS = frozenset({
    "id",
    "name",
    "species",
    "coat",
    "animation_state",
    "render_scale",
})


def sanitize_scene_event(event: dict) -> dict:
    sanitized = {
        key: value
        for key, value in event.items()
        if key in GUEST_SCENE_EVENT_KEYS
    }
    for key in ("modifiers", "plan"):
        steps = event.get(key)
        if isinstance(steps, list):
            sanitized[key] = [
                {
                    step_key: step_value
                    for step_key, step_value in step.items()
                    if step_key in GUEST_SCENE_STEP_KEYS
                }
                for step in steps
                if isinstance(step, dict)
            ]
    return sanitized


def sanitize_scene_payload(payload: dict) -> dict:
    """Project a full pet_state payload through the explicit guest allowlist."""
    sanitized = {
        key: value
        for key, value in payload.items()
        if key in GUEST_SCENE_VALUE_KEYS
    }
    scene_fx = payload.get("scene_fx")
    if isinstance(scene_fx, dict):
        sanitized["scene_fx"] = {
            key: value
            for key, value in scene_fx.items()
            if key in GUEST_SCENE_FX_KEYS
        }
    elif scene_fx is None and "scene_fx" in payload:
        sanitized["scene_fx"] = None
    scene_events = payload.get("scene_events")
    if isinstance(scene_events, list):
        sanitized["scene_events"] = [
            sanitize_scene_event(event)
            for event in scene_events
            if isinstance(event, dict)
        ]
    visit = payload.get("visit")
    if (
        isinstance(visit, dict)
        and visit.get("role") == "host"
        and isinstance(visit.get("visitor"), dict)
    ):
        sanitized["visit"] = {
            **{
                key: value
                for key, value in visit.items()
                if key in GUEST_VISIT_KEYS
            },
            "visitor": {
                key: value
                for key, value in visit["visitor"].items()
                if key in GUEST_VISITOR_KEYS
            },
        }
    elif "visit" in payload:
        sanitized["visit"] = None
    return sanitized


async def build_guest_scene_payload(session: AsyncSession, pet: Pet) -> dict:
    """Guest-safe pet_state: build the normal payload, then allowlist it."""
    return sanitize_scene_payload(await build_scene_payload(session, pet))


async def broadcast_scene_payloads(
    session: AsyncSession,
    pet: Pet,
) -> None:
    async with channel.delivery_guard(pet.id):
        while True:
            await session.refresh(pet)
            events_by_user: dict[str, dict] = {}
            for user_id in channel.connected_user_ids(pet.id):
                payload = await build_scene_payload(
                    session,
                    pet,
                    current_user_id=user_id,
                )
                events_by_user[user_id] = {"type": "pet_state", "pet": payload}
            guest_event = None
            if channel.guest_count(pet.id):
                payload = await build_guest_scene_payload(session, pet)
                guest_event = {"type": "pet_state", "pet": payload}
            participants_changed = await channel._send_personalized(
                pet.id,
                events_by_user,
                guest_event,
            )
            if not participants_changed:
                return


async def resolve_guest_pet(session: AsyncSession) -> Pet | None:
    """Which pet a guest watches. Privacy boundary, two modes:

    - GUEST_PET_ID set   → ONLY that pet. No fallback: a misconfigured id must
      never silently expose a real household's pet. Returns None on a miss.
    - GUEST_PET_ID unset → first pet (dev convenience). In prod, seed the demo
      pet (scripts/seed_demo_pet.py) and pin its id instead.

    Callers turn None into a clean 404 / WS close — never a crash.
    """
    from app.config import settings
    if settings.guest_pet_id:
        pet = await repo.get_pet(session, settings.guest_pet_id)
        if pet is None or (settings.is_prod and not pet.is_demo):
            return None
        return pet
    if settings.is_prod:
        return None
    return await repo.first_pet(session)
