from __future__ import annotations

import dataclasses
import json
import math

import pytest
from woolpack.cards import MAX_SVG_BYTES, PetCardError, parse_pet_card, pet_card_payload


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "card_id": "quiet_cat",
        "species": "cat",
        "coat": "tuxedo",
        "pronoun": "she",
        "svg": "<g><circle class='coat' r='4'/></g>",
        "palette": {"body": "#112233", "belly": "#aabbcc", "point": "#DDEEFF"},
        "geometry": {
            "earBelow": 400,
            "headBelow": 408.5,
            "tail": {"yAbove": 444, "xAbove": 238},
            "belly": {"yAbove": 416, "xAbove": 180, "xBelow": 220},
        },
    }


def test_pet_card_round_trip_has_only_the_versioned_allowlist() -> None:
    card = parse_pet_card(_payload())

    assert card.schema_version == 1
    assert card.card_id == "quiet_cat"
    assert card.species == "cat"
    assert card.coat == "tuxedo"
    assert card.pronoun == "she"
    assert set(pet_card_payload(card)) == {
        "schema_version",
        "card_id",
        "species",
        "coat",
        "pronoun",
        "svg",
        "palette",
        "geometry",
    }
    assert json.loads(json.dumps(pet_card_payload(card))) == pet_card_payload(card)


def test_pet_card_is_deeply_immutable_and_payload_is_a_mutable_copy() -> None:
    card = parse_pet_card(_payload())

    with pytest.raises(dataclasses.FrozenInstanceError):
        card.card_id = "other"
    with pytest.raises(TypeError):
        card.palette["body"] = "#000000"
    with pytest.raises(TypeError):
        card.geometry["tail"]["xAbove"] = 1

    payload = pet_card_payload(card)
    payload["palette"]["body"] = "#000000"
    payload["geometry"]["tail"]["xAbove"] = 1
    assert card.palette["body"] == "#112233"
    assert card.geometry["tail"]["xAbove"] == 238


def test_pet_card_sanitizes_svg() -> None:
    payload = _payload()
    payload["svg"] = (
        "<g onclick='x()' x-init='globalThis.pwned=1' "
        "data-x-effect='globalThis.pwned=2'><script>alert(1)</script>"
        "<circle r='1' fill='url(https://example.invalid/paint)' "
        "filter='url(https://example.invalid/filter)'/></g>"
    )

    svg = parse_pet_card(payload).svg

    assert "onclick" not in svg
    assert "x-init" not in svg
    assert "data-x-effect" not in svg
    assert "script" not in svg
    assert "url(" not in svg
    assert "filter" not in svg
    assert '<circle r="1" />' in svg


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_pet_card_rejects_non_exact_top_level_keys(change: str) -> None:
    payload = _payload()
    if change == "missing":
        payload.pop("coat")
    else:
        payload["private_name"] = "not public"

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("palette", lambda value: value.pop("point")),
        ("palette", lambda value: value.update(secret="#000000")),
        ("geometry", lambda value: value.pop("headBelow")),
        ("geometry", lambda value: value.update(secret=1)),
        ("geometry.tail", lambda value: value["tail"].pop("xAbove")),
        ("geometry.tail", lambda value: value["tail"].update(secret=1)),
        ("geometry.belly", lambda value: value["belly"].pop("xBelow")),
        ("geometry.belly", lambda value: value["belly"].update(secret=1)),
    ],
)
def test_pet_card_rejects_malformed_nested_keys(field: str, mutate: object) -> None:
    payload = _payload()
    target = payload["palette"] if field == "palette" else payload["geometry"]
    mutate(target)

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("card_id", "QuietCat"),
        ("card_id", "a" * 33),
        ("species", "Cat"),
        ("species", "a" * 17),
        ("coat", "gray-tabby"),
        ("coat", "a" * 17),
        ("pronoun", ""),
        ("pronoun", " " * 4),
        ("pronoun", "a" * 17),
    ],
)
def test_pet_card_rejects_invalid_subject_values(field: str, value: object) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


@pytest.mark.parametrize("value", [True, False, math.inf, -math.inf, math.nan, "400", None])
@pytest.mark.parametrize(
    "path",
    [
        ("earBelow",),
        ("headBelow",),
        ("tail", "yAbove"),
        ("tail", "xAbove"),
        ("belly", "yAbove"),
        ("belly", "xAbove"),
        ("belly", "xBelow"),
    ],
)
def test_pet_card_rejects_non_finite_or_non_numeric_geometry(
    path: tuple[str, ...], value: object
) -> None:
    payload = _payload()
    geometry = payload["geometry"]
    if len(path) == 1:
        geometry[path[0]] = value
    else:
        geometry[path[0]][path[1]] = value

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


@pytest.mark.parametrize("color", ["112233", "#12345", "#1234567", "#gggggg", 123456])
def test_pet_card_rejects_invalid_palette_colors(color: object) -> None:
    payload = _payload()
    payload["palette"]["body"] = color

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


def test_pet_card_svg_size_is_measured_as_utf8_bytes() -> None:
    payload = _payload()
    payload["svg"] = "<g><desc>" + ("🐾" * (MAX_SVG_BYTES // 4)) + "</desc></g>"

    with pytest.raises(PetCardError):
        parse_pet_card(payload)


@pytest.mark.parametrize("svg", ["not xml", "<circle/>", "<!DOCTYPE svg><g/>", b"<g/>"])
def test_pet_card_rejects_invalid_svg(svg: object) -> None:
    payload = _payload()
    payload["svg"] = svg

    with pytest.raises(PetCardError):
        parse_pet_card(payload)
