"""WebApp channel: in-process WebSocket fanout keyed by pet_id."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)

# Per-pet socket cap. 2 humans × ~5 devices/tabs each is more than enough;
# stops runaway tabs or an attacker from exhausting FDs.
MAX_SOCKETS_PER_PET = 10
# Guests watch but never interact; a separate, roomier cap since a shared
# "peek" link can draw more concurrent viewers than two humans ever would.
MAX_GUEST_SOCKETS_PER_PET = 25

# Per-client send timeout so one slow socket can't stall broadcast to others.
BROADCAST_SEND_TIMEOUT_S = 3.0


class WebAppChannel:
    name = "webapp"

    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._user_by_socket: dict[WebSocket, str] = {}
        # Guest sockets live in a SEPARATE bucket: they never count toward
        # online_count/presence, and broadcasts only ever send them sanitized
        # pet_state frames — never response/outing/milestone/presence.
        self._guest_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._delivery_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._mutation_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def register(self, pet_id: str, ws: WebSocket) -> bool:
        accepted, _ = await self.register_user(pet_id, f"legacy:{id(ws)}", ws)
        return accepted

    async def register_user(
        self,
        pet_id: str,
        user_id: str,
        ws: WebSocket,
    ) -> tuple[bool, bool]:
        async with self._lock:
            bucket = self._sockets[pet_id]
            if len(bucket) >= MAX_SOCKETS_PER_PET:
                return False, False
            first_socket = all(
                self._user_by_socket.get(existing) != user_id
                for existing in bucket
            )
            bucket.add(ws)
            self._user_by_socket[ws] = user_id
            return True, first_socket

    async def unregister(self, pet_id: str, ws: WebSocket) -> None:
        user_id = self._user_by_socket.get(ws)
        if user_id is None:
            return
        await self.unregister_user(pet_id, user_id, ws)

    async def unregister_user(
        self,
        pet_id: str,
        user_id: str,
        ws: WebSocket,
    ) -> bool:
        async with self._lock:
            bucket = self._sockets.get(pet_id, set())
            if ws not in bucket or self._user_by_socket.get(ws) != user_id:
                return False
            bucket.discard(ws)
            self._user_by_socket.pop(ws, None)
            last_socket = all(
                self._user_by_socket.get(existing) != user_id
                for existing in bucket
            )
            if not bucket:
                self._sockets.pop(pet_id, None)
            return last_socket

    async def register_guest(self, pet_id: str, ws: WebSocket) -> bool:
        """Returns False if the pet is already at the guest connection cap."""
        async with self._lock:
            bucket = self._guest_sockets[pet_id]
            if len(bucket) >= MAX_GUEST_SOCKETS_PER_PET:
                return False
            bucket.add(ws)
            return True

    async def unregister_guest(self, pet_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._guest_sockets[pet_id].discard(ws)
            if not self._guest_sockets[pet_id]:
                self._guest_sockets.pop(pet_id, None)

    def guest_count(self, pet_id: str) -> int:
        """Best-effort count of connected guest sockets. Kept out of
        online_count on purpose — guests are not presence."""
        return len(self._guest_sockets.get(pet_id, ()))

    def online_count(self, pet_id: str) -> int:
        return len(self.connected_user_ids(pet_id))

    def connected_user_ids(self, pet_id: str) -> set[str]:
        return {
            self._user_by_socket[ws]
            for ws in self._sockets.get(pet_id, ())
            if ws in self._user_by_socket
        }

    @asynccontextmanager
    async def delivery_guard(self, pet_id: str) -> AsyncIterator[None]:
        async with self._delivery_locks[pet_id]:
            yield

    @asynccontextmanager
    async def mutation_guard(self, pet_id: str) -> AsyncIterator[None]:
        async with self._mutation_locks[pet_id]:
            yield

    async def _fanout(
        self,
        sockets: list[WebSocket],
        payload: dict[str, Any],
    ) -> list[WebSocket]:
        async def _send(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(
                    ws.send_json(payload), timeout=BROADCAST_SEND_TIMEOUT_S
                )
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(_send(ws) for ws in sockets))
        return [ws for ws in results if ws is not None]

    async def _reap_participants(
        self,
        pet_id: str,
        dead: list[WebSocket],
    ) -> None:
        if not dead:
            return
        async with self._lock:
            bucket = self._sockets.get(pet_id, set())
            for ws in dead:
                bucket.discard(ws)
                self._user_by_socket.pop(ws, None)
            if not bucket:
                self._sockets.pop(pet_id, None)

    async def _reap_guests(
        self,
        pet_id: str,
        dead: list[WebSocket],
    ) -> None:
        if not dead:
            return
        async with self._lock:
            bucket = self._guest_sockets.get(pet_id, set())
            for ws in dead:
                bucket.discard(ws)
            if not bucket:
                self._guest_sockets.pop(pet_id, None)

    async def send_personalized(
        self,
        pet_id: str,
        events_by_user: dict[str, dict[str, Any]],
        guest_event: dict[str, Any] | None = None,
    ) -> bool:
        async with self.delivery_guard(pet_id):
            return await self._send_personalized(
                pet_id,
                events_by_user,
                guest_event,
            )

    async def _send_personalized(
        self,
        pet_id: str,
        events_by_user: dict[str, dict[str, Any]],
        guest_event: dict[str, Any] | None = None,
    ) -> bool:
        async with self._lock:
            users_before = {
                self._user_by_socket[ws]
                for ws in self._sockets.get(pet_id, ())
                if ws in self._user_by_socket
            }
            participant_groups = [
                (
                    [
                        ws
                        for ws in self._sockets.get(pet_id, ())
                        if self._user_by_socket.get(ws) == user_id
                    ],
                    event,
                )
                for user_id, event in events_by_user.items()
            ]
            guest_targets = (
                list(self._guest_sockets.get(pet_id, ()))
                if guest_event is not None
                else []
            )
        participant_results = await asyncio.gather(*(
            self._fanout(targets, event)
            for targets, event in participant_groups
        ))
        await self._reap_participants(
            pet_id,
            [ws for dead in participant_results for ws in dead],
        )
        if guest_event is not None:
            await self._reap_guests(
                pet_id,
                await self._fanout(guest_targets, guest_event),
            )
        return self.connected_user_ids(pet_id) != users_before

    async def send_to_user(
        self,
        pet_id: str,
        user_id: str,
        event: dict[str, Any],
    ) -> None:
        await self.send_personalized(
            pet_id,
            {user_id: event},
        )

    async def send_to_guests(
        self,
        pet_id: str,
        event: dict[str, Any],
    ) -> None:
        await self.send_personalized(pet_id, {}, event)

    async def broadcast(
        self,
        pet_id: str,
        event: dict[str, Any],
        *,
        exclude: WebSocket | None = None,
        exclude_user_id: str | None = None,
    ) -> bool:
        async with self.delivery_guard(pet_id):
            return await self._broadcast_and_sync(
                pet_id,
                event,
                exclude=exclude,
                exclude_user_id=exclude_user_id,
            )

    async def _broadcast_and_sync(
        self,
        pet_id: str,
        event: dict[str, Any],
        *,
        exclude: WebSocket | None,
        exclude_user_id: str | None,
    ) -> bool:
        changed = await self._broadcast(
            pet_id,
            event,
            exclude=exclude,
            exclude_user_id=exclude_user_id,
        )
        users_changed = changed
        while changed:
            changed = await self._broadcast(
                pet_id,
                {
                    "type": "presence",
                    "online_count": self.online_count(pet_id),
                },
                exclude=None,
                exclude_user_id=None,
            )
            users_changed = users_changed or changed
        return users_changed

    async def _broadcast(
        self,
        pet_id: str,
        event: dict[str, Any],
        *,
        exclude: WebSocket | None,
        exclude_user_id: str | None,
    ) -> bool:
        async with self._lock:
            users_before = {
                self._user_by_socket[ws]
                for ws in self._sockets.get(pet_id, ())
                if ws in self._user_by_socket
            }
            targets = list(self._sockets.get(pet_id, ()))
            guest_targets = list(self._guest_sockets.get(pet_id, ()))
        if exclude is not None:
            targets = [ws for ws in targets if ws is not exclude]
        if exclude_user_id is not None:
            targets = [
                ws
                for ws in targets
                if self._user_by_socket.get(ws) != exclude_user_id
            ]
        if not targets and not guest_targets:
            return False

        if targets:
            await self._reap_participants(
                pet_id,
                await self._fanout(targets, event),
            )

        # Guests receive ONLY sanitized pet_state frames. response / outing /
        # milestone / presence frames are never fanned out to the guest bucket.
        if guest_targets and event.get("type") == "pet_state" and isinstance(event.get("pet"), dict):
            # Lazy import: app.runtime.pet_state imports this channel, so a
            # top-level import here would be circular.
            from app.runtime.pet_state import sanitize_scene_payload

            guest_event = {"type": "pet_state", "pet": sanitize_scene_payload(event["pet"])}
            await self._reap_guests(
                pet_id,
                await self._fanout(guest_targets, guest_event),
            )
        return self.connected_user_ids(pet_id) != users_before


channel = WebAppChannel()
