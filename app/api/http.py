"""REST routes. All JSON except /join/{token} (GET) which is browser-friendly."""

from __future__ import annotations

import html as _html
import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_pet, current_user, current_user_optional, db
from app.auth.session import COOKIE_MAX_AGE, sign
from app.auth.site_access import has_guest_access
from app.channels.webapp import channel
from app.config import settings
from app.data.quirks_catalog import QUIRKS, validate_quirks
from app.data.species import SPECIES_REGISTRY, coats_for
from app.engine.quirks import get_pose_detail_for_pet
from app.memory import buffer, moments
from app.memory import core as core_memory
from app.runtime import visits
from app.runtime.actions import ActionIn, perform_action
from app.runtime.pet_state import (
    HUNGRY_AFTER_MINUTES,
    broadcast_scene_payloads,
    build_guest_scene_payload,
    build_scene_payload,
    fed_minutes_ago,
    resolve_guest_pet,
)
from app.storage import repo
from app.storage.db import SessionLocal
from app.storage.models import Pet, User
from app.time import iso_z, utc_now
from woolroom.overlay import (
    CatalogOverlayError,
    GuestCardSubject,
    OwnerCardSubject,
    guest_card_payload,
    owner_card_payload,
)

log = logging.getLogger(__name__)

router = APIRouter()
PENDING_INVITE_MAX_AGE = 60 * 60


async def _owner_card(
    request: Request,
    user: User,
    pet: Pet | None,
    *,
    coat: str | None = None,
) -> dict | None:
    if pet is None:
        return None
    try:
        return await owner_card_payload(
            request.app.state.catalog_overlay_provider,
            OwnerCardSubject(
                user_id=user.id,
                pet_id=pet.id,
                species=pet.species,
                coat=coat if coat is not None else pet.coat,
            ),
        )
    except CatalogOverlayError:
        log.error("owner catalog overlay refused its card")
        raise HTTPException(status_code=503, detail="site overlay unavailable") from None


async def _guest_card(request: Request, pet: Pet) -> dict | None:
    try:
        return await guest_card_payload(
            request.app.state.catalog_overlay_provider,
            GuestCardSubject(
                pet_id=pet.id,
                species=pet.species,
                coat=pet.coat,
            ),
        )
    except CatalogOverlayError:
        log.error("guest catalog overlay refused its card")
        raise HTTPException(status_code=503, detail="guest card unavailable") from None


# ────────── schemas ──────────


class StartIn(BaseModel):
    display_name: str = Field(default="friend", max_length=64)


def _coat_is_registered(coat: str) -> str:
    if not any(coat in entry["coats"] for entry in SPECIES_REGISTRY.values()):
        raise ValueError(f"unknown coat: {coat}")
    return coat


class AdoptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    quirks: list[str] = Field(
        min_length=settings.quirk_pick_count, max_length=settings.quirk_pick_count
    )
    coat: str = Field(default="marmalade", min_length=1, max_length=16)

    _known_coat = field_validator("coat")(_coat_is_registered)



# ────────── helpers ──────────


