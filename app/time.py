"""Shared UTC timestamp helpers.

The app currently stores naive UTC datetimes in SQLite / SQLAlchemy models.
Using a single helper avoids deprecated ``datetime.utcnow()`` calls while
preserving that storage format.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def utc_now() -> datetime:
    """Return a naive UTC datetime for app/storage compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


def home_tz() -> ZoneInfo:
    """The pet's home timezone (HOME_TZ)."""
    return ZoneInfo(settings.home_tz)


def local_now() -> datetime:
    """Aware datetime in the pet's home timezone."""
    return datetime.now(home_tz())


def to_local(dt: datetime) -> datetime:
    """Convert a stored naive-UTC datetime to the home timezone."""
    return dt.replace(tzinfo=UTC).astimezone(home_tz())


def iso_z(dt: datetime | None) -> str | None:
    """Serialize a stored naive-UTC datetime for the browser.

    A bare ``dt.isoformat()`` carries no offset, so ``new Date(...)`` in JS
    parses it as *local* time — every viewer behind UTC sees the moment hours
    in the future. Appending ``Z`` marks it as the UTC it already is.
    """
    if dt is None:
        return None
    s = dt.isoformat()
    return s if dt.tzinfo is not None else s + "Z"
