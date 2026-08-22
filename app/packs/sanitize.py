"""SVG sanitizer for pack figure art — fail-closed, stdlib only.

Pack figures are SVG fragment strings injected client-side via `x-html`
(figures.js), exactly like the builtin cat art. A pack is data, never
code (docs/design/woolroom-platform-2026-08-18.md §3.1), so figure art goes
through an allowlist sanitizer at load:

- elements: only ALLOWED_ELEMENTS survive; anything else — including the
  named dangerous set (`script`, `foreignObject`, `image`, `use`, `a`) — is
  dropped WITH its subtree;
- attributes: every `on*` event handler, every `href`/`xlink:href`, and any
  `style` containing `url(` is stripped;
- the root must be a single `<g>` (the pack contract's figure fragment) or
  `<svg>`; unparseable input is rejected.

Namespaces are collapsed to local names: the builtin art carries no
`xmlns`, and the fragment is injected into an already-SVG context.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

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

# Called out by the pack contract; in practice the allowlist above already
# drops everything not listed, these are the ones that must never survive
# even by accident. Kept as an explicit set so the sanitizer reads as the
# contract's checklist.
DANGEROUS_ELEMENTS = frozenset({"script", "foreignobject", "image", "use", "a"})


class SvgSanitizeError(ValueError):
    """The figure art violates the SVG gate (message names the violation)."""


def _local(name: object) -> str:
    """Local (namespace-free), lowercased name of an element tag or attribute."""
    if not isinstance(name, str):
        return ""
    return name.rsplit("}", 1)[-1].lower()


def _clean_element(el: ET.Element) -> ET.Element | None:
    """Rebuild one element under the allowlist, or None to drop it + subtree."""
    tag = _local(el.tag)
    if tag not in ALLOWED_ELEMENTS:
        return None
    out = ET.Element(tag)
    if el.text and el.text.strip():
        out.text = el.text  # <title>/<desc> copy; ET escapes on serialize
    for key, value in el.attrib.items():
        name = _local(key)
        if name.startswith("on"):
            continue  # every event handler attribute
        if name == "href":
            continue  # href and xlink:href
        if name == "style" and "url(" in value.lower().replace(" ", ""):
            continue  # style-borne external references
        out.set(name, value)
    for child in el:
        cleaned = _clean_element(child)
        if cleaned is not None:
            out.append(cleaned)
    return out


def sanitize_svg(text: str) -> str:
    """Sanitize a pack figure fragment, returning the clean SVG string.

    Raises SvgSanitizeError on parse failure or a non-fragment root.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SvgSanitizeError(f"figure does not parse as XML: {exc}") from exc
    tag = _local(root.tag)
    if tag not in {"g", "svg"}:
        raise SvgSanitizeError(f"figure root must be a single <g> fragment (or <svg>), got <{tag}>")
    clean = _clean_element(root)
    assert clean is not None  # root tag was allowlist-checked above
    return ET.tostring(clean, encoding="unicode")
