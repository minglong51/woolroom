"""Medium-term memory: 1-2 shared moments per week, promoted from buffer."""

from __future__ import annotations

import random
from dataclasses import dataclass
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.voice import (
    count_milestone_fragment as _count_milestone_fragment,
    template_fragment as _template_fragment,
)
from app.storage.models import BufferEvent, Moment
from app.time import utc_now


# Event types that always get promoted the first time they happen.
FIRST_SEEN_EVENT_TYPES: set[str] = {
    "walk",
    "greet",
    "feed",
    "adoption",
    "pet",
    "call",
    "message",
    "play",
    "visit",
}

# Per-event-type interaction counts that auto-promote to milestone moments.
# Skipping single-digit thresholds keeps onboarding from feeling congratulatory
# every other tap; first-seen handles the "we just did this for the first time"
# beat already.
COUNT_MILESTONES: list[int] = [10, 25, 50, 100, 250, 500]

# Action types that count toward count-milestones.
COUNTABLE_EVENT_TYPES: set[str] = {
    "pet",
    "feed",
    "walk",
    "greet",
    "call",
    "message",
    "play",
}


@dataclass
class MilestoneInfo:
    kind: str  # "first_seen" | "count"
    event_type: str
    count: int | None = None  # set for "count"


async def maybe_promote(
    session: AsyncSession,
    pet_id: str,
    event: BufferEvent,
    fragment_generator=None,
) -> tuple[Moment | None, MilestoneInfo | None]:
    """Decide whether this buffer event deserves to become a Moment.

    Returns (moment, milestone_info). milestone_info is set when the moment is
    a first-of-its-kind or a count threshold (10x, 50x, etc.) — caller can
    broadcast a celebration to the room instead of leaving it in the drawer.
    """
    # First notable actions always become a Moment once.
    if await _is_first_seen_event_type(session, pet_id, event.event_type):
        fragment = await _generate_fragment(event, fragment_generator)
        moment = await _create_moment(session, pet_id, event, fragment)
        return moment, MilestoneInfo(kind="first_seen", event_type=event.event_type)

    # Count-based milestone (10th, 25th, 50th, ... time this action happened).
    crossed = await _check_count_milestone(session, pet_id, event.event_type)
    if crossed is not None:
        fragment = _count_milestone_fragment(event.event_type, crossed)
        moment = await _create_moment(session, pet_id, event, fragment)
        return moment, MilestoneInfo(kind="count", event_type=event.event_type, count=crossed)

    # Otherwise, roughly 1 random promotion per week.
    # Probability per event ~= 1 / (events_per_week). Tune by buffer load.
    if random.random() < 0.02:  # ~1 in 50 events
        fragment = await _generate_fragment(event, fragment_generator)
        moment = await _create_moment(session, pet_id, event, fragment)
        return moment, None

    return None, None


async def _check_count_milestone(
    session: AsyncSession,
    pet_id: str,
    event_type: str,
) -> int | None:
    """If the *current* event made the per-pet count for this event_type
    hit one of COUNT_MILESTONES, return that count. Else None.

    BufferEvent for the current action has already been flushed before
    maybe_promote runs, so the count is inclusive of it.
    """
    if event_type not in COUNTABLE_EVENT_TYPES:
        return None
    q = select(func.count(BufferEvent.id)).where(
        BufferEvent.pet_id == pet_id,
        BufferEvent.event_type == event_type,
    )
    count = (await session.execute(q)).scalar_one() or 0
    return count if count in COUNT_MILESTONES else None


async def pin_event_as_moment(
    session: AsyncSession,
    pet_id: str,
    event_id: int,
    fragment_generator=None,
) -> Moment | None:
    """Manual promotion: user has decided this specific event is worth keeping.
    Idempotent if the event already has a Moment with it in source_event_ids."""
    event = await session.get(BufferEvent, event_id)
    if event is None or event.pet_id != pet_id:
        return None
    # Find a moment already containing this event id, if any.
    q = (
        select(Moment)
        .where(Moment.pet_id == pet_id)
        .order_by(Moment.created_at.desc())
        .limit(50)
    )
    for m in (await session.execute(q)).scalars().all():
        if event_id in (m.source_event_ids or []):
            return m
    fragment = await _generate_fragment(event, fragment_generator)
    return await _create_moment(session, pet_id, event, fragment)


async def _is_first_seen_event_type(
    session: AsyncSession,
    pet_id: str,
    event_type: str,
) -> bool:
    if event_type not in FIRST_SEEN_EVENT_TYPES:
        return False
    q = (
        select(Moment.id)
        .where(Moment.pet_id == pet_id, Moment.event_type == event_type)
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none() is None


async def random_recent(
    session: AsyncSession,
    pet_id: str,
    n: int = 2,
    bias_recent_days: int = 30,
) -> list[Moment]:
    """Sample N moments, weighted toward recent."""
    q = (
        select(Moment)
        .where(Moment.pet_id == pet_id)
        .order_by(Moment.created_at.desc())
        .limit(20)
    )
    result = await session.execute(q)
    pool = list(result.scalars().all())
    if not pool:
        return []
    # Weight: decay by days since created
    now = utc_now()

    def weight(m: Moment) -> float:
        age_days = max(0.0, (now - m.created_at).total_seconds() / 86400.0)
        return 1.0 / (1.0 + age_days / bias_recent_days)

    weights = [weight(m) for m in pool]
    return random.choices(pool, weights=weights, k=min(n, len(pool)))


async def _create_moment(
    session: AsyncSession,
    pet_id: str,
    event: BufferEvent,
    fragment: str,
) -> Moment:
    moment = Moment(
        pet_id=pet_id,
        fragment=fragment,
        event_type=event.event_type,
        source_event_ids=[event.id],
    )
    session.add(moment)
    await session.flush()
    return moment


async def _generate_fragment(event: BufferEvent, fragment_generator) -> str:
    """Fragment is a 1-line affective summary. Generator is optional (falls back to template)."""
    if fragment_generator is not None:
        try:
            return await fragment_generator(event)
        except Exception:
            pass
    return _template_fragment(event)