def _set_cookie(request: Request, resp: Response, user_id: str) -> None:
    namespace = request.app.state.auth_namespace
    resp.set_cookie(
        key=namespace.session_cookie,
        value=sign(user_id, namespace=namespace),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def _set_pending_invite_cookie(request: Request, resp: Response, token: str) -> None:
    resp.set_cookie(
        key=request.app.state.auth_namespace.pending_invite_cookie,
        value=token,
        max_age=PENDING_INVITE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def _clear_pending_invite_cookie(request: Request, resp: Response) -> None:
    resp.delete_cookie(request.app.state.auth_namespace.pending_invite_cookie, path="/")


def _pet_to_dict(pet: Pet) -> dict:
    """Light payload for adoption response — no DB-dependent fields."""
    return {
        "id": pet.id,
        "name": pet.name,
        "species": pet.species,
        "quirks": pet.quirks,
        "coat": pet.coat,
        "pose_detail": get_pose_detail_for_pet(pet),
        "animation_state": pet.animation_state,
        "mood_arousal": pet.mood_arousal,
        "mood_valence": pet.mood_valence,
        "adopted_at": iso_z(pet.adopted_at),
    }


def _absolute_url(request: Request, path: str) -> str:
    # Prefer an explicit BASE_URL when the operator sets one (useful behind a
    # misconfigured proxy). Otherwise derive from the request itself so dev
    # and prod both produce correct links without manual config.
    base = settings.base_url.rstrip("/") if settings.base_url else str(request.base_url).rstrip("/")
    return f"{base}{path}"


def _join_og_page(request: Request, pet_name: str | None) -> str:
    # Body of the 303 an invite link returns. Browsers follow Location and
    # never render it; link unfurlers (iMessage & friends) read the OG tags
    # here or at the redirect target. The pet's name is the one personal
    # detail an invite already shows on the landing page, so it can be here.
    title = f"you're invited to meet {_html.escape(pet_name)}" if pet_name else "woolroom"
    image = _html.escape(_absolute_url(request, "/static/apple-touch-icon.png"))
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>woolroom</title>"
        f"<meta property='og:title' content='{title}'>"
        "<meta property='og:description' content='a quiet room, shared.'>"
        "<meta property='og:type' content='website'>"
        f"<meta property='og:image' content='{image}'>"
        "<meta name='twitter:card' content='summary'>"
        "</head><body></body></html>"
    )


async def _join_user_to_invite(session: AsyncSession, token: str, user: User) -> Pet:
    candidate = await _peek_invite(session, token)
    if not candidate or not candidate.pet_id:
        raise HTTPException(status_code=404, detail="invite not found or already used")
    pet = await repo.get_pet(session, candidate.pet_id)
    if not pet:
        raise HTTPException(status_code=404, detail="pet missing")
    await repo.lock_pet_for_update(session, pet)
    link = await repo.consume_invite(session, token)
    if not link or link.pet_id != pet.id:
        raise HTTPException(status_code=404, detail="invite not found or already used")

    try:
        await repo.add_participant(session, pet.id, user.id)
    except ValueError as exc:
        if str(exc) == "user already belongs to a different household":
            raise HTTPException(
                status_code=409,
                detail="already have a different pet",
            ) from exc
        # add_participant rejected (>2 humans). Invite was already consumed by
        # the winning request; this one gets a 409.
        raise HTTPException(status_code=409, detail="pet is full") from exc

    existing_adopted_by = await core_memory.get_fact(session, pet.id, "adopted_by")
    if existing_adopted_by and user.display_name not in existing_adopted_by.split(", "):
        await core_memory.set_fact(
            session, pet.id, "adopted_by", f"{existing_adopted_by}, {user.display_name}"
        )

    return pet


# ────────── health + catalog ──────────


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@router.get("/api/quirks")
async def list_quirks() -> dict:
    return {
        "quirks": [
            {"id": qid, "label": q["label"], "description": q["description"]}
            for qid, q in QUIRKS.items()
        ]
    }


@router.get("/api/adoption-defaults")
async def adoption_defaults(request: Request) -> dict[str, dict[str, str]]:
    return request.app.state.adoption_defaults.client_payload()


# ────────── session / identity ──────────


@router.post("/api/start")
async def start(
    request: Request,
    body: StartIn,
    resp: Response,
    background_tasks: BackgroundTasks,
    existing: User | None = Depends(current_user_optional),
    session: AsyncSession = Depends(db),
) -> dict:
    """Create a user + session cookie. Idempotent if cookie already valid.

    New-user creation requires a pending_invite cookie. Without one, /api/start
    refuses — preventing the orphan-duplicate failure mode where a returning
    user mints a fresh User by typing their display_name (and loses their
    pet history because the new user has no participant rows).
    """
    pending_token = request.cookies.get(
        request.app.state.auth_namespace.pending_invite_cookie
    )
    joined_pet = None
    pending_invite_error = None
    if existing is not None:
        await repo.touch_user(session, existing)
        user = existing
        if pending_token:
            try:
                async with session.begin_nested():
                    joined_pet = await _join_user_to_invite(
                        session,
                        pending_token,
                        user,
                    )
                _clear_pending_invite_cookie(request, resp)
            except HTTPException as exc:
                if exc.status_code not in {404, 409}:
                    raise
                _clear_pending_invite_cookie(request, resp)
                pending_invite_error = exc.detail
        if joined_pet is not None:
            await session.commit()
            background_tasks.add_task(_broadcast_joined_pet, joined_pet.id)
        return {
            "user_id": user.id,
            "display_name": user.display_name,
            "joined_pet_id": joined_pet.id if joined_pet else None,
            "pending_invite_error": pending_invite_error,
        }

    if pending_token:
        if await _peek_invite(session, pending_token) is None:
            _clear_pending_invite_cookie(request, resp)
            resp.status_code = status.HTTP_403_FORBIDDEN
            return {"detail": "invite not found or already used"}
        user = await repo.create_user(session, body.display_name)
        try:
            joined_pet = await _join_user_to_invite(
                session,
                pending_token,
                user,
            )
        except HTTPException as exc:
            await session.rollback()
            _clear_pending_invite_cookie(request, resp)
            resp.status_code = exc.status_code
            return {"detail": exc.detail}
        await session.commit()
        _set_cookie(request, resp, user.id)
        _clear_pending_invite_cookie(request, resp)
        background_tasks.add_task(_broadcast_joined_pet, joined_pet.id)
    else:
        # Fresh-deployment bootstrap: the first human is admitted while the
        # users table is empty (otherwise a new self-hosted room is
        # unreachable — invites can only be minted by an existing pet owner).
        # Any existing user closes the gate; a returning user's DB is never
        # empty, so the duplicate-account rationale for invite-only holds.
        if not settings.open_signup and await repo.user_count(session) > 0:
            raise HTTPException(
                status_code=403,
                detail="invite required — ask your partner for a link, or use your saved login bookmark",
            )
        user = await repo.create_user(session, body.display_name)
        await session.commit()
        _set_cookie(request, resp, user.id)

    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "joined_pet_id": joined_pet.id if joined_pet else None,
        "pending_invite_error": pending_invite_error,
    }


