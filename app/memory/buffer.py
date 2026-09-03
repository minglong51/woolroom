"""Short-term memory: rolling window of recent events. No semantic user content stored."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storage.models import ActionReceipt, BufferEvent


async def add_event(
    session: AsyncSession,
    pet_id: str,
    event_type: str,
    user_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> BufferEvent:
    event = BufferEvent(
        pet_id=pet_id,
        user_id=user_id,
        event_type=event_type,
        meta=meta or {},
    )
    session.add(event)
    await session.flush()
    await _prune(session, pet_id)
    return event


async def get_action_receipt(
    session: AsyncSession,
    pet_id: str,
    user_id: str,
    origin_id: str,
) -> ActionReceipt | None:
    return await session.get(
        ActionReceipt,
        {
            "pet_id": pet_id,
            "user_id": user_id,
            "origin_id": origin_id,
        },
    )


async def add_action_receipt(
    session: AsyncSession,
    *,
    pet_id: str,
    user_id: str,
    origin_id: str,
    event_id: int,
    request_fingerprint: str,
) -> ActionReceipt:
    receipt = ActionReceipt(
        pet_id=pet_id,
        user_id=user_id,
        origin_id=origin_id,
        event_id=event_id,
        request_fingerprint=request_fingerprint,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def recent(session: AsyncSession, pet_id: str, limit: int = 5) -> list[BufferEvent]:
    q = (
        select(BufferEvent)
        .where(BufferEvent.pet_id == pet_id)
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(limit)
    )
    result = await session.execute(q)
    return list(result.scalars().all())


async def latest_event_of_types(
    session: AsyncSession,
    pet_id: str,
    event_types: Collection[str],
) -> BufferEvent | None:
    q = (
        select(BufferEvent)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.event_type.in_(event_types),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def _prune(session: AsyncSession, pet_id: str) -> None:
    """Keep only the most recent N events per pet."""
    cap = settings.buffer_max_events
    q = (
        select(BufferEvent.id)
        .where(BufferEvent.pet_id == pet_id)
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .offset(cap)
    )
    result = await session.execute(q)
    stale_ids = [row[0] for row in result.all()]
    if stale_ids:
        await session.execute(delete(BufferEvent).where(BufferEvent.id.in_(stale_ids)))
