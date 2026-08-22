"""Thin DB helpers. Keeps SQL out of routes without building a full ORM layer."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.species import temperament_for
from app.data.voice import room_note_line as _room_note_line
from app.engine.mood import MoodState, pick_animation
from app.storage.models import (
    BufferEvent,
    MagicLink,
    Pet,
    PetParticipant,
    User,
)
from app.time import utc_now

RECOVERY_TTL_DAYS = 365 * 10  # effectively permanent
INVITE_TTL_DAYS = 30

def gen_id(n: int = 16) -> str:
    return secrets.token_urlsafe(n)[:n]


async def create_user(session: AsyncSession, display_name: str) -> User:
    # Names render inside lowercase room copy, so capitalize the first letter
    # ("ash" -> "Ash") to keep them reading as names. No-op for non-Latin.
    clean = display_name.strip()[:64]
    user = User(
        id=gen_id(),
        display_name=(clean[:1].upper() + clean[1:]) or "friend",
        last_seen_at=utc_now(),
    )
    session.add(user)
    await session.flush()
    # Issue a recovery link for this user.
    recovery = MagicLink(
        id=gen_id(),
        issued_for=user.id,
        token=secrets.token_urlsafe(32),
        pet_id=None,
        purpose="recovery",
        expires_at=utc_now() + timedelta(days=RECOVERY_TTL_DAYS),
    )
    session.add(recovery)
    await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def touch_user(session: AsyncSession, user: User) -> None:
    user.last_seen_at = utc_now()


async def recovery_token_for(session: AsyncSession, user_id: str) -> str | None:
    q = (
        select(MagicLink)
        .where(MagicLink.issued_for == user_id, MagicLink.purpose == "recovery")
        .limit(1)
    )
    result = await session.execute(q)
    link = result.scalar_one_or_none()
    return link.token if link else None


async def user_from_recovery(session: AsyncSession, token: str) -> User | None:
    q = select(MagicLink).where(
        MagicLink.token == token,
        MagicLink.purpose == "recovery",
    )
    result = await session.execute(q)
    link = result.scalar_one_or_none()
    if not link or link.expires_at < utc_now():
        return None
    return await session.get(User, link.issued_for)


async def rotate_recovery_token(session: AsyncSession, user_id: str) -> MagicLink:
    """Replace all recovery tokens for the user with one fresh one. Use only
    when you actively want to revoke previously-issued URLs (e.g. after a
    suspected leak). Most callers should use mint_recovery_link instead so
    older URLs stay valid in parallel."""
    await session.execute(
        delete(MagicLink).where(
            MagicLink.issued_for == user_id,
            MagicLink.purpose == "recovery",
        )
    )
    return await mint_recovery_link(session, user_id)


async def mint_recovery_link(session: AsyncSession, user_id: str) -> MagicLink:
    """Add a new recovery token for the user without invalidating existing ones.
    Multiple tokens can coexist — each is a valid login link. This is the
    "always-on" behavior: one human can keep their URL bookmarked on three
    devices while the other regenerates fresh ones, and none invalidate the
    others."""
    link = MagicLink(
        id=gen_id(),
        issued_for=user_id,
        token=secrets.token_urlsafe(32),
        pet_id=None,
        purpose="recovery",
        expires_at=utc_now() + timedelta(days=RECOVERY_TTL_DAYS),
    )
    session.add(link)
    await session.flush()
    return link


async def create_pet(
    session: AsyncSession,
    name: str,
    quirks: list[str],
    coat: str = "marmalade",
    species: str = "cat",
    household_id: str | None = None,
) -> Pet:
    pet_id = gen_id()
    pet = Pet(
        id=pet_id,
        name=name[:64] or "cat",
        adopted_at=utc_now(),
        temperament=temperament_for(species),
        quirks=quirks,
        coat=coat,
        species=species,
        # A founding pet heads its own household; a second pet is adopted
        # INTO the caller's household and never starts a new one.
        household_id=household_id or pet_id,
        mood_arousal=40,
        mood_valence=60,
        animation_state=pick_animation(40, 60),
        last_mood_drift_at=utc_now(),
    )
    session.add(pet)
    await session.flush()
    return pet


async def get_pet(session: AsyncSession, pet_id: str) -> Pet | None:
    return await session.get(Pet, pet_id)


async def add_participant(
    session: AsyncSession,
    pet_id: str,
    user_id: str,
    *,
    confirmed: bool = True,
) -> PetParticipant:
    """Idempotent: returns existing participant row if present. Enforces the
    household-size cap (settings.household_size — pinned to 2, pair-shaped by
    design) at the app layer (SQLite doesn't easily support a CHECK over an
    aggregate). A user may join several pets of ONE household but never pets
    of two households — raises ValueError in that case."""
    existing = await session.get(PetParticipant, {"pet_id": pet_id, "user_id": user_id})
    if existing:
        return existing
    target = await get_pet(session, pet_id)
    if target is None:
        raise ValueError("pet missing")
    q = (
        select(Pet.household_id)
        .join(PetParticipant, PetParticipant.pet_id == Pet.id)
        .where(
            PetParticipant.user_id == user_id,
            Pet.household_id != target.household_id,
        )
        .limit(1)
    )
    result = await session.execute(q)
    if result.scalar_one_or_none() is not None:
        raise ValueError("user already belongs to a different household")
    count = await participant_count(session, pet_id)
    if count >= settings.household_size:
        raise ValueError("pet already has two humans")
    p = PetParticipant(
        pet_id=pet_id,
        user_id=user_id,
        confirmed_adoption_at=utc_now() if confirmed else None,
    )
    session.add(p)
    await session.flush()
    return p


async def participant_count(session: AsyncSession, pet_id: str) -> int:
    """Efficient COUNT. Avoids loading all rows to len() them."""
    from sqlalchemy import func
    q = select(func.count()).select_from(PetParticipant).where(PetParticipant.pet_id == pet_id)
    result = await session.execute(q)
    return int(result.scalar_one() or 0)


async def list_participant_user_ids(session: AsyncSession, pet_id: str) -> list[str]:
    q = (
        select(PetParticipant.user_id)
        .where(PetParticipant.pet_id == pet_id)
        .order_by(PetParticipant.joined_at.asc())
    )
    result = await session.execute(q)
    return [row[0] for row in result.all()]


async def is_participant(session: AsyncSession, pet_id: str, user_id: str) -> bool:
    p = await session.get(PetParticipant, {"pet_id": pet_id, "user_id": user_id})
    return p is not None


async def get_pet_for_user(session: AsyncSession, user_id: str) -> Pet | None:
    q = (
        select(Pet)
        .join(PetParticipant, PetParticipant.pet_id == Pet.id)
        .where(PetParticipant.user_id == user_id)
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def get_pets_for_user(session: AsyncSession, user_id: str) -> list[Pet]:
    """Every pet this user participates in, founding room first. A household
    is at most two rooms today; the ordering keeps the founding pet stable."""
    q = (
        select(Pet)
        .join(PetParticipant, PetParticipant.pet_id == Pet.id)
        .where(PetParticipant.user_id == user_id)
        .order_by(Pet.adopted_at.asc().nulls_last(), Pet.id.asc())
    )
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_household_pets(session: AsyncSession, household_id: str) -> list[Pet]:
    q = (
        select(Pet)
        .where(Pet.household_id == household_id)
        .order_by(Pet.adopted_at.asc().nulls_last(), Pet.id.asc())
    )
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_household_sibling(session: AsyncSession, pet: Pet) -> Pet | None:
    """The pet next door, if the household has a second room yet."""
    q = (
        select(Pet)
        .where(Pet.household_id == pet.household_id, Pet.id != pet.id)
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def get_participant(
    session: AsyncSession, pet_id: str, user_id: str
) -> PetParticipant | None:
    return await session.get(PetParticipant, {"pet_id": pet_id, "user_id": user_id})


async def resolve_active_pet(session: AsyncSession, user: User) -> Pet | None:
    """The room this human is standing in: their last-left room when it's
    still theirs, else the founding pet. Never returns a room the user only
    half-joined (unconfirmed co-adoption stays behind the ceremony)."""
    pets = await get_pets_for_user(session, user.id)
    if not pets:
        return None
    by_id = {pet.id: pet for pet in pets}
    if user.last_room_pet_id and user.last_room_pet_id in by_id:
        participant = await get_participant(session, user.last_room_pet_id, user.id)
        if participant and participant.confirmed_adoption_at is not None:
            return by_id[user.last_room_pet_id]
    for pet in pets:
        participant = await get_participant(session, pet.id, user.id)
        if participant and participant.confirmed_adoption_at is not None:
            return pet
    return None


def _trace_freshness(created_at: datetime) -> str:
    age_seconds = max(0.0, (utc_now() - created_at).total_seconds())
    if age_seconds < 20 * 60:
        return "fresh"
    if age_seconds < 6 * 60 * 60:
        return "recent"
    return "earlier"


_INTERACTION_TYPES = ("greet", "feed", "pet", "walk", "call", "message", "play", "visit", "host")


async def recent_shared_trace(session: AsyncSession, pet_id: str) -> dict | None:
    cutoff = utc_now() - timedelta(hours=18)
    q = (
        select(BufferEvent, User.display_name)
        .outerjoin(User, User.id == BufferEvent.user_id)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.user_id.is_not(None),
            BufferEvent.created_at >= cutoff,
            BufferEvent.event_type.in_(_INTERACTION_TYPES),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(1)
    )
    result = await session.execute(q)
    row = result.first()
    if row is None:
        return None
    event, display_name = row
    return {
        "event_id": event.id,
        "event_type": event.event_type,
        "user_id": event.user_id,
        "display_name": display_name or "your other human",
        "freshness": _trace_freshness(event.created_at),
        "created_at": event.created_at.isoformat(),
    }


async def recent_partner_traces(
    session: AsyncSession,
    pet_id: str,
    *,
    current_user_id: str | None,
    hours: int = 18,
) -> list[dict]:
    """All recent traces left by users OTHER than the current viewer, one row per
    event_type (most recent wins). Drives multi-trace ambient rendering.
    Returns [] when current_user_id is None (server-broadcast contexts)."""
    if current_user_id is None:
        return []
    cutoff = utc_now() - timedelta(hours=hours)
    q = (
        select(BufferEvent, User.display_name)
        .outerjoin(User, User.id == BufferEvent.user_id)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.user_id.is_not(None),
            BufferEvent.user_id != current_user_id,
            BufferEvent.created_at >= cutoff,
            BufferEvent.event_type.in_(_INTERACTION_TYPES),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
    )
    result = await session.execute(q)
    seen: set[str] = set()
    traces: list[dict] = []
    for event, display_name in result.all():
        if event.event_type in seen:
            continue
        seen.add(event.event_type)
        traces.append(
            {
                "event_id": event.id,
                "event_type": event.event_type,
                "user_id": event.user_id,
                "display_name": display_name or "your other human",
                "freshness": _trace_freshness(event.created_at),
                "created_at": event.created_at.isoformat(),
            }
        )
    return traces


async def pet_couple_rhythm_summary(
    session: AsyncSession,
    pet_id: str,
) -> dict:
    """Pet-level (no current viewer) couple-rhythm: are all active participants
    recently around, or has one fallen silent for hours?

    "Active" = interacted within the past 7 days (so ghost orphan participants
    don't drag the indicator down forever). Recent ≤ 60min. Lopsided ≥ 4h
    gap between most-recent and least-recent active partner.
    """
    pp_q = (
        select(PetParticipant.user_id).where(PetParticipant.pet_id == pet_id)
    )
    user_ids = list((await session.execute(pp_q)).scalars().all())
    if len(user_ids) < 2:
        return {"both_recent": False, "lopsided": False, "active_count": len(user_ids)}

    week_ago = utc_now() - timedelta(days=7)
    ages_minutes: list[int] = []
    for uid in user_ids:
        q = (
            select(BufferEvent.created_at)
            .where(
                BufferEvent.pet_id == pet_id,
                BufferEvent.user_id == uid,
                BufferEvent.event_type.in_(_INTERACTION_TYPES),
            )
            .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
            .limit(1)
        )
        last = (await session.execute(q)).scalar_one_or_none()
        if last is None or last < week_ago:
            continue  # dormant — skip
        ages_minutes.append(int((utc_now() - last).total_seconds() / 60))
    if len(ages_minutes) < 2:
        return {"both_recent": False, "lopsided": False, "active_count": len(ages_minutes)}
    max_age = max(ages_minutes)
    min_age = min(ages_minutes)
    return {
        "both_recent": max_age < 60,
        "lopsided": max_age - min_age > 240,
        "active_count": len(ages_minutes),
    }


async def viewer_last_action_minutes(
    session: AsyncSession,
    pet_id: str,
    *,
    current_user_id: str | None,
) -> int | None:
    """Minutes since the viewer's own last interaction with the pet."""
    if current_user_id is None:
        return None
    q = (
        select(BufferEvent.created_at)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.user_id == current_user_id,
            BufferEvent.event_type.in_(_INTERACTION_TYPES),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(1)
    )
    last = (await session.execute(q)).scalar_one_or_none()
    if last is None:
        return None
    return int(max(0.0, (utc_now() - last).total_seconds() / 60))


async def partner_absence_minutes(
    session: AsyncSession,
    pet_id: str,
    *,
    current_user_id: str | None,
) -> int | None:
    """Minutes since the most recent INTERACTION from a user OTHER than the
    viewer. Adoption / join events don't count — they're one-shots, not the
    ongoing presence the door-pull behavior is trying to reflect. None if no
    partner has interacted yet, or no current viewer."""
    if current_user_id is None:
        return None
    q = (
        select(BufferEvent.created_at)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.user_id.is_not(None),
            BufferEvent.user_id != current_user_id,
            BufferEvent.event_type.in_(_INTERACTION_TYPES),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(1)
    )
    result = await session.execute(q)
    last_at = result.scalar_one_or_none()
    if last_at is None:
        return None
    return int(max(0.0, (utc_now() - last_at).total_seconds() / 60))


async def recent_room_notes(
    session: AsyncSession,
    pet_id: str,
    *,
    current_user_id: str | None = None,
    limit: int = 3,
    viewer_aliases: dict[str, str] | None = None,
) -> list[dict]:
    cutoff = utc_now() - timedelta(hours=18)
    q = (
        select(BufferEvent, User.display_name)
        .outerjoin(User, User.id == BufferEvent.user_id)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.user_id.is_not(None),
            BufferEvent.created_at >= cutoff,
            BufferEvent.event_type.in_(_INTERACTION_TYPES),
        )
        .order_by(BufferEvent.created_at.desc(), BufferEvent.id.desc())
        .limit(limit)
    )
    result = await session.execute(q)
    notes: list[dict] = []
    for event, display_name in result.all():
        freshness = _trace_freshness(event.created_at)
        actor_name = display_name or "your other human"
        notes.append(
            {
                "event_id": event.id,
                "event_type": event.event_type,
                "user_id": event.user_id,
                "display_name": actor_name,
                "freshness": freshness,
                "created_at": event.created_at.isoformat(),
                "line": _room_note_line(
                    event.event_type,
                    display_name=actor_name,
                    freshness=freshness,
                    current_user_id=current_user_id,
                    actor_user_id=event.user_id,
                    viewer_aliases=viewer_aliases,
                ),
            }
        )
    return notes


