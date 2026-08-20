"""Contract test for the species registry (woolroom Phase 0, §3.1):
`app/data/species.py` is the one module that owns species identity — every
registered species carries temperament, coats, pronoun, and a phrase-overlay
key; every phrase overlay in `app/data/body_language.py` belongs to a
registered species; and the adopt-second schema takes its species and coat
vocabulary from the registry, not a hardcoded Literal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.http import AdoptSecondIn
from app.data.body_language import SPECIES_PHRASE_OVERLAYS
from app.data.species import SPECIES, SPECIES_REGISTRY, coats_for, temperament_for

REQUIRED_KEYS = ("temperament", "coats", "pronoun", "phrase_overlay")
OVERLAY_TABLES = ("body", "action", "spot", "tiny")


def test_registry_contains_the_cat_with_required_keys() -> None:
    assert SPECIES == ("cat",)
    assert tuple(SPECIES_REGISTRY) == SPECIES
    for species, entry in SPECIES_REGISTRY.items():
        for key in REQUIRED_KEYS:
            assert key in entry, f"{species} is missing registry key {key!r}"
        assert entry["temperament"]["breed_archetype"], f"{species} has no temperament"
        assert entry["coats"], f"{species} has no coats"
        assert entry["pronoun"], f"{species} has no pronoun"


def test_every_overlay_belongs_to_a_registered_species_and_is_complete() -> None:
    for species, overlay in SPECIES_PHRASE_OVERLAYS.items():
        assert species in SPECIES_REGISTRY, f"overlay for unregistered species {species!r}"
        for table in OVERLAY_TABLES:
            assert table in overlay, f"{species} overlay is missing the {table!r} table"
    for species, entry in SPECIES_REGISTRY.items():
        key = entry["phrase_overlay"]
        if key is not None:
            assert key in SPECIES_PHRASE_OVERLAYS, (
                f"{species} names phrase overlay {key!r} that does not exist"
            )


def test_adopt_second_accepts_every_registered_coat() -> None:
    for species in SPECIES:
        for coat in coats_for(species):
            body = AdoptSecondIn(name="Socks", quirk="content_sigher", species=species, coat=coat)
            assert body.species == species
            assert body.coat == coat


def test_adopt_second_rejects_unregistered_species_and_coats() -> None:
    # Species the registry doesn't carry are refused outright.
    with pytest.raises(ValidationError):
        AdoptSecondIn(name="Socks", quirk="content_sigher", species="dog", coat="red")
    for species in SPECIES:
        with pytest.raises(ValidationError):
            AdoptSecondIn(name="Socks", quirk="content_sigher", species=species, coat="plaid")


def test_adopt_second_defaults_stay_cat_shaped() -> None:
    """Wire compatibility: today's client never sends species/coat."""
    body = AdoptSecondIn(name="Socks", quirk="content_sigher")
    assert body.species == "cat"
    assert body.coat == "marmalade"


def test_temperament_for_parity() -> None:
    assert temperament_for("cat")["breed_archetype"] == "window cat"
    assert temperament_for("cat")["ignore_rate"] == 0.22
    # Unknown and missing species default to the cat, same dict object.
    assert temperament_for(None) is temperament_for("cat")
    assert temperament_for("dog") is temperament_for("cat")