async def _broadcast_joined_pet(pet_id: str) -> None:
    try:
        async with SessionLocal() as session:
            pet = await repo.get_pet(session, pet_id)
            if pet is not None:
                await broadcast_scene_payloads(session, pet)
    except Exception:
        log.exception("failed to broadcast joined pet %s", pet_id)


@router.post("/api/logout")
async def logout(request: Request, resp: Response) -> dict:
    resp.delete_cookie(request.app.state.auth_namespace.session_cookie, path="/")
    return {"ok": True}


@router.get("/api/me")
async def me(
    request: Request,
    response: Response,
    user: User | None = Depends(current_user_optional),
    session: AsyncSession = Depends(db),
) -> dict:
    namespace = request.app.state.auth_namespace
    pending_token = request.cookies.get(namespace.pending_invite_cookie)
    response.headers["Cache-Control"] = "private, no-store"
    guest = bool(getattr(request.state, "guest", False)) or (
        user is None
        and has_guest_access(
            request.cookies.get(namespace.guest_access_cookie),
            namespace=namespace,
        )
    )
    if guest:
        return {
            "user": None,
            "pet": None,
            "pending_invite": None,
            "open_signup": False,
            "guest": True,
            "card": None,
        }
    pending_invite = None
    if pending_token:
        peek = await _peek_invite(session, pending_token)
        if peek and peek.pet_id:
            invite_pet = await repo.get_pet(session, peek.pet_id)
            if invite_pet:
                facts = await core_memory.all_facts(session, invite_pet.id)
                pending_invite = {
                    "pet_id": invite_pet.id,
                    "pet_name": invite_pet.name,
                    "adopted_by": facts.get("adopted_by"),
                }
    # Lets the boot path fall back to the read-only guest scene when there's
    # no session. Never grants anything by itself — the guest endpoints
    # re-verify the cookie server-side.
    if user is None:
        return {
            "user": None,
            "pet": None,
            "pending_invite": pending_invite,
            # Effective openness: mirrors the /api/start bootstrap so the
            # landing page shows the begin form on a fresh deployment.
            "open_signup": settings.open_signup or await repo.user_count(session) == 0,
            "guest": False,
            "card": None,
        }
    pets = await repo.get_pets_for_user(session, user.id)
    pet = await repo.resolve_active_pet(session, user)
    pet_dict = await build_scene_payload(session, pet, current_user_id=user.id) if pet else None
    card = await _owner_card(request, user, pet)
    await repo.touch_user(session, user)
    return {
        "user": {
            "id": user.id,
            "display_name": user.display_name,
            "partner_aliases": (
                user.partner_aliases if isinstance(user.partner_aliases, dict) else {}
            ),
        },
        "pet": pet_dict,
        # Every room of the household, founding first — drives the door and
        # the co-adoption card. "pending" = the viewer hasn't picked the
        # second quirk yet; the room stays behind the ceremony until then.
        "pets": [
            await _pet_summary(session, p, user) for p in pets
        ],
        "active_pet_id": pet.id if pet else None,
        "pending_invite": pending_invite,
        "open_signup": settings.open_signup,
        "guest": False,
        "card": card,
    }


@router.get("/api/card")
async def active_card(
    request: Request,
    response: Response,
    pet_id: Annotated[str, Query(alias="pet", min_length=1, max_length=32)],
    user: Annotated[User | None, Depends(current_user_optional)],
    session: Annotated[AsyncSession, Depends(db)],
) -> dict:
    namespace = request.app.state.auth_namespace
    response.headers["Cache-Control"] = "private, no-store"
    guest = bool(getattr(request.state, "guest", False)) or (
        user is None
        and has_guest_access(
            request.cookies.get(namespace.guest_access_cookie),
            namespace=namespace,
        )
    )
    if guest:
        pet = await resolve_guest_pet(session)
        if pet is None or pet.id != pet_id:
            raise HTTPException(status_code=404, detail="guest card is not available")
        return {"card": await _guest_card(request, pet)}

    if user is not None:
        participant = await repo.get_participant(session, pet_id, user.id)
        if participant is None or participant.confirmed_adoption_at is None:
            raise HTTPException(status_code=403, detail="not your room")
        pet = await repo.get_pet(session, pet_id)
        if pet is None:
            raise HTTPException(status_code=404, detail="no such room")
        return {"card": await _owner_card(request, user, pet)}

    raise HTTPException(status_code=401, detail="guest access required")


