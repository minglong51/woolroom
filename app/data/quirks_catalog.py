"""Eight candidate quirks. Users pick QUIRK_PICK_COUNT (default 2) at adoption.

Each quirk's `behavior` block is the real wiring (the `hooks` field was only
ever a hint): declarative condition-grammar rules that
`app/engine/quirks.py` interprets. Definitions are plain data — dicts, lists,
strings, numbers; no callables — so a future content pack can supply quirks
without shipping code (woolroom Phase 0, docs/design/woolroom-extraction-
2026-08-17.md §3.1).

Rule shape per behavior channel:
- `pose`:      `{when, write}` — `write` maps rig keys to a value (a `set`)
               or a one-entry merge-op dict (`{"min": x}`). Every matching
               rule applies, in catalog order.
- `action`:    `{when, text, ...}` — first matching rule wins. Optional:
               `arousal_delta`, `valence_delta`, `scene_fx`, `fact_updates`,
               `choices`, `priority`.
- `scheduler`: same effect keys as `action`; first match wins.
- `events`:    `{when, emit}` — every matching rule emits `emit`
               (`{type, data}`) as one QuirkEvent.

`when` conditions AND together: action_in, state_in, state_not_in,
valence_gte, valence_lt, arousal_gte, arousal_lt, old_arousal_lt,
old_valence_lt, enters_state (old != s and new == s), fact_day_gate
(fact value != today's date), fact_exists, any (OR over nested when-dicts).

Effect strings interpolate `{today}`, fact keys (e.g. `{hidden_thing}`), and
`choices` picks (`{item}`): choices resolve with one `random.choice` per
firing — only after the rule matches, so non-firing rules never touch the RNG
stream — and the same pick flows into text, scene_fx, and fact_updates.

`priority` (default 0; lower fires first) exists only where the if-chain
order this registry replaced was not catalog order.
"""

from app.config import settings

