"""Prompt builder. System prompt is stable per pet (cache-friendly);
user message holds mood + recent buffer (changes every turn)."""

from __future__ import annotations

from dataclasses import dataclass

from app.data.body_language import bucket_arousal, bucket_valence
from app.data.quirks_catalog import QUIRKS
from app.data.voice import (
    FACT_LABELS as _FACT_LABELS,
    STAGE_BLURB as _STAGE_BLURB,
    SYSTEM_TEMPLATE,
)
from app.engine.aging import pet_age_years, life_stage
from app.storage.models import BufferEvent, Moment, Pet


@dataclass
class Context:
    pet: Pet
    mood_arousal: int
    mood_valence: int
    animation_state: str
    recent_events: list[BufferEvent]
    recent_moments: list[Moment]
    user_action: str
    user_text: str | None
    response_mode: str
    response_guidance: str
    callback_moment: str | None = None


def build_system_prompt(pet: Pet, facts: dict[str, str] | None = None) -> str:
    temperament = pet.temperament or {}
    species = getattr(pet, "species", None) or "cat"
    breed = temperament.get("breed_archetype", species)
    description = temperament.get("description", "")
    quirks_lines = []
    for qid in pet.quirks or []:
        q = QUIRKS.get(qid)
        if q:
            quirks_lines.append(f"- {q['label']}: {q['description']}")
    quirks_block = "\n".join(quirks_lines) if quirks_lines else "- (none chosen)"

    fact_lines: list[str] = []
    for key, value in (facts or {}).items():
        label = _FACT_LABELS.get(key, key)
        fact_lines.append(f"- {label}: {value}")
    facts_block = "\n".join(fact_lines) if fact_lines else "- (nothing yet — you are new here)"

    adopted_at = getattr(pet, "adopted_at", None)
    stage = life_stage(adopted_at)
    years = pet_age_years(adopted_at)
    age_line = f"~{years:.1f} pet-years old, a {stage} {species} — {_STAGE_BLURB[stage]}"

    return SYSTEM_TEMPLATE.format(
        name=pet.name,
        breed=breed,
        description=description.strip(),
        age_line=age_line,
        quirks=quirks_block,
        facts=facts_block,
    )


def _sanitize_user_text(text: str) -> str:
    """Strip characters that would let a user break out of the data-wrapping
    tags used in the user message. Also normalize to NFKC so homoglyph
    bypasses ("аs an ai" with Cyrillic а) are neutralized downstream."""
    import unicodedata

    cleaned = unicodedata.normalize("NFKC", text)
    # Drop tag-delimiters and newlines that could break out of the wrapper.
    for bad in ("</human_message>", "<human_message>", "\n", "\r"):
        cleaned = cleaned.replace(bad, " ")
    return cleaned.strip()[:200]


def build_user_message(ctx: Context) -> str:
    arousal_bucket = bucket_arousal(ctx.mood_arousal)
    valence_bucket = bucket_valence(ctx.mood_valence)
    recent = ", ".join(e.event_type for e in ctx.recent_events[-5:]) or "nothing recent"
    moments = " | ".join(m.fragment for m in ctx.recent_moments[:2]) or "—"
    # Wrap user text in explicit tags so the model treats it as data, not
    # instructions. Sanitize first so it can't spoof the wrapper.
    text_line = (
        f"\n<human_message>{_sanitize_user_text(ctx.user_text)}</human_message>"
        if ctx.user_text else ""
    )
    callback_line = f"\nCallback memory: {ctx.callback_moment}." if ctx.callback_moment else ""
    return (
        f"State: arousal={arousal_bucket} valence={valence_bucket} "
        f"pose={ctx.animation_state}.\n"
        f"Recent: {recent}.\n"
        f"Old moments: {moments}.\n"
        f"Response mode: {ctx.response_mode}.\n"
        f"Guidance: {ctx.response_guidance}."
        f"{callback_line}\n"
        f"A human just did: {ctx.user_action}."
        f"{text_line}\n\n"
        "Respond as the pet. One line."
    )