@router.get("/api/recovery-url")
async def recovery_url(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """The user's own login bookmark, on demand. A credential, so it rides
    its own endpoint instead of every /api/me — the client asks only while
    the bookmark card still needs it, or on explicit reveal."""
    token = await repo.recovery_token_for(session, user.id)
    return {
        "recovery_url": _absolute_url(request, f"/r/{token}") if token else None,
    }


async def _pet_summary(session: AsyncSession, pet: Pet, user: User) -> dict:
    """One room's heartbeat for the /api/me pets list — enough for the door
    stitch and the ceremony card without a full scene payload per room."""
    participant = await repo.get_participant(session, pet.id, user.id)
    facts = await core_memory.all_facts(session, pet.id)
    fed_minutes = fed_minutes_ago(facts)
    return {
        "id": pet.id,
        "name": pet.name,
        "species": pet.species,
        "coat": pet.coat,
        "quirks": pet.quirks,
        "animation_state": pet.animation_state,
        "hungry": fed_minutes is None or fed_minutes >= HUNGRY_AFTER_MINUTES,
        "participant_count": await repo.participant_count(session, pet.id),
        "online_count": channel.online_count(pet.id),
        "pending": participant is None or participant.confirmed_adoption_at is None,
        "adopted_at": iso_z(pet.adopted_at),
    }


# ────────── read-only guest scene ──────────


@router.get("/api/guest/scene")
async def guest_scene(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db),
) -> dict:
    """The sanitized pet_state for guest visitors. Requires the guest cookie;
    every private field is stripped in build_guest_scene_payload."""
    namespace = request.app.state.auth_namespace
    if not has_guest_access(
        request.cookies.get(namespace.guest_access_cookie),
        namespace=namespace,
    ):
        raise HTTPException(status_code=401, detail="guest access required")
    pet = await resolve_guest_pet(session)
    if pet is None:
        raise HTTPException(
            status_code=404,
            detail="guest demo cat is not available — seed it (scripts/seed_demo_pet.py) and pin GUEST_PET_ID",
        )
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "guest": True,
        "pet": await build_guest_scene_payload(session, pet),
        "card": await _guest_card(request, pet),
    }


# ────────── adoption + invite ──────────

