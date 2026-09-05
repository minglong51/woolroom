from __future__ import annotations

import hashlib
from dataclasses import dataclass
from xml.etree import ElementTree

from woolpack.sanitize import SvgSanitizeError, sanitize_svg


_MAX_TEXT_LENGTH = 256
_MAX_ICON_BYTES = 1_000_000
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _validate_text(field: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty text without surrounding whitespace")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{field} must be at most {_MAX_TEXT_LENGTH} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{field} must not contain control characters")


def _xml_text(value: str | None) -> str:
    return value if value and value.strip() else ""


def _svg_fingerprint(element: ElementTree.Element) -> tuple[object, ...]:
    tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
    attributes = tuple(
        sorted((key.rsplit("}", 1)[-1].lower(), value) for key, value in element.attrib.items())
    )
    return (
        tag,
        attributes,
        _xml_text(element.text),
        _xml_text(element.tail),
        tuple(_svg_fingerprint(child) for child in element),
    )


def _validate_svg(value: bytes) -> None:
    if not isinstance(value, bytes) or not value or len(value) > _MAX_ICON_BYTES:
        raise ValueError("favicon_svg must be non-empty and at most 1 MB")
    if b"<?" in value:
        raise ValueError("favicon_svg must not contain XML processing instructions")
    try:
        text = value.decode("utf-8")
        sanitized = sanitize_svg(text)
        root = ElementTree.fromstring(text)
        sanitized_root = ElementTree.fromstring(sanitized)
    except (ElementTree.ParseError, SvgSanitizeError, UnicodeDecodeError) as exc:
        raise ValueError("favicon_svg must be valid inert SVG") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("favicon_svg root element must be svg")
    if _svg_fingerprint(root) != _svg_fingerprint(sanitized_root):
        raise ValueError("favicon_svg must use only Woolpack's inert SVG allowlist")


def _validate_png(value: bytes) -> None:
    if (
        not isinstance(value, bytes)
        or not value.startswith(_PNG_SIGNATURE)
        or len(value) > _MAX_ICON_BYTES
    ):
        raise ValueError("apple_touch_icon_png must be a PNG no larger than 1 MB")


@dataclass(frozen=True)
class SiteIdentity:
    name: str = "woolroom"
    description: str = "a quiet room, shared."
    access_heading: str | None = None
    access_note: str | None = None
    guest_entry_label: str = "watch the room"
    guest_disclosure: str = (
        "read-only · no sign-in · the public demo · nothing you do changes the room"
    )
    favicon_svg: bytes | None = None
    apple_touch_icon_png: bytes | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("name", self.name),
            ("description", self.description),
            ("access_heading", self.resolved_access_heading),
            ("access_note", self.resolved_access_note),
            ("guest_entry_label", self.guest_entry_label),
            ("guest_disclosure", self.guest_disclosure),
        ):
            _validate_text(field, value)
        if (self.favicon_svg is None) != (self.apple_touch_icon_png is None):
            raise ValueError("custom site icons require both SVG and PNG assets")
        if self.favicon_svg is not None:
            _validate_svg(self.favicon_svg)
            _validate_png(self.apple_touch_icon_png or b"")

    @property
    def resolved_access_heading(self) -> str:
        return self.access_heading if self.access_heading is not None else self.name

    @property
    def resolved_access_note(self) -> str:
        return self.access_note if self.access_note is not None else self.description

    @property
    def asset_version(self) -> str:
        digest = hashlib.sha256()
        for value in (
            self.name,
            self.description,
            self.resolved_access_heading,
            self.resolved_access_note,
            self.guest_entry_label,
            self.guest_disclosure,
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        digest.update(self.favicon_svg or b"")
        digest.update(self.apple_touch_icon_png or b"")
        return digest.hexdigest()[:16]


DEFAULT_SITE_IDENTITY = SiteIdentity()


__all__ = ["DEFAULT_SITE_IDENTITY", "SiteIdentity"]
