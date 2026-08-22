"""Demo-pet self-play loop: scoping, cleanliness, jitter bounds, broadcast."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.test_guest_mode import _load_app, _start_and_adopt

PRIVATE_KEYS = {
    "household_names",
    "room_notes",
    "shared_trace",
    "shared_trace_cue",
    "partner_traces",
    "partner_trace_cues",
    "couple_rhythm",
    "partner_absence_minutes",
    "viewer_partner_aliases",
    "origin_line",
    "participant_count",
    "online_count",
}


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


async def _mood_of(session, pet_id: str) -> tuple[int, int, str]:
    from app.storage import repo

    pet = await repo.get_pet(session, pet_id)
    return (pet.mood_arousal, pet.mood_valence, pet.animation_state)


@pytest.mark.asyncio
async def test_self_play_acts_only_on_demo_pet(tmp_path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    from app.config import settings
    from app.scheduler.jobs import demo_self_play_tick
    from app.storage.db import SessionLocal
    from app.storage.models import BufferEvent, LLMCall, Moment

    with TestClient(app):
        # Two pets: a real household's pet and the demo pet. (Adopt is the
        # easiest way to make two valid pets in a test db.)
        with TestClient(app) as owner, TestClient(app) as demo_owner:
            real_pet = _start_and_adopt(owner, "Ash", "Purl")
            demo_pet = _start_and_adopt(demo_owner, "Demo", "biscuit")
        monkeypatch.setattr(settings, "guest_pet_id", demo_pet["id"])

        async with SessionLocal() as session:
            real_before = await _mood_of(session, real_pet["id"])
            demo_before = await _mood_of(session, demo_pet["id"])

            # The demo pet never "talks" and its data stays clean: the tick
            # must not add buffer events, moments, or LLM calls. (The adopt
            # above already wrote one adoption BufferEvent — setup noise —
            # so assert the tick adds nothing rather than asserting zero.)
            async def _counts():
                out = {}
                for model in (BufferEvent, Moment, LLMCall):
                    q = select(func.count(model.id)).where(
                        model.pet_id == demo_pet["id"]
                    )
                    out[model.__name__] = (await session.execute(q)).scalar_one()
                return out

            counts_before = await _counts()

        await demo_self_play_tick()

        async with SessionLocal() as session:
            real_after = await _mood_of(session, real_pet["id"])
            demo_after = await _mood_of(session, demo_pet["id"])

            # Demo pet moved; the real pet is untouched.
            assert demo_after != demo_before
            assert real_after == real_before

            assert await _counts() == counts_before


@pytest.mark.asyncio
async def test_self_play_noop_when_flag_off_or_unpinned(tmp_path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    from app.config import settings
    from app.scheduler.jobs import demo_self_play_tick
    from app.storage.db import SessionLocal

    with TestClient(app) as owner:
        pet = _start_and_adopt(owner, "Demo", "biscuit")

        # Pinned but flag off: no-op.
        monkeypatch.setattr(settings, "guest_pet_id", pet["id"])
        monkeypatch.setattr(settings, "guest_access_enabled", False)
        async with SessionLocal() as session:
            before = await _mood_of(session, pet["id"])
        await demo_self_play_tick()
        async with SessionLocal() as session:
            assert await _mood_of(session, pet["id"]) == before

        # Flag on but unpinned: no-op (dev fallback only applies to the
        # resolve_guest_pet read path, never to the writer job).
        monkeypatch.setattr(settings, "guest_access_enabled", True)
        monkeypatch.setattr(settings, "guest_pet_id", "")
        await demo_self_play_tick()
        async with SessionLocal() as session:
            assert await _mood_of(session, pet["id"]) == before

        # Flag on, pinned at a missing id: no-op, no crash.
        monkeypatch.setattr(settings, "guest_pet_id", "does-not-exist")
        await demo_self_play_tick()
        async with SessionLocal() as session:
            assert await _mood_of(session, pet["id"]) == before


@pytest.mark.asyncio
async def test_self_play_broadcast_reaches_guest_bucket_sanitized(
    tmp_path, monkeypatch
) -> None:
    app = _load_app(tmp_path, monkeypatch)
    from app.channels.webapp import channel
    from app.config import settings
    from app.scheduler.jobs import demo_self_play_tick

    with TestClient(app) as demo_owner:
        demo_pet = _start_and_adopt(demo_owner, "Demo", "biscuit")
        monkeypatch.setattr(settings, "guest_pet_id", demo_pet["id"])

        participant_ws = _FakeWs()
        guest_ws = _FakeWs()
        assert await channel.register(demo_pet["id"], participant_ws)
        assert await channel.register_guest(demo_pet["id"], guest_ws)
        try:
            await demo_self_play_tick()

            assert len(participant_ws.sent) == 1
            assert participant_ws.sent[0]["type"] == "pet_state"

            assert len(guest_ws.sent) == 1
            frame = guest_ws.sent[0]
            assert frame["type"] == "pet_state"
            assert frame["pet"]["id"] == demo_pet["id"]
            assert PRIVATE_KEYS.isdisjoint(frame["pet"].keys())
        finally:
            await channel.unregister(demo_pet["id"], participant_ws)
            await channel.unregister_guest(demo_pet["id"], guest_ws)


def test_self_play_interval_with_jitter_stays_humane(tmp_path, monkeypatch) -> None:
    """45–90 minutes between ticks: lively for a guest watching a few
    minutes, never a metronome."""
    _load_app(tmp_path, monkeypatch)
    from app.scheduler.jobs import SELF_PLAY_INTERVAL_MINUTES, SELF_PLAY_JITTER_SECONDS

    base = SELF_PLAY_INTERVAL_MINUTES * 60
    assert base - SELF_PLAY_JITTER_SECONDS >= 45 * 60
    assert base + SELF_PLAY_JITTER_SECONDS <= 90 * 60
    # And jitter is a meaningful fraction of the interval, not noise-level.
    assert SELF_PLAY_JITTER_SECONDS >= 10 * 60