@router.post("/api/adopt")
async def adopt(
    body: AdoptIn,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    allowlist = settings.allowlisted_user_ids
    if allowlist and user.id not in allowlist:
        raise HTTPException(status_code=403, detail="not authorized to adopt")
    existing = await repo.get_pet_for_user(session, user.id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="already have a pet")
    try:
        quirks = validate_quirks(body.quirks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    defaults = request.app.state.adoption_defaults
    coat = body.coat if "coat" in body.model_fields_set else defaults.primary_coat
    if coat not in coats_for(defaults.primary_species):
        raise HTTPException(
            status_code=422,
            detail=f"not a {defaults.primary_species} wool: {coat}",
        )
    pet = await repo.create_pet(
        session,
        body.name,
        quirks,
        coat=coat,
        species=defaults.primary_species,
    )
    await repo.add_participant(session, pet.id, user.id)
    await buffer.add_event(session, pet.id, "adoption", user_id=user.id)
    # Seed core facts so the pet can speak about its own origin from turn one.
    await core_memory.set_fact(session, pet.id, "adopted_by", user.display_name)
    if pet.adopted_at:
        await core_memory.set_fact(
            session, pet.id, "adopted_on", pet.adopted_at.strftime("%Y-%m-%d")
        )
    return {"pet": _pet_to_dict(pet)}


# ────────── household rooms: switch, second pet, playdates ──────────


class RoomIn(BaseModel):
    pet_id: str = Field(min_length=1, max_length=32)


@router.post("/api/room")
async def switch_room(
    body: RoomIn,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """Cross to another room of the household. Remembers the room you left
    last (boot lands there); the full scene payload comes back so the client
    can repaint without a second round trip."""
    participant = await repo.get_participant(session, body.pet_id, user.id)
    if participant is None:
        raise HTTPException(status_code=403, detail="not your room")
    if participant.confirmed_adoption_at is None:
        raise HTTPException(status_code=403, detail="meet them first — pick their second habit")
    pet = await repo.get_pet(session, body.pet_id)
    if pet is None:
        raise HTTPException(status_code=404, detail="no such room")
    card = await _owner_card(request, user, pet)
    user.last_room_pet_id = pet.id
    await session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "ok": True,
        "pet": await build_scene_payload(session, pet, current_user_id=user.id),
        "card": card,
    }


class AdoptSecondIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # ONE quirk here; the partner picks the second at the ceremony. That's
    # the point of the ritual — it arrives half-decided and gets completed.
    quirk: str = Field(min_length=1, max_length=64)
    # Kept for old clients; deployment configuration owns the persisted species.
    species: str = "cat"
    coat: str = "marmalade"

    @field_validator("species")
    @classmethod
    def _registered_species(cls, value: str) -> str:
        if value not in SPECIES_REGISTRY:
            raise ValueError(f"unknown species: {value}")
        return value

    @model_validator(mode="after")
    def _coat_belongs_to_species(self) -> AdoptSecondIn:
        if "species" not in self.model_fields_set:
            _coat_is_registered(self.coat)
        elif self.coat not in coats_for(self.species):
            raise ValueError(f"not a {self.species} wool: {self.coat}")
        return self


@router.post("/api/adopt-second")
async def adopt_second(
    body: AdoptSecondIn,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """Bring the second pet home. Gates: the household exists (a founding pet
    with its two confirmed humans) and has no second room yet. The caller
    picks its name + first quirk; the partner wakes to a card asking for the
    second."""
    allowlist = settings.allowlisted_user_ids
    if allowlist and user.id not in allowlist:
        raise HTTPException(status_code=403, detail="not authorized to adopt")
    if body.quirk not in QUIRKS:
        raise HTTPException(status_code=400, detail=f"unknown quirk: {body.quirk}")
    pets = await repo.get_pets_for_user(session, user.id)
    if not pets:
        raise HTTPException(status_code=404, detail="adopt the first pet before a second")
    founding = pets[0]
    caller = await repo.get_participant(session, founding.id, user.id)
    if caller is None or caller.confirmed_adoption_at is None:
        raise HTTPException(status_code=403, detail="not your household")
    household = await repo.get_household_pets(session, founding.household_id)
    if len(household) >= settings.max_rooms_per_household:
        raise HTTPException(status_code=409, detail="two rooms is plenty for now")
    partner_ids = [
        uid for uid in await repo.list_participant_user_ids(session, founding.id)
        if uid != user.id
    ]
    if len(partner_ids) != settings.household_size - 1:
        raise HTTPException(
            status_code=409,
            detail="the second pet comes home to a shared room — invite your partner first",
        )
    defaults = request.app.state.adoption_defaults
    if "species" in body.model_fields_set and body.species != defaults.secondary_species:
        raise HTTPException(
            status_code=422,
            detail="second adoption species is fixed by this Woolroom deployment",
        )
    coat = body.coat if "coat" in body.model_fields_set else defaults.secondary_coat
    if coat not in coats_for(defaults.secondary_species):
        raise HTTPException(
            status_code=422,
            detail=f"not a {defaults.secondary_species} wool: {coat}",
        )
    pet = await repo.create_pet(
        session,
        body.name,
        [body.quirk],
        coat=coat,
        species=defaults.secondary_species,
        household_id=founding.household_id,
    )
    await repo.add_participant(session, pet.id, user.id, confirmed=True)
    await repo.add_participant(session, pet.id, partner_ids[0], confirmed=False)
    await buffer.add_event(session, pet.id, "adoption", user_id=user.id)
    await core_memory.set_fact(session, pet.id, "adopted_by", user.display_name)
    if pet.adopted_at:
        await core_memory.set_fact(
            session, pet.id, "adopted_on", pet.adopted_at.strftime("%Y-%m-%d")
        )
    await session.commit()
    # Wake the founding room: the partner's app shows the ceremony card, and
    # both rooms learn the sibling exists.
    await channel.broadcast(founding.id, {
        "type": "household",
        "event": "second_arrived",
        "pet_id": pet.id,
        "pet_name": pet.name,
        "species": pet.species,
        "by_user_id": user.id,
        "by_display_name": user.display_name,
    })
    await broadcast_scene_payloads(session, founding)
    await broadcast_scene_payloads(session, pet)
    return {
        "ok": True,
        "pet": await build_scene_payload(session, pet, current_user_id=user.id),
    }


class SecondQuirkIn(BaseModel):
    pet_id: str = Field(min_length=1, max_length=32)
    quirk: str = Field(min_length=1, max_length=64)


@router.post("/api/second-quirk")
async def second_quirk(
    body: SecondQuirkIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """The ceremony's second half: the partner picks the second pet's other
    habit, which confirms their adoption. Until this fires, the second pet's
    room is closed to them (current_pet 403s) and their /api/me marks it
    pending."""
    participant = await repo.get_participant(session, body.pet_id, user.id)
    if participant is None:
        raise HTTPException(status_code=403, detail="not your room")
    if participant.confirmed_adoption_at is not None:
        raise HTTPException(status_code=409, detail="already home")
    if body.quirk not in QUIRKS:
        raise HTTPException(status_code=400, detail=f"unknown quirk: {body.quirk}")
    pet = await repo.get_pet(session, body.pet_id)
    if pet is None:
        raise HTTPException(status_code=404, detail="no such room")
    if body.quirk in (pet.quirks or []):
        raise HTTPException(status_code=409, detail="they already have that one")
    pet.quirks = [*(pet.quirks or []), body.quirk]
    participant.confirmed_adoption_at = utc_now()
    await buffer.add_event(
        session,
        pet.id,
        "adoption",
        user_id=user.id,
        meta={"second_quirk": body.quirk},
    )
    adopted_by = await core_memory.get_fact(session, pet.id, "adopted_by")
    if adopted_by and user.display_name not in adopted_by.split(", "):
        await core_memory.set_fact(
            session, pet.id, "adopted_by", f"{adopted_by}, {user.display_name}"
        )
    await session.commit()
    fragment = (
        f"{pet.name} is all the way home now — "
        f"{user.display_name} picked their second habit."
    )
    for room in await repo.get_household_pets(session, pet.household_id):
        await channel.broadcast(room.id, {
            "type": "milestone",
            "kind": "ceremony",
            "event_type": "adoption",
            "count": None,
            "fragment": fragment,
            "by_user_id": user.id,
            "by_display_name": user.display_name,
        })
        await broadcast_scene_payloads(session, room)
    return {
        "ok": True,
        "pet": await build_scene_payload(session, pet, current_user_id=user.id),
    }


class VisitIn(BaseModel):
    # The room you're standing in — its pet is the one who follows you
    # through the door.
    pet_id: str = Field(min_length=1, max_length=32)


@router.post("/api/visit")
async def start_visit(
    body: VisitIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """Double-tap on the door: the pet you were with follows you next door
    for a playdate. Human-invited only (phase 1); one visit per household."""
    visitor_pet = await repo.get_pet(session, body.pet_id)
    if visitor_pet is None:
        raise HTTPException(status_code=404, detail="no such room")
    host_pet = await repo.get_household_sibling(session, visitor_pet)
    if host_pet is None:
        raise HTTPException(status_code=409, detail="no next door yet")
    for room in (visitor_pet, host_pet):
        participant = await repo.get_participant(session, room.id, user.id)
        if participant is None or participant.confirmed_adoption_at is None:
            raise HTTPException(status_code=403, detail="not your room")
    async with channel.mutation_guard(visitor_pet.id), channel.mutation_guard(host_pet.id):
        visits.start_visit(
            visit_id=f"visit:{repo.gen_id(10)}",
            host_pet_id=host_pet.id,
            visitor_pet_id=visitor_pet.id,
        )
        visit_event = await buffer.add_event(
            session,
            visitor_pet.id,
            "visit",
            user_id=user.id,
            meta={"host": host_pet.name},
        )
        await buffer.add_event(
            session,
            host_pet.id,
            "host",
            user_id=user.id,
            meta={"visitor": visitor_pet.name},
        )
        promoted_moment, milestone_info = await moments.maybe_promote(
            session, visitor_pet.id, visit_event
        )
        await session.commit()
        for room in (visitor_pet, host_pet):
            await broadcast_scene_payloads(session, room)
        if promoted_moment is not None and milestone_info is not None:
            for room in (visitor_pet, host_pet):
                await channel.broadcast(room.id, {
                    "type": "milestone",
                    "kind": milestone_info.kind,
                    "event_type": milestone_info.event_type,
                    "count": milestone_info.count,
                    "fragment": promoted_moment.fragment,
                    "moment_id": promoted_moment.id,
                    "by_user_id": user.id,
                    "by_display_name": user.display_name,
                })
    return {"ok": True, "visit": visits.visit_for(host_pet.id)}


@router.post("/api/visit/end")
async def end_visit(
    body: VisitIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """Walk the visitor home early. Either human, from either room."""
    participant = await repo.get_participant(session, body.pet_id, user.id)
    if participant is None or participant.confirmed_adoption_at is None:
        raise HTTPException(status_code=403, detail="not your room")
    ended = visits.end_visit_for(body.pet_id)
    if ended is not None:
        async with SessionLocal() as broadcast_session:
            for room_id in (ended["host_pet_id"], ended["visitor_pet_id"]):
                room = await repo.get_pet(broadcast_session, room_id)
                if room is not None:
                    await broadcast_scene_payloads(broadcast_session, room)
    return {"ok": True, "ended": ended is not None}


@router.post("/api/invite")
async def make_invite(
    request: Request,
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    count = await repo.participant_count(session, pet.id)
    if count >= settings.household_size:
        raise HTTPException(status_code=409, detail="pet already has two humans")
    try:
        link = await repo.get_or_create_invite(session, pet.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"url": _absolute_url(request, f"/join/{link.token}")}


@router.get("/join/{token}")
async def join(
    request: Request,
    token: str,
    existing: User | None = Depends(current_user_optional),
    session: AsyncSession = Depends(db),
):
    peek = await _peek_invite(session, token)

    # Already-logged-in user clicking a stale invite link (used or expired) for
    # the pet they're already a participant of: silent redirect, don't 404.
    if existing is not None and peek is None:
        stale = await _peek_invite_any(session, token)
        if stale is not None and await repo.is_participant(
            session, stale.pet_id, existing.id
        ):
            return RedirectResponse(url="/", status_code=303)

    if existing is None:
        if peek is None:
            raise HTTPException(status_code=404, detail="invite not found or already used")
        # Still a plain 303 to "/" — but with an OG-tagged body so the invite
        # link unfurls when pasted into a chat. Behavior is unchanged: cookie
        # set, browser lands on the landing page, pending-invite flow takes it.
        invite_pet = await repo.get_pet(session, peek.pet_id)
        redirect = Response(
            content=_join_og_page(request, invite_pet.name if invite_pet else None),
            status_code=303,
            media_type="text/html",
            headers={"location": "/", "Referrer-Policy": "no-referrer"},
        )
        _set_pending_invite_cookie(request, redirect, token)
        return redirect

    # Short-circuit: if the visitor is already a participant of this invite's
    # pet, DON'T burn the invite — just send them back to the scene.
    if peek is not None:
        if await repo.is_participant(session, peek.pet_id, existing.id):
            return RedirectResponse(url="/", status_code=303)
        existing_pet = await repo.get_pet_for_user(session, existing.id)
        if existing_pet is not None and existing_pet.id != peek.pet_id:
            raise HTTPException(
                status_code=409,
                detail="already have a different pet",
            )

    pet = await _join_user_to_invite(session, token, existing)
    await session.commit()

    await broadcast_scene_payloads(session, pet)

    redirect = RedirectResponse(url="/", status_code=303)
    # Don't leak the recovery token in Referer headers on subsequent navigations.
    redirect.headers["Referrer-Policy"] = "no-referrer"
    return redirect


async def _peek_invite(session: AsyncSession, token: str):
    """Non-consuming lookup used only for the short-circuit path."""
    from sqlalchemy import select as _select
    from app.storage.models import MagicLink
    now = utc_now()
    q = _select(MagicLink).where(
        MagicLink.token == token,
        MagicLink.purpose == "invite",
        MagicLink.used_at.is_(None),
        MagicLink.expires_at > now,
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def _peek_invite_any(session: AsyncSession, token: str):
    """Lookup an invite by token regardless of used/expired state. Used to give
    already-authenticated participants a soft-land on stale invite links."""
    from sqlalchemy import select as _select
    from app.storage.models import MagicLink
    q = _select(MagicLink).where(
        MagicLink.token == token,
        MagicLink.purpose == "invite",
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


@router.post("/api/join-pending")
async def join_pending(
    request: Request,
    resp: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    pending_token = request.cookies.get(
        request.app.state.auth_namespace.pending_invite_cookie
    )
    if not pending_token:
        raise HTTPException(status_code=404, detail="no pending invite")
    try:
        pet = await _join_user_to_invite(session, pending_token, user)
    except HTTPException as exc:
        if exc.status_code in {404, 409}:
            _clear_pending_invite_cookie(request, resp)
        raise
    await session.commit()
    _clear_pending_invite_cookie(request, resp)
    payload = await build_scene_payload(session, pet, current_user_id=user.id)
    await broadcast_scene_payloads(session, pet)
    return {"ok": True, "pet": payload}


_RECOVERY_CONFLICT_HTML = """<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<title>woolroom — recovery link</title>
<style>
  body { font: 16px/1.5 ui-rounded, -apple-system, system-ui, sans-serif;
    color: #3a3127; background: #f7f0e3; padding: 40px 24px;
    max-width: 520px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 12px; color: #6a5135; }
  p { margin: 0 0 14px; }
  a { color: #9a6a2c; }
  .muted { color: #8a7a62; font-size: 14px; }
</style></head><body>
<h1>that link isn't for this account</h1>
<p>You're signed in as a different user, so this recovery link can't switch you over.</p>
<p>If this is meant for someone else's device, send the link there. If it's yours, log out first.</p>
<p class='muted'><a href='/'>Back to the room</a></p>
</body></html>"""


@router.get("/r/{token}")
async def recover(
    request: Request,
    token: str,
    existing: User | None = Depends(current_user_optional),
    session: AsyncSession = Depends(db),
):
    user = await repo.user_from_recovery(session, token)
    if not user:
        raise HTTPException(status_code=404, detail="recovery link invalid")
    if existing is not None and existing.id != user.id:
        return HTMLResponse(status_code=409, content=_RECOVERY_CONFLICT_HTML)
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.headers["Referrer-Policy"] = "no-referrer"
    if existing is None:
        _set_cookie(request, redirect, user.id)
    return redirect


# ────────── action ──────────


class AliasesIn(BaseModel):
    partner_aliases: dict[str, str] = Field(default_factory=dict)


@router.put("/api/aliases")
async def update_aliases(
    body: AliasesIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> dict:
    """Persist this viewer's partner-name aliases. Keys can be either the
    partner's user_id (precise) or their display_name (looser). _room_note_line
    tries user_id first, falls back to display_name."""
    cleaned: dict[str, str] = {}
    for k, v in body.partner_aliases.items():
        if not isinstance(v, str):
            continue
        s = v.strip()[:64]
        key = (k or "").strip()[:64]
        if key and s:
            cleaned[key] = s
    user.partner_aliases = cleaned
    await session.commit()
    return {"ok": True, "partner_aliases": cleaned}


class CoatIn(BaseModel):
    coat: str = Field(min_length=1, max_length=16)


@router.put("/api/coat")
async def update_coat(
    body: CoatIn,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    """Change the pet's coat after adoption — the retroactive path for pets
    adopted before coats existed. Broadcasts so the partner's room recolors
    live, like any other pet_state change."""
    allowed = coats_for(pet.species)
    if body.coat not in allowed:
        raise HTTPException(status_code=422, detail=f"not a {pet.species} wool")
    card = await _owner_card(request, user, pet, coat=body.coat)
    pet.coat = body.coat
    await session.commit()
    await broadcast_scene_payloads(session, pet)
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok": True, "coat": pet.coat, "card": card}


class PinMomentIn(BaseModel):
    event_id: int


@router.post("/api/memory/pin")
async def pin_moment(
    body: PinMomentIn,
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    """Manually promote a buffer event to a Moment. Lets users elevate
    interactions the auto-promoter (maybe_promote) might have skipped."""
    moment = await moments.pin_event_as_moment(session, pet.id, body.event_id)
    if moment is None:
        raise HTTPException(status_code=404, detail="event not found")
    await session.commit()
    return {
        "id": moment.id,
        "fragment": moment.fragment,
        "event_type": moment.event_type,
        "created_at": iso_z(moment.created_at),
    }


@router.get("/api/unseen-notes")
async def unseen_notes(
    user: User = Depends(current_user),
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    """Partner whispers this viewer hasn't seen yet. Viewer-scoped on purpose:
    never embedded in build_scene_payload, which action handlers broadcast to
    both sockets from the actor's perspective."""
    notes = await repo.unseen_message_notes(session, pet.id, viewer_id=user.id)
    return {"notes": notes}


@router.post("/api/unseen-notes/{event_id}/seen")
async def mark_unseen_note_seen(
    event_id: int,
    user: User = Depends(current_user),
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    ok = await repo.mark_note_seen(session, event_id, pet_id=pet.id, viewer_id=user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="note not found")
    await session.commit()
    return {"ok": True}


@router.get("/api/memory")
async def memory(
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    """Backwards-looking timeline: adoption, life stage, household, moments.
    The grain is "things worth telling someone about" — buffer events without
    a promoted moment stay private. Used to build a memory drawer in the UI."""
    from app.engine.aging import pet_age_years, life_stage
    from app.memory import core as _core
    from app.storage.models import Moment as _Moment
    facts = await _core.all_facts(session, pet.id)
    moments_q = (
        _sa_select(_Moment)
        .where(_Moment.pet_id == pet.id)
        .order_by(_Moment.created_at.desc(), _Moment.id.desc())
        .limit(40)
    )
    moments_rows = (await session.execute(moments_q)).scalars().all()
    return {
        "pet_name": pet.name,
        "adopted_at": iso_z(pet.adopted_at),
        "pet_age_years": round(pet_age_years(pet.adopted_at), 2),
        "life_stage": life_stage(pet.adopted_at),
        "household_names": (
            [n.strip() for n in facts.get("adopted_by", "").split(",") if n.strip()]
        ),
        "first_walk_day": facts.get("first_walk_day"),
        "first_sigh_day": facts.get("first_sigh_day"),
        "moments": [
            {
                "id": m.id,
                "fragment": m.fragment,
                "event_type": m.event_type,
                "created_at": iso_z(m.created_at),
            }
            for m in moments_rows
        ],
    }


@router.post("/api/action")
async def action(
    body: ActionIn,
    user: User = Depends(current_user),
    pet: Pet = Depends(current_pet),
    session: AsyncSession = Depends(db),
) -> dict:
    async with channel.mutation_guard(pet.id):
        return await perform_action(body, user, pet, session)
