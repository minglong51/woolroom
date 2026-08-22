"""Playdate visits: one pet slips through the door into the sibling room.

In-process and short-lived by design (like scene_fx): a visit is a felt
moment, not a fact. The DB records the *story* (buffer events on both pets);
this module only tracks who is currently standing in the wrong room.

One active visit per household. Expiry is lazy — every read sweeps records
whose time has run out, and the next payload simply no longer carries the
visit; the client turns that disappearance into the going-home beat.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.time import utc_now

# Long enough to feel like company, short enough that "they play together
# sometimes" stays special. Human-invited visits only (phase 1).
VISIT_DURATION_S = 12 * 60

_visits: dict[str, dict[str, Any]] = {}


def _sweep() -> None:
    now = utc_now()
    for visit_id, visit in list(_visits.items()):
        if visit["expires_at"] <= now:
            _visits.pop(visit_id, None)


def start_visit(*, visit_id: str, host_pet_id: str, visitor_pet_id: str) -> dict[str, Any]:
    """Begin a visit. Caller has already validated household + participants.
    Starting a fresh visit replaces any lingering one (a household only has
    one doorway)."""
    _sweep()
    for existing_id, existing in list(_visits.items()):
        if existing["host_pet_id"] in {host_pet_id, visitor_pet_id} or existing[
            "visitor_pet_id"
        ] in {host_pet_id, visitor_pet_id}:
            _visits.pop(existing_id, None)
    now = utc_now()
    visit = {
        "id": visit_id,
        "host_pet_id": host_pet_id,
        "visitor_pet_id": visitor_pet_id,
        "started_at": now.isoformat(timespec="seconds") + "Z",
        "expires_at": now + timedelta(seconds=VISIT_DURATION_S),
        "duration_s": VISIT_DURATION_S,
    }
    _visits[visit_id] = visit
    return visit


def end_visit_for(pet_id: str) -> dict[str, Any] | None:
    """End whatever visit involves this pet, returning the record if any."""
    _sweep()
    for visit_id, visit in list(_visits.items()):
        if pet_id in {visit["host_pet_id"], visit["visitor_pet_id"]}:
            return _visits.pop(visit_id)
    return None


def visit_for(pet_id: str) -> dict[str, Any] | None:
    """The live visit involving this pet (as host or as the one away)."""
    _sweep()
    now = utc_now()
    for visit in _visits.values():
        if pet_id in {visit["host_pet_id"], visit["visitor_pet_id"]}:
            return {
                **{k: v for k, v in visit.items() if k != "expires_at"},
                "remaining_ms": max(
                    0, int((visit["expires_at"] - now).total_seconds() * 1000)
                ),
                "role": "host" if visit["host_pet_id"] == pet_id else "away",
            }
    return None