QUIRKS: dict[str, dict] = {
    "hides_small_things": {
        "label": "Keeps small treasures",
        "description": "Occasionally comes home with a small found thing and files it somewhere only it knows.",
        "complexity": "high",
        "hooks": ["on_outing_return", "on_idle_tick"],
        "behavior": {
            "action": [
                {
                    # Comes home from a walk with a treasure — once per day.
                    "when": {
                        "action_in": ["walk"],
                        "fact_day_gate": "last_hidden_thing_day",
                        "state_in": ["alert", "playful"],
                    },
                    "choices": {
                        "item": [
                            "a twist tie",
                            "a crumpled receipt",
                            "a milk-bottle ring",
                            "a single dry leaf",
                        ],
                    },
                    "text": "*trots in with {item} in its mouth and files it somewhere only it knows*",
                    "valence_delta": 1,
                    "scene_fx": {"mode": "stash", "item": "{item}", "duration_ms": 9000},
                    "fact_updates": {
                        "last_hidden_thing_day": "{today}",
                        "hidden_thing": "{item}",
                    },
                },
                {
                    # Caught carrying the stash — shows it, thinks better of
                    # it. Once per day, and only when feeling warm.
                    "when": {
                        "action_in": ["call", "message", "pet"],
                        "fact_exists": "hidden_thing",
                        "fact_day_gate": "last_hidden_reveal_day",
                        "state_in": ["sitting", "alert", "playful"],
                        "valence_gte": 52,
                    },
                    "text": "*surfaces briefly with {hidden_thing}, thinks better of sharing, and puts it back*",
                    "scene_fx": {"mode": "carry", "item": "{hidden_thing}", "duration_ms": 7000},
                    "fact_updates": {"last_hidden_reveal_day": "{today}"},
                },
            ],
        },
    },
    "fixated_watcher": {
        "label": "Deep watcher",
        "description": "Locks onto one drifting speck of nothing and watches it like television.",
        "complexity": "medium",
        "hooks": ["on_idle_tick"],
        "behavior": {
            "pose": [
                {
                    "when": {"state_in": ["sitting", "alert"]},
                    "write": {
                        "focus_target": "mote",
                        "tail_motion": "still",
                        "head_shift_y": -2,
                    },
                },
            ],
        },
    },
    "threshold_refuser": {
        "label": "Doorway philosopher",
        "description": "Sometimes the doorway is simply wrong, and no one is going through it today.",
        "complexity": "medium",
        "hooks": ["on_action"],
        "behavior": {
            "action": [
                {
                    # The refusal reads the mood BEFORE the walk nudge.
                    # priority: must fire before hides_small_things' stash on
                    # the same walk — the if-chain order this replaces.
                    "priority": -1,
                    "when": {
                        "action_in": ["walk"],
                        "any": [
                            {"old_arousal_lt": 45},
                            {"old_valence_lt": 45},
                        ],
                    },
                    "text": "*sits down at the threshold, and the doorway loses the argument*",
                    "arousal_delta": -18,
                    "valence_delta": -4,
                    "scene_fx": {"mode": "threshold_refusal", "duration_ms": 4400},
                },
            ],
        },
    },
    "content_sigher": {
        "label": "Heavy sigher",
        "description": "Settles into naps with one long, audible sigh, like a tiny pensioner.",
        "complexity": "low",
        "hooks": ["on_mood_transition"],
        "behavior": {
            "events": [
                {
                    "when": {"enters_state": "sleeping"},
                    "emit": {
                        "type": "response",
                        "data": {"text": "*sinks into the nap with one long, audible sigh*", "is_utterance": False},
                    },
                },
            ],
        },
    },
    "one_eye_napper": {
        "label": "Half-asleep napper",
        "description": "Sleeps with one eye cracked open. Awake? No. Watching? Also probably no.",
        "complexity": "low",
        "hooks": ["on_sprite_render"],
        "behavior": {
            "pose": [
                {
                    "when": {"state_in": ["sleeping"]},
                    "write": {"eye_style": "one_eye"},
                },
            ],
        },
    },
    "lean_in_greeter": {
        "label": "Full-body greeter",
        "description": "Says hello by leaning its whole shoulder into you. No words necessary.",
        "complexity": "low",
        "hooks": ["on_action"],
        "behavior": {
            "pose": [
                {
                    # The playful lean is a throw, the sitting lean a press —
                    # two rules so writes stay scalar (8 vs 5 by state).
                    "when": {"state_in": ["playful"], "valence_gte": 55},
                    "write": {"body_lean": 8, "head_shift_y": {"min": -1}},
                },
                {
                    "when": {"state_in": ["sitting"], "valence_gte": 55},
                    "write": {"body_lean": 5, "head_shift_y": {"min": -1}},
                },
            ],
            "action": [
                {
                    "when": {"action_in": ["greet"], "state_not_in": ["sleeping"]},
                    "text": "*leans its whole shoulder into your shin and calls that hello*",
                    "valence_delta": 2,
                    "scene_fx": {"mode": "lean_in", "duration_ms": 2200},
                },
            ],
        },
    },
    "zoomie_initiator": {
        "label": "Sudden sprinter",
        "description": "Rarely, and for no stated reason, rips across the room at full speed.",
        "complexity": "medium",
        "hooks": ["on_scheduler_tick"],
        "behavior": {
            "scheduler": [
                {
                    # The day's first slide into playful, at high energy.
                    "when": {
                        "fact_day_gate": "last_zoomie_day",
                        "enters_state": "playful",
                        "arousal_gte": 68,
                        "valence_gte": 60,
                    },
                    "text": "*rips across the room out of nowhere, cornering like it stole something*",
                    "scene_fx": {"mode": "zoomie", "duration_ms": 7000},
                    "fact_updates": {"last_zoomie_day": "{today}"},
                },
            ],
        },
    },
    "side_eye_judge": {
        "label": "Quiet critic",
        "description": "Certain interruptions earn a long, measured look from across the room.",
        "complexity": "low",
        "hooks": ["on_action"],
        "behavior": {
            "pose": [
                {
                    "when": {"state_in": ["sitting", "alert"], "valence_lt": 55},
                    "write": {
                        "eye_style": "side_eye",
                        "ear_angle": "back",
                        "tail_motion": "still",
                        "focus_target": "side",
                    },
                },
            ],
            "action": [
                {
                    "when": {
                        "action_in": ["call", "message"],
                        "state_in": ["sitting", "alert"],
                        "valence_lt": 55,
                    },
                    "text": "*holds you in a long side-eye, then looks away first, slowly*",
                    "valence_delta": -2,
                    "scene_fx": {"mode": "side_eye", "duration_ms": 2000},
                },
            ],
        },
    },
}


def validate_quirks(quirk_ids: list[str]) -> list[str]:
    unknown = [q for q in quirk_ids if q not in QUIRKS]
    if unknown:
        raise ValueError(f"Unknown quirks: {unknown}")
    if len(quirk_ids) != settings.quirk_pick_count:
        raise ValueError(
            f"Must pick exactly {settings.quirk_pick_count} quirks, got {len(quirk_ids)}"
        )
    return quirk_ids
