"""Canonical scene-fx vocabulary + shared-trace cue mapping (woolroom Phase 0).

Single source of truth for the fx-mode vocabulary that flows from the server
(scene_fx action/mood tables, quirk fx, http modifiers) to the client
(wool.js scene + ACTION_FX_MODES echo-swallow, sound.js motifs), and for the
shared-trace → ambient-cue mapping. It replaces the four drifting copies of
this contract — see docs/design/HLD.md ("fx/room contract must version or
every pack rots invisibly").

Versioning: bump FX_VOCAB_VERSION on ANY mode add/remove/rename. Loaders must
error loudly on unknown modes — the silent no-op on unknown modes is the rot
this module exists to kill.

Deliberately NOT in the registry:
- `action:<action>` base steps — compile_scene_plan mints them dynamically
  (`f"action:{action}"`); clients strip the prefix and switch on the action.
- `alert_perk` — retired mood override; survives only as a comment in
  scene_fx.py. No live code references it, so it is not a vocabulary entry.
"""

from __future__ import annotations

FX_VOCAB_VERSION = 1

# Every fx mode referenced today across scene_fx.py's tables, wool.js, and
# sound.js. mode → one-line meaning.
FX_MODES: dict[str, str] = {
    # ── action base fx (runtime/scene_fx.py ACTION_SCENE_FX) ──
    "greet": "greeting bounce when a human says hi",
    "petting": "default stroke response — petting sway",
    "kibble": "feed scatter at the bowl",
    "leash_tug": "walk excitement, leash toward the door",
    "call_ring": "summoning response to a call",
    "message_ping": "a message lands; the pet carries it",
    "zoomie": "play / zoomie_initiator tear around the room",
    # ── mood overrides (runtime/scene_fx.py MOOD_ACTION_SCENE_FX) ──
    "flinch_away": "grumpy pet shrinks from the hand",
    "petting_melt": "content low-arousal pet melts under a stroke",
    "head_tilt": "curious tilt at a message",
    "sigh_settle": "low-arousal greet ends in a settled sigh",
    # ── quirk fx (engine/quirks.py — modifiers/scene_fx, never action defaults) ──
    "threshold_refusal": "walk refused at the threshold",
    "stash": "hides a treasured thing",
    "carry": "carries the hidden thing around",
    "lean_in": "leans into the human",
    "side_eye": "judgemental sideways glance",
    # ── interaction modifiers (api/http.py hitboxes / refusal) ──
    "ignored": "a tap during refusal/busy is acknowledged with stillness",
    "petting_head": "hitbox-targeted stroke: head",
    "petting_ear": "hitbox-targeted stroke: ear",
    "petting_tail": "hitbox-targeted stroke: tail",
    "petting_belly": "hitbox-targeted stroke: belly",
    # ── shared-trace cue modes (TRACE_CUE_MAP below) ──
    # Emitted ONLY as trace cues, never as scene_fx / plan steps; wool.js
    # renders them as CSS evidence classes and sound.js deliberately has no
    # motifs for them (ambient evidence is silent).
    # Client-side discrepancy, reported not fixed: wool.js's trace rendering
    # also accepts a "kibble" cue mode (woolTraceClasses/woolTraceStyle), but
    # no server cue ever emits it — feed cues are "bowl". Dead defensiveness.
    "warm_spot": "partner's greeting left a warm spot on the rug",
    "brushed_coat": "partner's stroke left brush lines in the coat (~1h)",
    "bowl": "partner fed them; the bowl is still out",
    "leash": "partner walked them; the leash hangs by the door",
    "phone_glow": "partner called/messaged; the phone glows",
    "rumpled_rug": "partner played; the rug stays rumpled",
}

# Shared-trace → ambient cue mapping (moved verbatim from
# app/runtime/shared_trace.py; the ws.js `_deriveSharedTraceCue` fallback must
# match — tests/test_room_contract.py enforces).
TRACE_CUE_MAP: dict[str, dict[str, str]] = {
    "greet": {"mode": "warm_spot", "anchor": "rug"},
    # A partner's stroke brushes the nap of the coat — the pet itself
    # carries the trace, not the rug.
    "pet": {"mode": "brushed_coat", "anchor": "dog"},
    "feed": {"mode": "bowl", "anchor": "floor"},
    "walk": {"mode": "leash", "anchor": "door"},
    "call": {"mode": "phone_glow", "anchor": "shelf"},
    "message": {"mode": "phone_glow", "anchor": "floor"},
    "play": {"mode": "rumpled_rug", "anchor": "rug"},
}
