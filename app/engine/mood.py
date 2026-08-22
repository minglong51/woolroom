"""
Mood engine. Pure functions over (MoodState, now) -> MoodState.

Two invisible dimensions:
  - arousal: 0 (sleepy) .. 100 (alert)
  - valence: 0 (grumpy) .. 100 (content)

Rules:
  - Arousal tracks a daily diurnal curve + recent interaction bumps.
  - Valence drifts slowly from care consistency over ~7 days.
  - Neither dimension is ever shown as a number. Only animation state + body language.
  - Mood transitions are slow. Minutes, not turns.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo


@dataclass
class MoodState:
    arousal: int  # 0..100
    valence: int  # 0..100
    animation_state: str  # sleeping | sitting | alert | playful
    last_drift_at: datetime


def clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def diurnal_target(now: datetime, tz: tzinfo | None = None) -> int:
    """Daily sleepy/awake curve. Low at night, high midday, gentle dip midafternoon.

    ``now`` is naive UTC (storage convention); pass ``tz`` so the curve runs on
    the pet's local clock — without it the pet sleeps on UTC hours.
    """
    if tz is not None:
        now = now.replace(tzinfo=UTC).astimezone(tz)
    hour = now.hour + now.minute / 60.0
    # peak around 11am-ish, low around 3am-ish
    base = 50 + 35 * math.sin((hour - 7.0) * math.pi / 12.0)
    # a small afternoon dip
    if 14 <= hour <= 16:
        base -= 10
    return clamp(base)


def drift(
    state: MoodState,
    now: datetime,
    care_rate_7d: float = 0.5,  # 0..1, how consistently you've cared recently
    tz: tzinfo | None = None,  # home timezone for the diurnal curve; now stays UTC
) -> MoodState:
    """Advance mood toward its natural targets. Slow — proportional to elapsed minutes."""
    elapsed_min = max(0.0, (now - state.last_drift_at).total_seconds() / 60.0)
    if elapsed_min <= 0:
        return state

    # Arousal pulled toward diurnal target, slowly.
    target_arousal = diurnal_target(now, tz)
    # Rate: ~1 point per 3 minutes of elapsed time, capped.
    pull = min(1.0, elapsed_min / 180.0)  # over 3 hours, fully pulls to target
    new_arousal = state.arousal + (target_arousal - state.arousal) * pull

    # Valence pulled toward a target derived from care consistency.
    # Consistent care (care_rate_7d near 1) -> content baseline ~70
    # Neglect (near 0) -> drifts toward aloof ~40 (NOT sad — shiba)
    target_valence = 40 + 30 * care_rate_7d
    valence_pull = min(1.0, elapsed_min / (60 * 24))  # 24h to fully adjust
    new_valence = state.valence + (target_valence - state.valence) * valence_pull

    # Gentle random noise so it doesn't feel mechanical.
    new_arousal += random.uniform(-2, 2)
    new_valence += random.uniform(-1, 1)

    new_state = MoodState(
        arousal=clamp(new_arousal),
        valence=clamp(new_valence),
        animation_state=pick_animation(clamp(new_arousal), clamp(new_valence)),
        last_drift_at=now,
    )
    return new_state


def pick_animation(arousal: int, valence: int) -> str:
    if arousal < 30:
        return "sleeping"
    if arousal < 55:
        return "sitting"
    if valence >= 60:
        return "playful"
    return "alert"


# Action → (arousal_delta, valence_delta).  Keep nudges small.
ACTION_NUDGE: dict[str, tuple[int, int]] = {
    "greet": (+6, +3),
    "feed": (+8, +6),
    "pet": (+2, +5),
    "walk": (+15, +4),
    "call": (+10, +1),
    "message": (+3, +1),
    # play = high-arousal positive burst, triggers the zoomie scene-fx so the
    # dog visibly tears around the room for ~5s. The "lively run" button.
    "play": (+22, +7),
}


def nudge(state: MoodState, arousal_delta: int = 0, valence_delta: int = 0) -> MoodState:
    """Event-driven adjustment. Care actions give small bumps; mood is mostly autonomous."""
    return MoodState(
        arousal=clamp(state.arousal + arousal_delta),
        valence=clamp(state.valence + valence_delta),
        animation_state=pick_animation(
            clamp(state.arousal + arousal_delta),
            clamp(state.valence + valence_delta),
        ),
        last_drift_at=state.last_drift_at,
    )


def should_ignore_action(arousal: int, ignore_rate: float) -> bool:
    """Sleeping dog ignores most things. Alert dog engages."""
    if arousal < 25:
        return random.random() < 0.85  # deep sleep, ignores almost everything
    if arousal < 45:
        return random.random() < ignore_rate + 0.2
    return random.random() < ignore_rate
