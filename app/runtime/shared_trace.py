"""Helpers for turning recent shared activity into subtle scene cues."""

from __future__ import annotations

from typing import Any

from app.room_contract import TRACE_CUE_MAP


def build_trace_scene_cue(trace: dict[str, Any] | None, current_user_id: str | None) -> dict[str, Any] | None:
    if not trace or not current_user_id:
        return None
    if trace.get("user_id") == current_user_id:
        return None

    event_type = trace.get("event_type")
    freshness = trace.get("freshness", "earlier")
    intensity = {"fresh": "strong", "recent": "soft", "earlier": "faint"}.get(freshness, "faint")

    # The trace→cue vocabulary lives in app/room_contract.py (TRACE_CUE_MAP).
    # Design note that shaped it: a partner's stroke brushes the nap of the
    # coat — the pet itself carries the trace, not the rug.
    cue = TRACE_CUE_MAP.get(event_type)
    if not cue:
        return None
    payload = {
        **cue,
        "intensity": intensity,
        "event_type": event_type,
        "actor_user_id": trace.get("user_id"),
        "display_name": trace.get("display_name") or "your other human",
        # Client-side age gates (the brushed coat fades ~1h) need the actual
        # timestamp, not just the coarse freshness bucket.
        "created_at": trace.get("created_at"),
    }
    if trace.get("event_id") is not None:
        payload["id"] = f"trace:{trace['event_id']}"
    return payload
