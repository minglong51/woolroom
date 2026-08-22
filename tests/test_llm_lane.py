"""Tests for the metered-Haiku prod lane: credential policy, provider switch,
daily budget circuit-breaker, and the no-immediate-repeat phrase selector."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.data.body_language import BODY_LANGUAGE, bucket_arousal, bucket_valence, fallback_phrase
from app.runtime import llm_log
from app.runtime.respond import respond


def _pet(*, arousal: int = 58, valence: int = 66):
    return SimpleNamespace(
        id="pet-1",
        name="Purl",
        temperament={"ignore_rate": 0.0, "breed_archetype": "window cat", "description": "quiet"},
        quirks=[],
        mood_arousal=arousal,
        mood_valence=valence,
        animation_state="sitting",
    )


# ---- credential policy (boot matrix) ----


def _prod_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "env": "prod",
        "secret_key": "x" * 32,
        "anthropic_api_key": "",
    }
    return Settings(**(base | overrides))


def test_prod_boots_with_metered_key():
    s = _prod_settings(anthropic_api_key="sk-ant-api-test-key")
    assert s.is_prod


def test_prod_refuses_oauth_shaped_key():
    with pytest.raises(ValueError, match="metered"):
        _prod_settings(anthropic_api_key="sk-ant-oat01-subscription-token")


def test_prod_refuses_unshaped_key():
    with pytest.raises(ValueError, match="metered"):
        _prod_settings(anthropic_api_key="some-random-token")


def test_prod_boots_without_key():
    s = _prod_settings()
    assert s.anthropic_api_key == ""


# ---- provider switch + budget circuit-breaker ----


@pytest.mark.asyncio
async def test_daily_cap_falls_back_without_llm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("llm must not be called once the daily cap is reached")

    async def _at_cap(pet_id):
        return 50

    monkeypatch.setattr("app.runtime.respond.client.complete", _boom)
    monkeypatch.setattr(llm_log, "calls_today", _at_cap)
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-api-test-key")
    monkeypatch.setattr(settings, "llm_daily_call_cap", 50)

    res = await respond(
        _pet(),
        "message",
        "good morning",
        recent_events=[],
        recent_moments=[],
        core_facts={},
        event_id=1,
    )

    assert res.text  # deterministic fallback, no error


@pytest.mark.asyncio
async def test_anthropic_lane_without_key_stays_deterministic(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("llm must not be called without a key")

    monkeypatch.setattr("app.runtime.respond.client.complete", _boom)
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    res = await respond(
        _pet(),
        "message",
        "good morning",
        recent_events=[],
        recent_moments=[],
        core_facts={},
        event_id=1,
    )

    assert res.text


# ---- phrase selector: seeded rotation, no immediate repeat ----


def test_seeded_pick_never_repeats_immediately():
    arousal, valence = 50, 50  # ("med", "neutral") — a 4-line BODY_LANGUAGE cell
    key = (bucket_arousal(arousal), bucket_valence(valence))
    assert len(BODY_LANGUAGE[key]) > 1

    previous = None
    for event_id in range(1, 41):
        # action=None routes to the generic BODY_LANGUAGE table
        line = fallback_phrase(arousal, valence, event_id=event_id)
        assert line in BODY_LANGUAGE[key]
        assert line != previous
        previous = line


@pytest.mark.asyncio
async def test_calls_today_fails_closed_when_db_is_unavailable(monkeypatch):
    # The budget cap must never fail open on a metered key: with the
    # llm_calls table unreachable, calls_today reports at least the cap so
    # the respond path falls back to the phrasebook.
    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    from app.config import settings

    monkeypatch.setattr(llm_log, "SessionLocal", _Boom())
    monkeypatch.setattr(settings, "llm_daily_call_cap", 7)
    assert await llm_log.calls_today("failclosed-pet") >= 7

    # Attempts still count in-process while the DB is down, so recovery
    # never under-reports what was actually spent.
    await llm_log.record(
        llm_log.CallRecord(
            provider="p", model="m", prompt_hash="h",
            latency_ms=1, status="ok", pet_id="failclosed-pet",
        )
    )
    assert llm_log._attempts("failclosed-pet") == 1
