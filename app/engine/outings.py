"""Deterministic daily outing fragments.

Outings should vary across days and pets, but stay short and quiet. Keep this
engine deterministic so the same pet/day yields the same fragment regardless of
which worker generates it.
"""

from __future__ import annotations

import hashlib
from typing import Any


BASE_MOVES = (
    "trotted a loose loop",
    "made a neat neighborhood circuit",
    "moved in one patient little orbit",
    "did a tidy block-length patrol",
)

BASE_NOTICES = (
    "nosed a leaf",
    "paused over a warm patch of pavement",
    "checked the same hedge twice",
    "took a long second with a fence post",
)

BASE_AFTERS = (
    "came back acting like nothing at all had happened",
    "returned with the outside still on it",
    "came home mildly wind-combed",
    "arrived back calm, but not uninterested",
)

QUIRK_DETAILS: dict[str, tuple[str, ...]] = {
    "hides_small_things": (
        "found something small and definitely did not bring all of it back",
        "spent too long considering a bottle cap like it was private business",
    ),
    "fixated_watcher": (
        "stopped cold over a drifting speck and watched it like a duty",
        "held still for a long second because a tiny thing moved wrong",
    ),
    "threshold_refuser": (
        "made the doorway negotiation feel longer than it was",
        "had one brief principled objection at the threshold",
    ),
    "content_sigher": (
        "sighed once on the way home as if the outing had been acceptable",
        "let out one small satisfied breath after the corner turn",
    ),
    "one_eye_napper": (
        "came back looking half asleep and fully unconvinced",
        "looked tired on the return and denied it completely",
    ),
    "lean_in_greeter": (
        "came back and did a quiet shoulder-check against a shin",
        "returned and leaned in once instead of making a production of it",
    ),
    "zoomie_initiator": (
        "did one abrupt sideways sprint for reasons known only internally",
        "spent six seconds moving much faster than the moment required",
    ),
    "side_eye_judge": (
        "gave a passing pigeon a look that felt a little personal",
        "side-eyed something ordinary and made it feel accused",
    ),
}


def _pick(seed: str, label: str, options: tuple[str, ...]) -> str:
    digest = hashlib.sha256(f"{seed}:{label}".encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(options)
    return options[idx]


def generate_outing_story(pet: Any, day: str) -> str:
    seed = f"{pet.id}:{day}"
    clauses = [
        _pick(seed, "move", BASE_MOVES),
        _pick(seed, "notice", BASE_NOTICES),
    ]

    quirks = tuple(sorted(pet.quirks or []))
    if quirks:
        chosen_quirk = _pick(seed, "quirk-id", quirks)
        options = QUIRK_DETAILS.get(chosen_quirk)
        if options:
            clauses.append(_pick(seed, f"quirk:{chosen_quirk}", options))

    clauses.append(_pick(seed, "after", BASE_AFTERS))
    return ", ".join(clauses) + "."
