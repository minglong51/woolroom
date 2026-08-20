"""Output validator. Rejects chatbot-slop utterances — we want a pet, not an assistant."""

from __future__ import annotations

import re

MAX_CHARS = 80

# Chatbot-energy patterns the pet must NEVER produce.
BANNED_SUBSTRINGS = (
    "let me know",
    "i can help",
    "i'm here to",
    "feel free to",
    "would you like",
    "hope this helps",
    "if you need",
    "as an ai",
    "sorry, i",
    "happy to help",
    "i understand",
)

# Advice/reflective-listening patterns (mirroring the user's feelings back is off-brand).
BANNED_PATTERNS = (
    re.compile(r"\byou should\b", re.I),
    re.compile(r"\bit sounds like\b", re.I),
    re.compile(r"\bi think you\b", re.I),
    re.compile(r"\b(remember|try to|maybe you)\b", re.I),
)

# Off-scene words: the pet lives in a small wooden room with one human, a bowl,
# and a door. Rooms and outdoor settings are hallucination — reject and fall
# back to the scene-safe phrasebook. Items already in the phrasebook (phone,
# screen, leash, bowl, door, rectangle) are intentionally excluded.
# (The scene's fx vocabulary has its own canonical home: app/room_contract.py.)
OFF_SCENE_PATTERNS = (
    re.compile(
        r"\b(kitchen|hallway|bedroom|bathroom|living room|dining room|basement|"
        r"attic|garage|couch|sofa|tv|television|fridge|refrigerator|fireplace|"
        r"garden|yard|backyard|park|street|sidewalk)\b",
        re.I,
    ),
)


def validate(text: str) -> bool:
    """True if this text is safe to emit as a pet utterance."""
    if not text:
        return False
    import unicodedata
    # NFKC folds width/compatibility variants (ﬁ→fi, half-width letters, etc.)
    # so the banlist catches them. True Cyrillic-Latin homoglyph defense would
    # need a confusables filter, out of scope here.
    t = unicodedata.normalize("NFKC", text).strip()
    if len(t) > MAX_CHARS:
        return False
    low = t.lower()
    if any(bad in low for bad in BANNED_SUBSTRINGS):
        return False
    if any(p.search(t) for p in BANNED_PATTERNS):
        return False
    if any(p.search(t) for p in OFF_SCENE_PATTERNS):
        return False
    # No more than one sentence of advice/question shape — a pet rarely asks.
    if t.count("?") > 1:
        return False
    return True


def clean(text: str) -> str:
    """Gentle trim: strip quotes, collapse whitespace, cap length."""
    t = text.strip().strip('"').strip("'")
    t = re.sub(r"\s+", " ", t)
    return t[:MAX_CHARS]
