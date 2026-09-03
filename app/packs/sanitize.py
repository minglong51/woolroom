"""SVG sanitizer for pack figure art — fail-closed, stdlib only.

Pack figures are SVG fragment strings injected client-side via `x-html`
(figures.js), exactly like the builtin cat art. A pack is data, never
code (docs/design/woolroom-platform-2026-08-18.md §3.1), so figure art goes
through an allowlist sanitizer at load:

- elements: only ALLOWED_ELEMENTS survive; anything else — including the
  named dangerous set (`script`, `foreignObject`, `image`, `use`, `a`) — is
  dropped WITH its subtree;
- attributes: every `on*` event handler, Alpine `x-*`/`data-x-*` directive,
  `href`/`xlink:href`, and URL-bearing or CSS-escaped value is stripped;
- the root must be a single `<g>` (the pack contract's figure fragment) or
  `<svg>`; unparseable input is rejected;
- DTD/entity declarations are refused before parse (entity expansion is a
  memory bomb — 338 bytes can expand to megabytes inside the byte caps),
  and over-deep nesting is refused rather than escaping as RecursionError.

Namespaces are collapsed to local names: the builtin art carries no
`xmlns`, and the fragment is injected into an already-SVG context.
"""

from woolpack.sanitize import (
    ALLOWED_ELEMENTS,
    DANGEROUS_ELEMENTS,
    SvgSanitizeError,
    _clean_element,
    _local,
    sanitize_svg,
)

__all__ = [
    "ALLOWED_ELEMENTS",
    "DANGEROUS_ELEMENTS",
    "SvgSanitizeError",
    "_clean_element",
    "_local",
    "sanitize_svg",
]
