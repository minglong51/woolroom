"""Top-level respond() — the only thing routes call.

Pipeline:
  1. If the pet should ignore (sleeping / high ignore_rate), return fallback body-language.
  2. Try LLM.
  3. Validate output. If rejected, fall back to phrasebook.
  4. Enforce utterance rate limit (≤1 utterance per pet per 5 min).
     Body-language (*...*) is not rate-limited.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import settings
from app.data.body_language import contextual_message_phrase, fallback_phrase
from app.data.species import temperament_for
from app.engine.mood import should_ignore_action
from app.runtime import client, llm_log, prompt, validator
from app.storage.models import BufferEvent, Moment, Pet


UTTERANCE_COOLDOWN_S = 5 * 60

_last_utterance_at: dict[str, float] = {}


@dataclass
class Response:
    text: str  # what to show
    is_utterance: bool  # False = body language (*...*), True = spoken words


@dataclass
class ResponseIntent:
    mode: str
    guidance: str
    callback_moment: str | None = None


def _is_body_language(text: str) -> bool:
    return text.startswith("*") and text.endswith("*")


def _rate_limited(pet_id: str) -> bool:
    last = _last_utterance_at.get(pet_id, 0.0)
    return (time.time() - last) < UTTERANCE_COOLDOWN_S


def _mark_utterance(pet_id: str) -> None:
    _last_utterance_at[pet_id] = time.time()


def decide_ignore(pet: Pet) -> bool:
    ignore_rate = (pet.temperament or temperament_for(None)).get("ignore_rate", 0.33)
    return should_ignore_action(pet.mood_arousal, ignore_rate)


def _deterministic_response(
    pet: Pet,
    action: str,
    user_text: str | None,
    event_id: int | None,
    spot: str | None,
    *,
    allow_utterance: bool,
) -> Response:
    species = getattr(pet, "species", "cat") or "cat"
    if action == "message":
        text = contextual_message_phrase(
            pet.mood_arousal,
            pet.mood_valence,
            user_text,
            event_id,
            allow_utterance=allow_utterance,
            species=species,
        )
    else:
        text = fallback_phrase(
            pet.mood_arousal,
            pet.mood_valence,
            action=action,
            spot=spot,
            event_id=event_id,
            species=species,
        )
    is_utterance = not _is_body_language(text)
    if is_utterance:
        _mark_utterance(pet.id)
    return Response(text=text, is_utterance=is_utterance)


def _pick_callback_moment(recent_moments: list[Moment], action: str, valence: int) -> str | None:
    if valence < 55 or action not in {"message", "call", "walk", "pet"}:
        return None
    if not recent_moments:
        return None
    return recent_moments[0].fragment


def _choose_intent(
    pet: Pet,
    action: str,
    recent_moments: list[Moment],
) -> ResponseIntent:
    callback_moment = _pick_callback_moment(recent_moments, action, pet.mood_valence)
    if action in {"greet", "pet", "feed", "walk"}:
        return ResponseIntent(
            mode="body_language",
            guidance="Prefer body language only. Do not speak unless there is an unusually strong reason.",
            callback_moment=callback_moment,
        )
    if action == "message":
        return ResponseIntent(
            mode="callback" if callback_moment else "utterance",
            guidance=(
                "If you speak, keep it brief and slightly oblique. Do not answer the human's message directly."
                if not callback_moment
                else "If you speak, make it a small sideways callback to the memory. Do not narrate or explain it."
            ),
            callback_moment=callback_moment,
        )
    if action == "call":
        return ResponseIntent(
            mode="utterance" if pet.mood_arousal >= 45 else "body_language",
            guidance="A call can earn a short acknowledgment, but body language is still more natural.",
            callback_moment=callback_moment,
        )
    return ResponseIntent(
        mode="body_language",
        guidance="Stay physical, quiet, and sparse.",
        callback_moment=callback_moment,
    )


async def respond(
    pet: Pet,
    action: str,
    user_text: str | None,
    recent_events: list[BufferEvent],
    recent_moments: list[Moment],
    core_facts: dict[str, str] | None = None,
    spot: str | None = None,
    event_id: int | None = None,
    ignored: bool | None = None,
) -> Response:
    if ignored is None:
        ignored = decide_ignore(pet)
    if ignored:
        return _deterministic_response(
            pet,
            action,
            None,
            event_id,
            spot,
            allow_utterance=False,
        )

    intent = _choose_intent(pet, action, recent_moments)
    if intent.mode == "body_language":
        return Response(
            text=fallback_phrase(
                pet.mood_arousal,
                pet.mood_valence,
                action=action,
                spot=spot,
                event_id=event_id,
                species=getattr(pet, "species", "cat") or "cat",
            ),
            is_utterance=False,
        )

    # The provider switch is the only LLM gate: "disabled" (or an anthropic
    # lane with no metered key) means the deterministic phrasebook answers.
    # The daily cap is the budget circuit-breaker — at/over it, same fallback.
    provider = settings.llm_provider.strip().casefold()
    llm_ready = provider != "disabled" and not (
        provider == "anthropic" and not settings.anthropic_api_key.strip()
    )
    if not llm_ready or await llm_log.calls_today(pet.id) >= settings.llm_daily_call_cap:
        return _deterministic_response(
            pet,
            action,
            user_text,
            event_id,
            spot,
            allow_utterance=not _rate_limited(pet.id),
        )

    if _rate_limited(pet.id):
        return _deterministic_response(
            pet,
            action,
            user_text,
            event_id,
            spot,
            allow_utterance=False,
        )

    ctx = prompt.Context(
        pet=pet,
        mood_arousal=pet.mood_arousal,
        mood_valence=pet.mood_valence,
        animation_state=pet.animation_state,
        recent_events=recent_events,
        recent_moments=recent_moments,
        user_action=action,
        user_text=user_text,
        response_mode=intent.mode,
        response_guidance=intent.guidance,
        callback_moment=intent.callback_moment,
    )
    system = prompt.build_system_prompt(pet, facts=core_facts)
    user_msg = prompt.build_user_message(ctx)

    raw = await client.complete(system, user_msg, pet_id=pet.id)
    if raw:
        text = validator.clean(raw.splitlines()[0] if raw else "")
        verdict = "accepted" if validator.validate(text) else "rejected"
        await llm_log.update_verdict(llm_log.hash_prompt(system, user_msg), verdict)
        if verdict == "accepted":
            is_utter = not _is_body_language(text)
            if is_utter:
                _mark_utterance(pet.id)
            return Response(text=text, is_utterance=is_utter)

    return _deterministic_response(
        pet,
        action,
        user_text,
        event_id,
        spot,
        allow_utterance=False,
    )
