"""Pet aging — compressed timeline so the pet visibly grows over weeks/months
instead of staying frozen at adoption-day proportions.

Convention: 1 real month (30 days) = 1 pet year.
A cat's ~15-year lifespan compresses to ~15 months of room-time to a senior.
"""

from __future__ import annotations

from datetime import datetime

from app.time import utc_now


REAL_DAYS_PER_PET_YEAR = 30


def pet_age_years(adopted_at: datetime | None, now: datetime | None = None) -> float:
    if adopted_at is None:
        return 0.0
    elapsed = (now or utc_now()) - adopted_at
    return max(0.0, elapsed.total_seconds() / 86400.0 / REAL_DAYS_PER_PET_YEAR)


def life_stage(adopted_at: datetime | None, now: datetime | None = None) -> str:
    years = pet_age_years(adopted_at, now)
    if years < 3:
        return "kitten"
    if years < 7:
        return "young"
    if years < 12:
        return "adult"
    return "senior"


_RENDER_SCALE: dict[str, float] = {
    "kitten": 0.7,
    "young": 0.85,
    "adult": 1.0,
    "senior": 0.95,
}


def render_scale(stage: str) -> float:
    return _RENDER_SCALE.get(stage, 1.0)


# Per-stage proportional multipliers applied on top of the global render_scale.
# Multipliers compound with poseProfile (per-pose tuning) and the body-anchor
# canvas transform. Offsets are pixel-space additions to poseProfile fields.
#
# Design intent:
#   kitten — relatively oversized head, short body, smaller tail, head perked up
#   young  — residual kitten bias, leaner body
#   adult  — baseline (1.0 everywhere)
#   senior — head slightly smaller, body slightly wider/squatter, head + ears
#            carried lower (droop)
_STAGE_PROPORTIONS: dict[str, dict[str, float]] = {
    "kitten": {
        "headScale": 1.18,
        "bodyScaleX": 0.92,
        "bodyScaleY": 0.95,
        "chestScale": 0.93,
        "haunchScale": 0.95,
        "tailScale": 0.85,
        "headOffsetY": -3,
        "earOffsetY": -2,
        # Head sits closer to cx so it tracks the smaller body's front edge
        # instead of jutting past it.
        "headOffsetXScale": 0.88,
        # Bouncier paw lift, faster breath.
        "pawLiftScale": 1.45,
        "breathPeriodScale": 0.78,
    },
    "young": {
        "headScale": 1.05,
        "bodyScaleX": 0.97,
        "bodyScaleY": 0.99,
        "chestScale": 0.98,
        "haunchScale": 0.98,
        "tailScale": 0.96,
        "headOffsetY": -1,
        "earOffsetY": -1,
        "headOffsetXScale": 0.96,
        "pawLiftScale": 1.15,
        "breathPeriodScale": 0.92,
    },
    "adult": {
        "headScale": 1.0,
        "bodyScaleX": 1.0,
        "bodyScaleY": 1.0,
        "chestScale": 1.0,
        "haunchScale": 1.0,
        "tailScale": 1.0,
        "headOffsetY": 0,
        "earOffsetY": 0,
        "headOffsetXScale": 1.0,
        "pawLiftScale": 1.0,
        "breathPeriodScale": 1.0,
    },
    "senior": {
        "headScale": 0.97,
        "bodyScaleX": 1.03,
        "bodyScaleY": 0.97,
        "chestScale": 1.02,
        "haunchScale": 1.02,
        "tailScale": 0.95,
        "headOffsetY": 4,
        "earOffsetY": 3,
        # Head carried slightly forward — senior pets lean into their gait.
        "headOffsetXScale": 1.02,
        # Slower spring, slower breath.
        "pawLiftScale": 0.7,
        "breathPeriodScale": 1.28,
    },
}


def stage_proportions(stage: str) -> dict[str, float]:
    return dict(_STAGE_PROPORTIONS.get(stage, _STAGE_PROPORTIONS["adult"]))
