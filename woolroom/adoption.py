from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdoptionDefaults:
    primary_species: str = "cat"
    primary_coat: str = "marmalade"
    secondary_species: str = "cat"
    secondary_coat: str = "marmalade"

    def validate(self, species_coats: Mapping[str, Collection[str]]) -> None:
        for slot, species, coat in (
            ("primary", self.primary_species, self.primary_coat),
            ("secondary", self.secondary_species, self.secondary_coat),
        ):
            coats = species_coats.get(species)
            if coats is None:
                raise ValueError(
                    f"{slot} adoption species {species!r} is not registered by Woolroom or a loaded pack"
                )
            if coat not in coats:
                raise ValueError(
                    f"{slot} adoption coat {coat!r} is not registered for species {species!r}"
                )

    def client_payload(self) -> dict[str, dict[str, str]]:
        return {
            "primary": {"species": self.primary_species, "coat": self.primary_coat},
            "secondary": {"species": self.secondary_species, "coat": self.secondary_coat},
        }
