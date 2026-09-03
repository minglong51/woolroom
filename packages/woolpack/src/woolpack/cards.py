from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from woolpack.sanitize import SvgSanitizeError, sanitize_svg

PET_CARD_SCHEMA_VERSION = 1
MAX_SVG_BYTES = 256 * 1024
MAX_PRONOUN_CHARS = 16

_CARD_KEYS = frozenset(
    {"schema_version", "card_id", "species", "coat", "pronoun", "svg", "palette", "geometry"}
)
_PALETTE_KEYS = frozenset({"body", "belly", "point"})
_GEOMETRY_KEYS = frozenset({"earBelow", "headBelow", "tail", "belly"})
_GEOMETRY_REGION_KEYS = {
    "tail": frozenset({"yAbove", "xAbove"}),
    "belly": frozenset({"yAbove", "xAbove", "xBelow"}),
}
_CARD_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_SUBJECT_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


class PetCardError(ValueError):
    pass


@dataclass(frozen=True)
class PetCardV1:
    schema_version: int
    card_id: str
    species: str
    coat: str
    pronoun: str
    svg: str
    palette: Mapping[str, str]
    geometry: Mapping[str, object]


def _exact_mapping(value: Any, keys: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PetCardError(f"{field} must be a mapping")
    if set(value) != keys:
        raise PetCardError(f"{field} must contain exactly {sorted(keys)}")
    return value


def _identifier(value: Any, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PetCardError(f"{field} has an invalid identifier")
    return value


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PetCardError(f"{field} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise PetCardError(f"{field} must be a finite number")
    return value


def _parse_palette(value: Any) -> Mapping[str, str]:
    palette = _exact_mapping(value, _PALETTE_KEYS, "palette")
    parsed: dict[str, str] = {}
    for key in ("body", "belly", "point"):
        color = palette[key]
        if not isinstance(color, str) or _HEX_COLOR_RE.fullmatch(color) is None:
            raise PetCardError(f"palette.{key} must be a #rrggbb color")
        parsed[key] = color
    return MappingProxyType(parsed)


def _parse_geometry(value: Any) -> Mapping[str, object]:
    geometry = _exact_mapping(value, _GEOMETRY_KEYS, "geometry")
    tail = _exact_mapping(geometry["tail"], _GEOMETRY_REGION_KEYS["tail"], "geometry.tail")
    belly = _exact_mapping(
        geometry["belly"], _GEOMETRY_REGION_KEYS["belly"], "geometry.belly"
    )
    return MappingProxyType(
        {
            "earBelow": _number(geometry["earBelow"], "geometry.earBelow"),
            "headBelow": _number(geometry["headBelow"], "geometry.headBelow"),
            "tail": MappingProxyType(
                {
                    "yAbove": _number(tail["yAbove"], "geometry.tail.yAbove"),
                    "xAbove": _number(tail["xAbove"], "geometry.tail.xAbove"),
                }
            ),
            "belly": MappingProxyType(
                {
                    "yAbove": _number(belly["yAbove"], "geometry.belly.yAbove"),
                    "xAbove": _number(belly["xAbove"], "geometry.belly.xAbove"),
                    "xBelow": _number(belly["xBelow"], "geometry.belly.xBelow"),
                }
            ),
        }
    )


def _parse_svg(value: Any) -> str:
    if not isinstance(value, str):
        raise PetCardError("svg must be a UTF-8 string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PetCardError("svg must be a UTF-8 string") from exc
    if size > MAX_SVG_BYTES:
        raise PetCardError(f"svg exceeds the {MAX_SVG_BYTES}-byte cap")
    try:
        clean = sanitize_svg(value)
    except SvgSanitizeError as exc:
        raise PetCardError(f"svg is invalid: {exc}") from exc
    if len(clean.encode("utf-8")) > MAX_SVG_BYTES:
        raise PetCardError(f"sanitized svg exceeds the {MAX_SVG_BYTES}-byte cap")
    return clean


def parse_pet_card(value: Mapping[str, Any]) -> PetCardV1:
    card = _exact_mapping(value, _CARD_KEYS, "pet card")
    if (
        isinstance(card["schema_version"], bool)
        or card["schema_version"] != PET_CARD_SCHEMA_VERSION
    ):
        raise PetCardError("schema_version must be integer 1")
    if not isinstance(card["schema_version"], int):
        raise PetCardError("schema_version must be integer 1")
    pronoun = card["pronoun"]
    if not isinstance(pronoun, str) or not pronoun.strip():
        raise PetCardError("pronoun must be a non-empty string")
    if len(pronoun) > MAX_PRONOUN_CHARS:
        raise PetCardError(f"pronoun exceeds the {MAX_PRONOUN_CHARS}-character cap")
    return PetCardV1(
        schema_version=PET_CARD_SCHEMA_VERSION,
        card_id=_identifier(card["card_id"], _CARD_ID_RE, "card_id"),
        species=_identifier(card["species"], _SUBJECT_ID_RE, "species"),
        coat=_identifier(card["coat"], _SUBJECT_ID_RE, "coat"),
        pronoun=pronoun,
        svg=_parse_svg(card["svg"]),
        palette=_parse_palette(card["palette"]),
        geometry=_parse_geometry(card["geometry"]),
    )


def pet_card_payload(card: PetCardV1) -> dict[str, object]:
    tail = card.geometry["tail"]
    belly = card.geometry["belly"]
    assert isinstance(tail, Mapping)
    assert isinstance(belly, Mapping)
    return {
        "schema_version": card.schema_version,
        "card_id": card.card_id,
        "species": card.species,
        "coat": card.coat,
        "pronoun": card.pronoun,
        "svg": card.svg,
        "palette": dict(card.palette),
        "geometry": {
            "earBelow": card.geometry["earBelow"],
            "headBelow": card.geometry["headBelow"],
            "tail": dict(tail),
            "belly": dict(belly),
        },
    }