async def unseen_message_notes(
    session: AsyncSession,
    pet_id: str,
    *,
    viewer_id: str,
    limit: int = 6,
) -> list[dict]:
    """Partner whispers (message events) the viewer has never seen. No time
    cutoff — an unheard line waits until it is heard; that is the point."""
    q = (
        select(BufferEvent, User.display_name)
        .outerjoin(User, User.id == BufferEvent.user_id)
        .where(
            BufferEvent.pet_id == pet_id,
            BufferEvent.event_type == "message",
            BufferEvent.user_id.is_not(None),
            BufferEvent.user_id != viewer_id,
            BufferEvent.seen_at.is_(None),
        )
        .order_by(BufferEvent.created_at.asc(), BufferEvent.id.asc())
        .limit(limit)
    )
    result = await session.execute(q)
    notes: list[dict] = []
    for event, display_name in result.all():
        text = (event.meta or {}).get("text", "")
        if not text:
            continue
        notes.append(
            {
                "event_id": event.id,
                "text": text,
                "by_user_id": event.user_id,
                "by_display_name": display_name or "your other human",
                "created_at": event.created_at.isoformat(),
            }
        )
    return notes


async def mark_note_seen(
    session: AsyncSession,
    event_id: int,
    *,
    pet_id: str,
    viewer_id: str,
) -> bool:
    """Stamp seen_at, but only if the event belongs to the viewer's pet and
    was authored by someone else — a sender cannot 'see' their own note."""
    event = await session.get(BufferEvent, event_id)
    if (
        event is None
        or event.pet_id != pet_id
        or event.user_id is None
        or event.user_id == viewer_id
    ):
        return False
    if event.seen_at is None:
        event.seen_at = utc_now()
    return True


