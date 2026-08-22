"""FastAPI dependencies. Session-cookie user + pet lookup."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import COOKIE_NAME, load_user
from app.storage import repo
from app.storage.db import SessionLocal
from app.storage.models import Pet, User


async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_user_optional(
    paws_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    session: AsyncSession = Depends(db),
) -> User | None:
    return await load_user(session, paws_session)


async def current_user(
    user: User | None = Depends(current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no session")
    return user


async def current_pet(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> Pet:
    """The room the caller is acting in. Rooms are addressed explicitly via
    the ?pet=<id> query param (every room-scoped route, GET or POST); without
    it, fall back to the human's active room (last-left, else founding).

    An unconfirmed co-adoption participant gets a 403 everywhere except the
    ceremony endpoints — the second cat's room opens for them once they've
    picked its second quirk."""
    requested = request.query_params.get("pet")
    if requested:
        participant = await repo.get_participant(session, requested, user.id)
        if participant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not your room",
            )
        if participant.confirmed_adoption_at is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="meet him first — pick his second habit",
            )
        pet = await repo.get_pet(session, requested)
        if pet is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such room")
        return pet
    pet = await repo.resolve_active_pet(session, user)
    if pet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no pet yet")
    return pet
