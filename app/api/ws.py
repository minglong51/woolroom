"""/ws — scene WebSocket. Authed via signed session cookie."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth.session import COOKIE_NAME, load_user
from app.auth.site_access import (
    GUEST_ACCESS_COOKIE,
    SITE_ACCESS_COOKIE,
    has_guest_access,
    has_site_access,
)
from app.channels.webapp import BROADCAST_SEND_TIMEOUT_S, channel
from app.config import settings
from app.runtime.pet_state import (
    build_guest_scene_payload,
    build_scene_payload,
    resolve_guest_pet,
)
from app.storage import repo
from app.storage.db import SessionLocal

log = logging.getLogger(__name__)

router = APIRouter()

MAX_WS_MESSAGE_BYTES = 4096  # client only ever sends short pings


def _origin_allowed(ws: WebSocket) -> bool:
    """Defend against cross-site WebSocket hijacking (CSWSH). A third-party
    page can open ws:// to us with the victim's cookie auto-attached; reject
    if the Origin doesn't match our configured base URL (or the request's
    own host when BASE_URL isn't set).

    In practice BASE_URL may lag behind the actual host (e.g. localhost vs
    127.0.0.1, or a temporary tunnel), so we also accept an origin whose
    host matches the request Host.
    """
    origin = ws.headers.get("origin")
    if origin is None:
        # Non-browser clients (curl, python) omit Origin — allow them.
        return True
    parsed = urlparse(origin)
    if settings.base_url:
        allowed = settings.base_url.rstrip("/")
        if origin.rstrip("/") == allowed:
            return True
        # Same-host fallback: BASE_URL may be stale, but same-host traffic
        # is almost certainly the app itself.
        req_host = ws.headers.get("host", "")
        return parsed.netloc == req_host
    # Fall back to matching origin host against the request's own host.
    req_host = ws.headers.get("host", "")
    return parsed.netloc == req_host


@router.websocket("/ws")
async def ws_scene(ws: WebSocket) -> None:
    if not _origin_allowed(ws):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    guest = has_guest_access(ws.cookies.get(GUEST_ACCESS_COOKIE))
    site_access = has_site_access(ws.cookies.get(SITE_ACCESS_COOKIE))
    if not site_access and guest:
        await _ws_guest_scene(ws)
        return
    if not site_access:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    cookie = ws.cookies.get(COOKIE_NAME)
    requested_pet_id = ws.query_params.get("pet")
    async with SessionLocal() as session:
        user = await load_user(session, cookie)
        if user and requested_pet_id:
            # Room-scoped socket: the client picks which room it's watching.
            # Unconfirmed (ceremony-pending) rooms refuse, same as REST.
            participant = await repo.get_participant(session, requested_pet_id, user.id)
            if participant is None or participant.confirmed_adoption_at is None:
                user = None  # fall through to the clean close below
                pet = None
            else:
                pet = await repo.get_pet(session, requested_pet_id)
        else:
            pet = await repo.resolve_active_pet(session, user) if user else None
    if not user or not pet:
        # Normal auth failed — a valid guest cookie still gets the room,
        # read-only: sanitized payload, separate socket bucket, no presence.
        if guest:
            await _ws_guest_scene(ws)
            return
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    registered = False
    first_socket = False
    presence_announced = False
    try:
        async with channel.delivery_guard(pet.id):
            accepted, first_socket = await channel.register_user(
                pet.id,
                user.id,
                ws,
            )
            if not accepted:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            registered = True
            async with SessionLocal() as session:
                fresh_pet = await repo.get_pet(session, pet.id)
                initial_payload = (
                    await build_scene_payload(
                        session,
                        fresh_pet,
                        current_user_id=user.id,
                    )
                    if fresh_pet
                    else None
                )
            if initial_payload is None:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            await asyncio.wait_for(
                ws.send_json({"type": "pet_state", "pet": initial_payload}),
                timeout=BROADCAST_SEND_TIMEOUT_S,
            )
            if first_socket:
                await channel._broadcast_and_sync(
                    pet.id,
                    {
                        "type": "presence",
                        "online_count": channel.online_count(pet.id),
                        "user_id": user.id,
                        "display_name": user.display_name,
                        "joined": True,
                    },
                    exclude=None,
                    exclude_user_id=user.id,
                )
                presence_announced = True
        while True:
            msg = await ws.receive_text()
            if len(msg) > MAX_WS_MESSAGE_BYTES:
                # Refuse oversized frames — client only sends short pings.
                await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
            # We ignore payload content — frame itself is the keepalive.
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws error: %s", exc)
    finally:
        async with channel.delivery_guard(pet.id):
            last_socket = (
                await channel.unregister_user(pet.id, user.id, ws)
                if registered
                else False
            )
            if last_socket and (presence_announced or not first_socket):
                await channel._broadcast_and_sync(
                    pet.id,
                    {
                        "type": "presence",
                        "online_count": channel.online_count(pet.id),
                        "user_id": user.id,
                        "display_name": user.display_name,
                        "joined": False,
                    },
                    exclude=None,
                    exclude_user_id=None,
                )


async def _ws_guest_scene(ws: WebSocket) -> None:
    """Read-only guest socket: sanitized initial state, guest bucket (no
    presence, no online_count), then keepalive-only. Guest messages are
    ignored exactly like participant pings — actions flow over REST, which
    guests can't call."""
    async with SessionLocal() as session:
        pet = await resolve_guest_pet(session)
    if pet is None:
        # No demo dog resolvable (misconfigured GUEST_PET_ID, or nothing
        # seeded yet) — close cleanly, never crash, never fall back.
        log.warning("guest ws: no guest pet resolvable; closing")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    registered = False
    try:
        async with channel.delivery_guard(pet.id):
            if not await channel.register_guest(pet.id, ws):
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            registered = True
            async with SessionLocal() as session:
                fresh_pet = await repo.get_pet(session, pet.id)
                initial_payload = (
                    await build_guest_scene_payload(session, fresh_pet)
                    if fresh_pet
                    else None
                )
            if initial_payload is None:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            await asyncio.wait_for(
                ws.send_json({"type": "pet_state", "pet": initial_payload}),
                timeout=BROADCAST_SEND_TIMEOUT_S,
            )
        while True:
            msg = await ws.receive_text()
            if len(msg) > MAX_WS_MESSAGE_BYTES:
                await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("guest ws error: %s", exc)
    finally:
        if registered:
            await channel.unregister_guest(pet.id, ws)
