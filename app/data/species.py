"""Species registry — the one module that owns species identity.

woolroom Phase 0 (docs/packs.md): adding a species touches one pack, zero
engine files. Each entry carries the species' locked temperament (stored on
`pets.temperament` at adoption), its identity coats, its default pronoun, and
the key of its phrase overlay in
`app/data/body_language.py:SPECIES_PHRASE_OVERLAYS` — None means the shared
base tables ARE this species' voice. Plain data; mutated only at boot by the
content-pack loader (`app/packs/loader.py`, via `register_species`), frozen
again before the app serves requests.

The builtin species is the cat — a self-possessed window cat. Present,
watchful, economical with affection, dramatic about dinner. It keeps its own
ledger.
"""

from __future__ import annotations

CAT_TEMPERAMENT: dict = {
    "breed_archetype": "window cat",
    "description": (
        "Self-possessed and watchful. Present, but on its own terms — it will "
        "sit near you for an hour and call that quality time. Economical with "
        "affection, dramatic about dinner. Ignores you politely, never warmly, "
        "never for long."
    ),
    "traits": {
        "warmth_baseline": "medium-low, sincere when it shows",
        "sociability": "selective",
        "energy": "conserved, then one decisive burst",
        "curiosity": "high, silent",
        "expressiveness": "body-language, minimal",
        "stubbornness": "high, polite",
    },
    "ignore_rate": 0.22,  # probability of ignoring a non-food care action
}


SPECIES_REGISTRY: dict[str, dict] = {
    "cat": {
        "temperament": CAT_TEMPERAMENT,
        # Undyed-wool palettes; "marmalade" is the default the art is drawn in.
        "coats": ("tuxedo", "marmalade", "ash"),
        "pronoun": "it",
        # The shared body-language tables are the cat voice — nothing to overlay.
        "phrase_overlay": None,
    },
}

SPECIES: tuple[str, ...] = tuple(SPECIES_REGISTRY)


def register_species(species_id: str, entry: dict) -> None:
    """Boot-time registration hook for content packs.

    `app/packs/loader.py` calls this once per pack species during startup,
    before the app serves any request; the registry is frozen again
    afterwards. `entry` mirrors the builtin shape exactly: `temperament`,
    `coats`, `pronoun`, `phrase_overlay` (overlay key or None). Registering
    an id that already exists — builtin or pack — is an error: packs add
    identity, they never override it.
    """
    global SPECIES
    if species_id in SPECIES_REGISTRY:
        raise ValueError(f"species {species_id!r} is already registered")
    SPECIES_REGISTRY[species_id] = entry
    SPECIES = tuple(SPECIES_REGISTRY)


def _entry_for(species: str | None) -> dict:
    return SPECIES_REGISTRY.get(species or "cat", SPECIES_REGISTRY["cat"])


def temperament_for(species: str | None) -> dict:
    return _entry_for(species)["temperament"]


def coats_for(species: str | None) -> tuple[str, ...]:
    return _entry_for(species)["coats"]


def pronoun_for(species: str | None) -> str:
    return _entry_for(species)["pronoun"]
