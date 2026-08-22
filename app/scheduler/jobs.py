"""Background jobs. Mood drift, daily outing, busy-mode expiry.

Care rate is approximated from recent buffer events (last 7 days). No tight coupling."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.channels.webapp import channel
from app.config import settings
from app.data.voice import anniversary_fragment as _anniversary_fragment
from app.engine.mood import ACTION_NUDGE, drift, nudge
from app.engine.outings import generate_outing_story
from app.engine.quirks import get_quirk_events, get_scheduler_quirk_effect
from app.memory import core as core_memory
from app.runtime.pet_state import broadcast_scene_payloads
from app.runtime.scene_fx import (
    build_scene_event,
    default_action_scene_fx,
    mood_action_scene_fx,
    record_scene_event,
    set_scene_fx,
)
from app.storage import repo
from app.storage.db import SessionLocal
from app.storage.models import BufferEvent, Moment, Outing
from app.time import home_tz, local_now, to_local, utc_now

log = logging.getLogger(__name__)


async def _care_rate_7d(session, pet_id: str) -> float:
    cutoff = utc_now() - timedelta(days=7)
    q = (
        select(func.count(BufferEvent.id))
        .where(BufferEvent.pet_id == pet_id, BufferEvent.created_at >= cutoff)
    )
    result = await session.execute(q)
    n = result.scalar_one() or 0
    # 0 events/week -> 0.0; ~14 events/week -> 1.0
    return max(0.0, min(1.0, n / 14.0))


async def mood_drift_tick() -> None:
    now = utc_now()
    async with SessionLocal() as session:
        pet_ids = await repo.list_pet_ids(session)
    # Isolate each pet in its own session so one bad row can't abort the tick
    # for every other pet.
    for pid in pet_ids:
        try:
            async with channel.mutation_guard(pid), SessionLocal() as session:
                pet = await repo.get_pet(session, pid)
                if not pet:
                    continue
                await repo.lock_pet_for_mood_update(session, pet)
                care_rate = await _care_rate_7d(session, pid)
                new_mood = drift(repo.read_mood(pet), now, care_rate_7d=care_rate, tz=home_tz())
                # Couple-rhythm overlay: small valence nudge per tick based on
                # how the two humans are showing up async. Subtle by design —
                # one tick won't flip the pet's mood, but lopsided weeks will.
                rhythm = await repo.pet_couple_rhythm_summary(session, pid)
                if rhythm.get("both_recent"):
                    new_mood = nudge(new_mood, valence_delta=1)
                elif rhythm.get("lopsided"):
                    new_mood = nudge(new_mood, valence_delta=-1)
                if (
                    new_mood.arousal != pet.mood_arousal
                    or new_mood.valence != pet.mood_valence
                    or new_mood.animation_state != pet.animation_state
                ):
                    old_mood = repo.read_mood(pet)
                    await repo.write_mood(session, pet, new_mood)
                    facts = await core_memory.all_facts(session, pid)
                    # First settle-into-sleep for a content_sigher is a real milestone.
                    if (
                        "content_sigher" in (pet.quirks or [])
                        and new_mood.animation_state == "sleeping"
                        and old_mood.animation_state != "sleeping"
                    ):
                        await core_memory.note_first(
                            session, pid, "first_sigh_day", local_now().strftime("%Y-%m-%d")
                        )
                    scheduler_quirk = get_scheduler_quirk_effect(
                        old_mood,
                        new_mood,
                        pet.quirks or [],
                        facts=facts,
                        now=local_now(),
                    )
                    scene_event = None
                    if scheduler_quirk is not None:
                        if scheduler_quirk.scene_fx:
                            scene_event = build_scene_event(
                                event_id=f"scheduler:{pid}:{int(now.timestamp() * 1000)}",
                                action=None,
                                actor_user_id=None,
                                animation_state=new_mood.animation_state,
                                modifiers=[scheduler_quirk.scene_fx],
                            )
                        for key, value in (scheduler_quirk.fact_updates or {}).items():
                            await core_memory.set_fact(session, pid, key, value)
                    await session.commit()
                    if scene_event is not None:
                        set_scene_fx(
                            pid,
                            **scheduler_quirk.scene_fx,
                            event_id=scene_event["id"],
                        )
                        record_scene_event(pid, scene_event)
                        await channel.broadcast(pid, {
                            "type": "scene_event",
                            "event": scene_event,
                        })
                    await broadcast_scene_payloads(session, pet)

                    if scheduler_quirk is not None and scheduler_quirk.response_text:
                        await channel.broadcast(pid, {
                            "type": "response",
                            "text": scheduler_quirk.response_text,
                            "is_utterance": False,
                        })

                    # Quirks behaviors
                    quirk_events = get_quirk_events(old_mood, new_mood, pet.quirks or [])
                    for qv in quirk_events:
                        await channel.broadcast(pid, {
                            "type": qv.type,
                            **qv.data,
                        })
                else:
                    await repo.write_mood(session, pet, new_mood)
                    await session.commit()
        except Exception:
            log.exception("mood_drift_tick failed for pet %s", pid)


async def daily_outing_tick() -> None:
    """Post a tiny outing-story fragment once per day per pet."""
    day = local_now().strftime("%Y-%m-%d")
    async with SessionLocal() as session:
        pet_ids = await repo.list_pet_ids(session)
    # Isolate each pet so a bad row can't skip every pet that follows it today.
    for pid in pet_ids:
        try:
            async with channel.mutation_guard(pid), SessionLocal() as session:
                pet = await repo.get_pet(session, pid)
                if not pet:
                    continue
                exists = (
                    await session.execute(
                        select(Outing.id).where(Outing.pet_id == pid, Outing.day == day)
                    )
                ).first()
                if exists:
                    continue
                story = generate_outing_story(pet, day)
                session.add(Outing(pet_id=pid, day=day, story=story))
                await session.commit()
                await channel.broadcast(pid, {
                    "type": "outing",
                    "day": day,
                    "story": story,
                })
        except Exception:
            log.exception("daily_outing_tick failed for pet %s", pid)


ANNIVERSARY_DAYS: list[int] = [30, 60, 100, 180, 365, 730, 1000, 1500, 2000]


async def anniversary_tick() -> None:
    """Daily: for each pet, if days_since_adoption matches a threshold and we
    haven't already marked it, create an anniversary Moment + broadcast a
    milestone WS frame. Idempotent via a core_fact sentinel per threshold."""
    async with SessionLocal() as session:
        pet_ids = await repo.list_pet_ids(session)
    for pid in pet_ids:
        try:
            async with channel.mutation_guard(pid), SessionLocal() as session:
                pet = await repo.get_pet(session, pid)
                if not pet or not pet.adopted_at:
                    continue
                days = (local_now().date() - to_local(pet.adopted_at).date()).days
                if days not in ANNIVERSARY_DAYS:
                    continue
                key = f"anniversary_{days}_seen"
                if await core_memory.get_fact(session, pid, key):
                    continue
                fragment = _anniversary_fragment(days)
                marker = BufferEvent(
                    pet_id=pid,
                    user_id=None,
                    event_type="anniversary",
                    meta={"days": days},
                )
                session.add(marker)
                await session.flush()
                session.add(Moment(
                    pet_id=pid,
                    fragment=fragment,
                    event_type="anniversary",
                    source_event_ids=[marker.id],
                ))
                await core_memory.set_fact(session, pid, key, "yes")
                await session.commit()
                await channel.broadcast(pid, {
                    "type": "milestone",
                    "kind": "anniversary",
                    "event_type": "anniversary",
                    "count": days,
                    "fragment": fragment,
                })
        except Exception:
            log.exception("anniversary_tick failed for pet %s", pid)


# ────────── demo-pet self-play ──────────
# The demo dog (GUEST_PET_ID) has no humans, so without this it would only
# ever drift. One small self-care action per tick keeps it visibly alive for
# guests. Self-care kinds map onto REAL action types so the mood math (nudge
# + pick_animation) and the scene-fx lookup are the exact functions
# /api/action uses — no duplicated behavior. No BufferEvents, no Moments, no
# LLM calls, no response frames: the demo dog never "talks" and its data
# stays clean.

SELF_PLAY_INTERVAL_MINUTES = 67
SELF_PLAY_JITTER_SECONDS = 22 * 60  # 45..89 minutes between ticks — not a metronome

# (action kind, weight). Calm behaviors dominate; zoomies are a rare treat.
_SELF_PLAY_WEIGHTS: list[tuple[str, int]] = [
    ("greet", 4),  # a stretch / shake-off
    ("feed", 3),   # wander to the bowl for a snack
    ("walk", 2),   # a lap around the room
    ("play", 1),   # brief zoomies
]


async def demo_self_play_tick() -> None:
    """One lightweight internal care action on the demo pet, then broadcast.
    Scoped to GUEST_PET_ID only; a no-op when guest mode or the pin is absent.
    Real pets are never touched."""
    if not settings.guest_access_enabled or not settings.guest_pet_id:
        return
    async with (
        channel.mutation_guard(settings.guest_pet_id),
        SessionLocal() as session,
    ):
        pet = await repo.get_pet(session, settings.guest_pet_id)
        if pet is None or (settings.is_prod and not pet.is_demo):
            return
        await repo.lock_pet_for_mood_update(session, pet)
        old_mood = repo.read_mood(pet)
        if old_mood.arousal < 35:
            kind = "greet"  # a sleepy dog only stretches — no midnight zoomies
        else:
            kinds, weights = zip(*_SELF_PLAY_WEIGHTS)
            kind = random.choices(kinds, weights=weights, k=1)[0]
        darousal, dvalence = ACTION_NUDGE.get(kind, (0, 0))
        new_mood = nudge(old_mood, arousal_delta=darousal, valence_delta=dvalence)
        await repo.write_mood(session, pet, new_mood)
        # Same fx precedence as the /api/action path: mood-specific override
        # first, generic action fx as fallback.
        mood_fx = mood_action_scene_fx(kind, new_mood.arousal, new_mood.valence)
        default_fx = default_action_scene_fx(kind)
        modifiers = [mood_fx] if mood_fx else []
        scene_event = build_scene_event(
            event_id=f"demo:{pet.id}:{int(utc_now().timestamp() * 1000)}",
            action=kind,
            actor_user_id=None,
            animation_state=new_mood.animation_state,
            modifiers=modifiers,
            variant="zoomie" if kind == "play" else None,
        )
        legacy_fx = mood_fx or default_fx
        await session.commit()
        if legacy_fx:
            set_scene_fx(pet.id, **legacy_fx, event_id=scene_event["id"])
        record_scene_event(pet.id, scene_event)
        await broadcast_scene_payloads(session, pet)


def start_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=settings.home_tz)
    sched.add_job(
        mood_drift_tick,
        "interval",
        minutes=settings.mood_drift_interval_minutes,
        id="mood_drift",
    )
    sched.add_job(
        daily_outing_tick,
        "cron",
        hour=settings.daily_outing_hour,
        minute=0,
        id="daily_outing",
    )
    sched.add_job(
        anniversary_tick,
        "cron",
        hour=settings.daily_outing_hour,
        minute=5,
        id="anniversary",
    )
    sched.add_job(
        demo_self_play_tick,
        "interval",
        minutes=SELF_PLAY_INTERVAL_MINUTES - SELF_PLAY_JITTER_SECONDS // 60,
        jitter=SELF_PLAY_JITTER_SECONDS * 2,
        id="demo_self_play",
    )
    sched.start()
    log.info("scheduler started")
    return sched
