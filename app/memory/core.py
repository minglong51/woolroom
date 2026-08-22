"""Long-term memory: fixed facts. Names, adoption date, quirks, firsts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# origin_line lives in the voice pack (app/data/voice.py); re-exported here so
# existing `core_memory.origin_line` call sites (pet_state) keep working.
from app.data.voice import origin_line as origin_line
from app.storage.models import CoreFact


async def set_fact(session: AsyncSession, pet_id: str, key: str, value: str) -> None:
    q = select(CoreFact).where(CoreFact.pet_id == pet_id, CoreFact.key == key)
    result = await session.execute(q)
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        session.add(CoreFact(pet_id=pet_id, key=key, value=value))
    await session.flush()


async def get_fact(session: AsyncSession, pet_id: str, key: str) -> str | None:
    q = select(CoreFact).where(CoreFact.pet_id == pet_id, CoreFact.key == key)
    result = await session.execute(q)
    fact = result.scalar_one_or_none()
    return fact.value if fact else None


async def all_facts(session: AsyncSession, pet_id: str) -> dict[str, str]:
    q = select(CoreFact).where(CoreFact.pet_id == pet_id)
    result = await session.execute(q)
    return {fact.key: fact.value for fact in result.scalars()}


async def note_first(session: AsyncSession, pet_id: str, key: str, value: str) -> bool:
    """Write a core fact only if it is not already set. Returns True if written.
    Used for 'first-time' milestones like first_walk_day — subsequent calls are no-ops."""
    if await get_fact(session, pet_id, key) is not None:
        return False
    await set_fact(session, pet_id, key, value)
    return True
