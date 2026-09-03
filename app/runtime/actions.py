"""The action orchestration: one human verb in, one committed event +
mood/quirk/fx resolution + post-commit broadcasts out.

Extracted verbatim from api/http.py — this is application core, not
routing: idempotency receipts (HMAC-fingerprinted origin_id), the buffer
write, mood nudge + quirk effect, scene-fx modifier resolution, milestone
promotion, the respond() call, and the four room broadcasts. The HTTP
route holds the mutation guard and the dependencies; errors stay
HTTPException because its 409/422 shapes ARE the endpoint's contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets as _secrets
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.webapp import channel
from app.config import settings
from app.engine.mood import ACTION_NUDGE, nudge
from app.engine.quirks import get_action_quirk_effect
from app.memory import buffer, core as core_memory, moments
from app.runtime.pet_state import broadcast_scene_payloads, build_scene_payload
from app.runtime.respond import Response as RuntimeResponse
from app.runtime.respond import decide_ignore, respond
from app.runtime.scene_fx import (
    build_scene_event,
    default_action_scene_fx,
    mood_action_scene_fx,
    record_scene_event,
    set_scene_fx,
)
from app.storage import repo
from app.storage.models import Pet, User
from app.time import local_now, to_local, utc_now

log = logging.getLogger(__name__)


class ActionIn(BaseModel):
    type: Literal["greet", "feed", "pet", "walk", "call", "message", "play"]
    text: str | None = Field(default=None, max_length=200)
    variant: Literal["zoomie"] | None = None
    # Where on the pet the action targeted — only meaningful for "pet".
    # Frontend hit-detects which body region was clicked and tags the action.
    spot: Literal["head", "body", "tail", "ear", "belly"] | None = Field(default=None)
    origin_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )


async def perform_action(
    body: ActionIn,
    user: User,
    pet: Pet,
    session: AsyncSession,
) -> dict:
    if body.variant and body.type != "play":
        raise HTTPException(status_code=422, detail="variant is only valid for play")
    await repo.lock_pet_for_mood_update(session, pet)
    request_payload = json.dumps(
        body.model_dump(exclude={"origin_id"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    request_fingerprint = hmac.new(
        settings.secret_key.encode(),
        request_payload,
        hashlib.sha256,
    ).hexdigest()
    if body.origin_id:
        receipt = await buffer.get_action_receipt(
            session,
            pet.id,
            user.id,
            body.origin_id,
        )
        if receipt is not None:
            if not _secrets.compare_digest(
                receipt.request_fingerprint,
                request_fingerprint,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="origin_id was already used for a different action",
                )
            await session.commit()
            await session.refresh(pet)
            payload = await build_scene_payload(
                session,
                pet,
                current_user_id=user.id,
            )
            scene_event_id = f"action:{receipt.event_id}"
            response_scene_event = next(
                (
                    event
                    for event in payload.get("scene_events", [])
                    if event.get("id") == scene_event_id
                ),
                None,
            )
            return {
                "ok": True,
                "pet": payload,
                "response": None,
                "scene_event": response_scene_event,
            }
    # 1) write buffer event
    event_meta: dict | None = None
    if body.text:
        event_meta = {"text": body.text}
    if body.spot and body.type == "pet":
        event_meta = {**(event_meta or {}), "spot": body.spot}
    if body.origin_id:
        event_meta = {**(event_meta or {}), "origin_id": body.origin_id}
    previous_action = await buffer.latest_event_of_types(
        session,
        pet.id,
        ACTION_NUDGE,
    )
    can_ignore = (
        previous_action is not None
        and to_local(previous_action.created_at).date() == local_now().date()
        and previous_action.meta.get("ignored") is False
    )
    event = await buffer.add_event(
        session,
        pet.id,
        body.type,
        user_id=user.id,
        meta=event_meta,
    )
    if body.type == "feed":
        # The bowl's memory: the hunger cue (bowl glow + bowl-gaze) reads this.
        await core_memory.set_fact(
            session,
            pet.id,
            "last_fed_at",
            f"{utc_now().isoformat(timespec='seconds')}Z",
        )
    if body.origin_id:
        await buffer.add_action_receipt(
            session,
            pet_id=pet.id,
            user_id=user.id,
            origin_id=body.origin_id,
            event_id=event.id,
            request_fingerprint=request_fingerprint,
        )
    # 2) mood nudge (small)
    old_mood = repo.read_mood(pet)
    darousal, dvalence = ACTION_NUDGE.get(body.type, (0, 0))
    mood = nudge(old_mood, arousal_delta=darousal, valence_delta=dvalence)
    facts = await core_memory.all_facts(session, pet.id)
    quirk_effect = get_action_quirk_effect(
        body.type,
        old_mood,
        mood,
        pet.quirks or [],
        facts=facts,
    )
    if quirk_effect is not None:
        mood = nudge(
            mood,
            arousal_delta=quirk_effect.arousal_delta,
            valence_delta=quirk_effect.valence_delta,
        )
    await repo.write_mood(session, pet, mood)
    modifiers: list[dict] = []
    ignored = False
    if quirk_effect is not None:
        if quirk_effect.scene_fx:
            modifiers.append(dict(quirk_effect.scene_fx))
        for key, value in (quirk_effect.fact_updates or {}).items():
            await core_memory.set_fact(session, pet.id, key, value)
    else:
        ignored = can_ignore and decide_ignore(pet)
        if ignored:
            modifiers.append({"mode": "ignored", "duration_ms": 1800})
        elif body.type == "pet" and body.spot and body.spot != "body":
            spot_fx_map = {
                "head": {"mode": "petting_head", "duration_ms": 2400},
                "ear": {"mode": "petting_ear", "duration_ms": 2000},
                "tail": {"mode": "petting_tail", "duration_ms": 2400},
                "belly": {"mode": "petting_belly", "duration_ms": 3000},
            }
            modifiers.append(spot_fx_map[body.spot])
        else:
            mood_fx = mood_action_scene_fx(body.type, mood.arousal, mood.valence)
            default_fx = default_action_scene_fx(body.type)
            if mood_fx and (
                body.type == "pet"
                or not default_fx
                or mood_fx.get("mode") != default_fx.get("mode")
            ):
                modifiers.append(mood_fx)
    event.meta = {**event.meta, "ignored": ignored}
    default_fx = default_action_scene_fx(body.type)
    if ignored:
        legacy_fx = (
            {"mode": "flinch_away", "duration_ms": 1800}
            if body.type == "pet"
            else default_fx
        )
    else:
        legacy_fx = modifiers[-1] if modifiers else default_fx
    if legacy_fx and legacy_fx.get("mode") in {
        "threshold_refusal",
        "lean_in",
        "side_eye",
    }:
        legacy_fx = default_fx
    scene_event_id = f"action:{event.id}"
    scene_event = build_scene_event(
        event_id=scene_event_id,
        action=body.type,
        actor_user_id=user.id,
        animation_state=mood.animation_state,
        modifiers=modifiers,
        origin_id=body.origin_id,
        variant=body.variant,
    )
    # 3) first-time milestone facts
    if body.type == "walk":
        await core_memory.note_first(
            session, pet.id, "first_walk_day", local_now().strftime("%Y-%m-%d")
        )
    # 4) maybe promote to moment (and surface auto-milestones to the room)
    promoted_moment, milestone_info = await moments.maybe_promote(session, pet.id, event)
    if quirk_effect is not None:
        response = RuntimeResponse(
            text=quirk_effect.text,
            is_utterance=quirk_effect.is_utterance,
        )
    else:
        recent = await buffer.recent(session, pet.id, limit=5)
        recent_moments = await moments.random_recent(session, pet.id, n=2)
        response = None
    await session.commit()
    try:
        if legacy_fx:
            set_scene_fx(
                pet.id,
                **legacy_fx,
                event_id=scene_event_id,
            )
        record_scene_event(pet.id, scene_event)
        await channel.broadcast(
            pet.id,
            {"type": "scene_event", "event": scene_event},
            exclude_user_id=None if body.origin_id else user.id,
        )
        await broadcast_scene_payloads(session, pet)
        if response is None:
            response = await respond(
                pet,
                body.type,
                body.text,
                recent,
                recent_moments,
                core_facts=facts,
                spot=body.spot,
                event_id=event.id,
                ignored=ignored,
            )
        await session.refresh(pet)
        payload = await build_scene_payload(
            session,
            pet,
            current_user_id=user.id,
        )
        response_scene_event = next(
            (
                event
                for event in payload.get("scene_events", [])
                if event.get("id") == scene_event_id
            ),
            None,
        )
        await channel.broadcast(pet.id, {
            "type": "response",
            "text": response.text,
            "is_utterance": response.is_utterance,
            "by_user_id": user.id,
            "action": body.type,
            "scene_event_id": scene_event_id,
        })
        # Surface auto-milestones (10x pet, first walk, etc.) as their own WS
        # frame so the client can render them with celebration treatment instead
        # of leaving them only in the memory drawer.
        if promoted_moment is not None and milestone_info is not None:
            await channel.broadcast(pet.id, {
                "type": "milestone",
                "kind": milestone_info.kind,  # "first_seen" | "count"
                "event_type": milestone_info.event_type,
                "count": milestone_info.count,
                "fragment": promoted_moment.fragment,
                "moment_id": promoted_moment.id,
                "by_user_id": user.id,
                "by_display_name": user.display_name,
            })
        return {
            "ok": True,
            "pet": payload,
            "response": {
                "text": response.text,
                "is_utterance": response.is_utterance,
                "by_user_id": user.id,
                "action": body.type,
                "scene_event_id": scene_event_id,
            },
            "scene_event": response_scene_event,
        }
    except Exception:
        log.exception(
            "action %s committed but post-commit delivery failed",
            scene_event_id,
        )
        return {
            "ok": True,
            "pet": None,
            "response": None,
            "scene_event": scene_event,
        }
