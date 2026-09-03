from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from woolpack.cards import PetCardV1, parse_pet_card, pet_card_payload

PLUGIN_API_VERSION = 2


@dataclass(frozen=True, slots=True)
class OwnerCardSubject:
    user_id: str
    pet_id: str
    species: str
    coat: str


@dataclass(frozen=True, slots=True)
class GuestCardSubject:
    pet_id: str
    species: str
    coat: str


CardValue = Mapping[str, object] | PetCardV1


@dataclass(frozen=True, slots=True)
class BoundPetCard:
    pet_id: str
    card: CardValue


class CatalogOverlayProvider(Protocol):
    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def owner_card(self, subject: OwnerCardSubject) -> BoundPetCard | None: ...

    async def guest_card(self, subject: GuestCardSubject) -> BoundPetCard | None: ...


class EmptyCatalogOverlayProvider:
    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def owner_card(self, subject: OwnerCardSubject) -> None:
        return None

    async def guest_card(self, subject: GuestCardSubject) -> None:
        return None


class CatalogOverlayError(RuntimeError):
    pass


def _payload_for_subject(
    value: BoundPetCard | None,
    *,
    pet_id: str,
    species: str,
    coat: str,
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, BoundPetCard) or value.pet_id != pet_id:
        raise CatalogOverlayError("overlay card does not match its requested pet")
    try:
        raw = pet_card_payload(value.card) if isinstance(value.card, PetCardV1) else value.card
        card = parse_pet_card(raw)
    except Exception as exc:
        raise CatalogOverlayError("overlay returned an invalid pet card") from exc
    if card.species != species or card.coat != coat:
        raise CatalogOverlayError("overlay card does not match its requested subject")
    return pet_card_payload(card)


async def owner_card_payload(
    provider: CatalogOverlayProvider,
    subject: OwnerCardSubject,
) -> dict[str, object] | None:
    try:
        value = await provider.owner_card(subject)
    except Exception as exc:
        raise CatalogOverlayError("owner overlay provider failed") from exc
    return _payload_for_subject(
        value,
        pet_id=subject.pet_id,
        species=subject.species,
        coat=subject.coat,
    )


async def guest_card_payload(
    provider: CatalogOverlayProvider,
    subject: GuestCardSubject,
) -> dict[str, object] | None:
    try:
        value = await provider.guest_card(subject)
    except Exception as exc:
        raise CatalogOverlayError("guest overlay provider failed") from exc
    return _payload_for_subject(
        value,
        pet_id=subject.pet_id,
        species=subject.species,
        coat=subject.coat,
    )
