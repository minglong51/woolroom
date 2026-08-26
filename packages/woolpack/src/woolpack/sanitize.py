from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_DTD_MARKER = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)

ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "path",
        "circle",
        "ellipse",
        "rect",
        "line",
        "polyline",
        "polygon",
        "title",
        "desc",
    }
)

DANGEROUS_ELEMENTS = frozenset({"script", "foreignobject", "image", "use", "a"})


class SvgSanitizeError(ValueError):
    pass


def _local(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return name.rsplit("}", 1)[-1].lower()


def _clean_element(el: ET.Element) -> ET.Element | None:
    tag = _local(el.tag)
    if tag not in ALLOWED_ELEMENTS:
        return None
    out = ET.Element(tag)
    if el.text and el.text.strip():
        out.text = el.text
    for key, value in el.attrib.items():
        name = _local(key)
        if name.startswith("on"):
            continue
        if name == "href":
            continue
        if name == "style" and "url(" in value.lower().replace(" ", ""):
            continue
        out.set(name, value)
    for child in el:
        cleaned = _clean_element(child)
        if cleaned is not None:
            out.append(cleaned)
    return out


def sanitize_svg(text: str) -> str:
    if _DTD_MARKER.search(text):
        raise SvgSanitizeError(
            "figure carries a DTD or entity declaration; packs are plain SVG fragments"
        )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SvgSanitizeError(f"figure does not parse as XML: {exc}") from exc
    tag = _local(root.tag)
    if tag not in {"g", "svg"}:
        raise SvgSanitizeError(f"figure root must be a single <g> fragment (or <svg>), got <{tag}>")
    try:
        clean = _clean_element(root)
    except RecursionError:
        raise SvgSanitizeError("figure nests too deeply") from None
    assert clean is not None
    return ET.tostring(clean, encoding="unicode")
