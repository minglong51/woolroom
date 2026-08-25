"""The browser boundary for stored naive-UTC datetimes: a bare isoformat()
parses as LOCAL time in JS, so anything the frontend feeds to new Date()
must carry the Z. Regression test for the 2026-08-24 dogfood finding on the
paws sibling — same naive boundary lived here."""

from __future__ import annotations

from datetime import UTC, datetime

from app.time import iso_z


def test_iso_z_marks_naive_utc_explicitly() -> None:
    assert iso_z(datetime(2026, 8, 25, 4, 10, 30)) == "2026-08-25T04:10:30Z"


def test_iso_z_leaves_aware_datetimes_alone() -> None:
    aware = datetime(2026, 8, 25, 4, 10, 30, tzinfo=UTC)
    assert iso_z(aware) == aware.isoformat()


def test_iso_z_passes_none_through() -> None:
    assert iso_z(None) is None