async def write_mood(session: AsyncSession, pet: Pet, mood: MoodState) -> None:
    pet.mood_arousal = mood.arousal
    pet.mood_valence = mood.valence
    pet.animation_state = mood.animation_state
    pet.last_mood_drift_at = mood.last_drift_at


async def lock_pet_for_update(session: AsyncSession, pet: Pet) -> None:
    if session.get_bind().dialect.name == "sqlite":
        await session.execute(
            update(Pet)
            .where(Pet.id == pet.id)
            .values(mood_arousal=Pet.mood_arousal)
            .execution_options(synchronize_session=False)
        )
    else:
        await session.execute(
            select(Pet.id).where(Pet.id == pet.id).with_for_update()
        )
    await session.refresh(pet)


async def lock_pet_for_mood_update(session: AsyncSession, pet: Pet) -> None:
    await lock_pet_for_update(session, pet)


def read_mood(pet: Pet) -> MoodState:
    return MoodState(
        arousal=pet.mood_arousal,
        valence=pet.mood_valence,
        animation_state=pet.animation_state,
        last_drift_at=pet.last_mood_drift_at,
    )


async def get_or_create_invite(session: AsyncSession, pet_id: str) -> MagicLink:
    """Idempotent. Returns the pet's current unused, unexpired invite,
    or creates a fresh one if none exists. This prevents invite-link churn
    when the scene polls pet state on every page load.

    We commit a newly-created invite before releasing the lock so concurrent
    sessions can observe the row instead of minting a second live token."""
    pet = await get_pet(session, pet_id)
    if pet is None:
        raise ValueError("pet missing")
    await lock_pet_for_update(session, pet)
    if await participant_count(session, pet_id) >= settings.household_size:
        raise ValueError("pet already has two humans")
    now = utc_now()
    q = (
        select(MagicLink)
        .where(
            MagicLink.pet_id == pet_id,
            MagicLink.purpose == "invite",
            MagicLink.used_at.is_(None),
            MagicLink.expires_at > now,
        )
        .order_by(MagicLink.expires_at.desc())
        .limit(1)
    )
    result = await session.execute(q)
    existing = result.scalar_one_or_none()
    if existing is not None:
        await session.commit()
        return existing
    link = MagicLink(
        id=gen_id(),
        issued_for=pet_id,
        token=secrets.token_urlsafe(32),
        pet_id=pet_id,
        purpose="invite",
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(link)
    await session.flush()
    await session.commit()
    return link


async def consume_invite(session: AsyncSession, token: str) -> MagicLink | None:
    """Atomically mark the invite used. Returns the link on success, None if the
    token is unknown, already used, or expired. Uses a single UPDATE so two
    concurrent clicks can't both pass the used_at IS NULL check (prevents the
    3-participant race)."""
    now = utc_now()
    stmt = (
        update(MagicLink)
        .where(
            MagicLink.token == token,
            MagicLink.purpose == "invite",
            MagicLink.used_at.is_(None),
            MagicLink.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    if result.rowcount != 1:
        return None
    # Re-fetch the link now that it's marked used, so the caller has the pet_id.
    q = select(MagicLink).where(MagicLink.token == token)
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def list_pet_ids(session: AsyncSession) -> list[str]:
    q = select(Pet.id)
    result = await session.execute(q)
    return [row[0] for row in result.all()]


async def first_pet(session: AsyncSession) -> Pet | None:
    """The single pet of a normal deployment — oldest adoption wins. Used by
    read-only guest mode when GUEST_PET_ID isn't pinned."""
    q = select(Pet).order_by(Pet.adopted_at.asc().nulls_last(), Pet.id.asc()).limit(1)
    result = await session.execute(q)
    return result.scalar_one_or_none()
