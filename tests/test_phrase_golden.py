"""Golden-dump gate for the phrasebook — woolroom Phase 0, pressure-test item 2.

The ~1.3k-line body_language phrasebook is the extraction's one asset with no
behavioral test net: `_pick`'s seeded selection means re-ordering a table or
re-keying a salt silently reshuffles which line a real moment produces while
every membership test stays green. Detection lag on such a drift is "a human
notices the pet talks funny, days later" — with no diff to answer them.

This test pins BOTH the table bytes AND the selection mapping (which phrase
each (action, spot, species, mood, event_id) resolves to), so any voice change
is always a deliberate, reviewable golden diff.

Regenerate after an INTENTIONAL voice change:

    PHRASE_GOLDEN_UPDATE=1 .venv/bin/python -m pytest tests/test_phrase_golden.py

then eyeball the golden diff in review.

Scope notes:
- `fallback_phrase(action="message")` is gated on an unseeded `random()` and
  is covered here through the deterministic `contextual_message_phrase` path.
- `_pick(sequence=None)` falls back to `randrange` (non-deterministic) — only
  pinned indirectly via the seeded paths.
- The `voice` section pins the rest of the server-side copy — moments
  count/template fragments, anniversary fragments, room-note lines,
  `origin_line`, and the rendered `SYSTEM_TEMPLATE` — as resolved OUTPUTS at
  their engine call sites, so the `app/data/voice.py` extraction (and any
  later copy edit) must render byte-identically or show up as a golden diff.
  `origin_line`/system-prompt probes are time-safe: they use offsets from
  `utc_now()` and `adopted_at=None` (fixed 0.0 pet-years) respectively.
- The `client_voice`/`index_voice` sections pin the client copy pack itself
  (`app/data/voice.py:CLIENT_VOICE` — served over GET /api/voice — and
  `INDEX_VOICE` — substituted into index.html at serve time), including the
  coat lists mirrored from `app/data/species.py:coats_for`.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from app.data import body_language as bl
from app.data import voice as voice_mod
from app.memory import core as core_memory
from app.memory import moments
from app.runtime import prompt as prompt_mod
from app.scheduler import jobs
from app.storage import repo
from app.storage.models import BufferEvent, Pet
from app.time import utc_now

GOLDEN = Path(__file__).parent / "fixtures" / "phrase_golden.json"

_MOOD_SAMPLE = [0, 33, 67]  # one value per arousal/valence bucket
_EVENT_IDS = [0, 1, 2, 5, 17, 63]
_SPOT_SAMPLE = sorted(bl.PET_SPOT_LANGUAGE)
_ACTIONS = sorted(k for k in bl.ACTION_LANGUAGE if k != "message")
_INTENT_SETS = {
    "concern": bl._CONCERN_MESSAGES,
    "rest": bl._REST_MESSAGES,
    "affection": bl._AFFECTION_MESSAGES,
    "greeting": bl._GREETING_MESSAGES,
}


def _encode(obj):
    """Canonical JSON-able form; tuple dict keys become 'a|b' strings."""
    if isinstance(obj, dict):
        return {
            ("|".join(str(p) for p in k) if isinstance(k, tuple) else str(k)): _encode(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _fallback_sweep():
    out = {}
    for species in ("cat",):
        combos = [(a, None) for a in [None, *_ACTIONS]] + [("pet", s) for s in _SPOT_SAMPLE]
        for action, spot in combos:
            for arousal in _MOOD_SAMPLE:
                for valence in _MOOD_SAMPLE:
                    for event_id in _EVENT_IDS:
                        bl._last_served.clear()  # isolate each pick from call order
                        phrase = bl.fallback_phrase(
                            arousal, valence, action, spot=spot, event_id=event_id, species=species
                        )
                        out[f"{species}|{action or '-'}|{spot or '-'}|{arousal}|{valence}|{event_id}"] = phrase
    return out


def _repeat_guard():
    """The immediate-repeat guard: same cell + same event twice must advance."""
    out = {}
    for species in ("cat",):
        for action, spot in [(None, None), ("pet", "head"), ("greet", None)]:
            bl._last_served.clear()
            first = bl.fallback_phrase(50, 50, action, spot=spot, event_id=7, species=species)
            second = bl.fallback_phrase(50, 50, action, spot=spot, event_id=7, species=species)
            out[f"{species}|{action or '-'}|{spot or '-'}"] = [first, second]
    return out


def _contextual_sweep():
    probes = {intent: sorted(msgs)[0] for intent, msgs in _INTENT_SETS.items()}
    probes["neutral"] = "zzz qqx"
    out = {}
    for species in ("cat",):
        for allow_utterance in (True, False):
            for label, text in probes.items():
                for arousal in _MOOD_SAMPLE:
                    for valence in _MOOD_SAMPLE:
                        for event_id in [*_EVENT_IDS, None]:
                            phrase = bl.contextual_message_phrase(
                                arousal, valence, text, event_id,
                                allow_utterance=allow_utterance, species=species,
                            )
                            out[f"{species}|{allow_utterance}|{label}|{arousal}|{valence}|{event_id}"] = phrase
    return out


# ────────── voice-pack call-site probes (moments/jobs/repo/core/prompt) ──────────

# Room-note kinds: the nine `templates` keys inside `_room_note_line` plus
# "nap" to pin the `was here` fallback.
_ROOM_NOTE_KINDS = ["call", "feed", "greet", "host", "message", "pet", "play", "visit", "walk", "nap"]
_ROOM_NOTE_FRESHNESS = ["fresh", "recent", "stale"]
_ROOM_NOTE_SUBJECTS = {
    "other": {"current_user_id": "u1", "actor_user_id": "u2"},
    "you": {"current_user_id": "u2", "actor_user_id": "u2"},
    "alias": {"current_user_id": "u1", "actor_user_id": "u2", "viewer_aliases": {"u2": "M"}},
}


def _voice_moments():
    # Every (event_type × milestone) combo: specific lines, the `labels`
    # fallback, and the event_type-itself fallback ("play", "visit"). Count 7
    # pins behavior at a non-milestone count.
    count_fragments = {}
    for event_type in sorted(moments.COUNTABLE_EVENT_TYPES) + ["visit"]:
        for count in [*moments.COUNT_MILESTONES, 7]:
            count_fragments[f"{event_type}|{count}"] = moments._count_milestone_fragment(
                event_type, count
            )
    # Every first-seen event type, plus "anniversary"/"nap" for the default line.
    template_fragments = {
        event_type: moments._template_fragment(BufferEvent(event_type=event_type))
        for event_type in [*sorted(moments.FIRST_SEEN_EVENT_TYPES), "anniversary", "nap"]
    }
    return {
        "count_milestone_fragment": count_fragments,
        "template_fragment": template_fragments,
    }


def _voice_anniversary():
    # All thresholds plus day 45 for the "{days} days together" fallback.
    return {str(days): jobs._anniversary_fragment(days) for days in [*jobs.ANNIVERSARY_DAYS, 45]}


def _voice_room_notes():
    out = {}
    for kind in _ROOM_NOTE_KINDS:
        for freshness in _ROOM_NOTE_FRESHNESS:
            for subject, kwargs in _ROOM_NOTE_SUBJECTS.items():
                out[f"{kind}|{freshness}|{subject}"] = repo._room_note_line(
                    kind, display_name="Mara", freshness=freshness, **kwargs
                )
    return out


def _voice_origin_line():
    now = utc_now()
    out = {
        "no_adopted_by": core_memory.origin_line({}, now),
        "no_adopted_at": core_memory.origin_line({"adopted_by": "Ash"}, None),
    }
    # Offsets from now, so the day buckets (today/yesterday/days/weeks/months)
    # resolve identically on every run.
    for days in (0, 1, 5, 20, 90):
        adopted_at = now - timedelta(days=days)
        out[f"adopted_{days}d_ago"] = core_memory.origin_line({"adopted_by": "Ash"}, adopted_at)
    return out


def _voice_system_prompt():
    # adopted_at=None pins the age line at 0.0 pet-years/kitten — a fixed date
    # would drift the rendering as real time passes.
    pet = Pet(
        id="golden-pet",
        name="Purl",
        household_id="golden-pet",
        adopted_at=None,
        temperament={
            "breed_archetype": "window cat",
            "description": "Self-possessed and watchful, dramatic about dinner.",
        },
        quirks=["content_sigher", "lean_in_greeter"],
    )
    facts = {
        "adopted_by": "Ash & Wren",
        "adopted_on": "2026-05-01",
        "first_walk_day": "2026-05-03",
        "custom_note": "sleeps under the desk",  # unlabeled key → raw key as label
    }
    full = prompt_mod.build_system_prompt(pet, facts)
    # Bare pet: pins the "- (none chosen)" / "- (nothing yet …)" fallbacks and
    # the default breed archetype.
    bare = prompt_mod.build_system_prompt(
        Pet(id="golden-bare", name="Pip", household_id="golden-bare"), None
    )
    return {"full": full, "bare": bare}


def build_dump():
    return {
        "tables": _encode({
            name: getattr(bl, name)
            for name in (
                "BODY_LANGUAGE", "ACTION_LANGUAGE", "MESSAGE_TINY_UTTERANCES",
                "MESSAGE_CONTEXT_LANGUAGE", "PET_SPOT_LANGUAGE",
                "SPECIES_PHRASE_OVERLAYS",
            )
        }),
        "buckets": {str(v): [bl.bucket_arousal(v), bl.bucket_valence(v)]
                    for v in [0, 32, 33, 50, 66, 67, 100]},
        "intent_sets": {k: sorted(v) for k, v in _INTENT_SETS.items()},
        "classify_probes": {
            label: bl.classify_message(text)
            for label, text in
            [(k, sorted(v)[0]) for k, v in _INTENT_SETS.items()] + [("neutral", "zzz qqx")]
        },
        "fallback_selection": _fallback_sweep(),
        "repeat_guard": _repeat_guard(),
        "contextual_selection": _contextual_sweep(),
        "voice": {
            "moments": _voice_moments(),
            "anniversary_fragment": _voice_anniversary(),
            "room_note_line": _voice_room_notes(),
            "origin_line": _voice_origin_line(),
            "system_prompt": _voice_system_prompt(),
        },
        "client_voice": _encode(voice_mod.CLIENT_VOICE),
        "index_voice": _encode(voice_mod.INDEX_VOICE),
    }


def test_phrase_golden():
    dump = json.dumps(build_dump(), sort_keys=True, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("PHRASE_GOLDEN_UPDATE"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(dump, encoding="utf-8")
        return
    assert GOLDEN.exists(), "golden missing — generate once with PHRASE_GOLDEN_UPDATE=1"
    assert dump == GOLDEN.read_text(encoding="utf-8"), (
        "phrasebook voice drifted. If intentional: regenerate with "
        "PHRASE_GOLDEN_UPDATE=1 .venv/bin/python -m pytest tests/test_phrase_golden.py "
        "and review the golden diff."
    )
