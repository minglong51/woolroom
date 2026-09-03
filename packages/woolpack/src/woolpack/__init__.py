from importlib.metadata import PackageNotFoundError, version

from woolpack.cards import (
    PET_CARD_SCHEMA_VERSION,
    PetCardError,
    PetCardV1,
    parse_pet_card,
    pet_card_payload,
)
from woolpack.contract import DEFAULT_ENVIRONMENT, PackEnvironment
from woolpack.validation import ValidatedPack, validate_pack

try:
    __version__ = version("woolpack")
except PackageNotFoundError:
    __version__ = "0.2.0"

__all__ = [
    "DEFAULT_ENVIRONMENT",
    "PET_CARD_SCHEMA_VERSION",
    "PackEnvironment",
    "PetCardError",
    "PetCardV1",
    "ValidatedPack",
    "__version__",
    "parse_pet_card",
    "pet_card_payload",
    "validate_pack",
]
