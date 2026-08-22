from types import SimpleNamespace

import pytest

from app.data.body_language import ACTION_LANGUAGE
from app.runtime.respond import respond


def _pet(*, arousal: int = 55, valence: int = 60):
    return SimpleNamespace(
        id="pet-1",
        name="Purl",
        temperament={"ignore_rate": 0.0, "breed_archetype": "window cat", "description": "quiet"},
        quirks=[],
        mood_arousal=arousal,
        mood_valence=valence,
        animation_state="sitting",
    )


@pytest.mark.asyncio
async def test_greet_prefers_body_language_without_llm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("llm should not be called for greet")

    monkeypatch.setattr(respond.__globals__["client"], "complete", _boom)

    res = await respond(
        _pet(),
        "greet",
        None,
        recent_events=[],
        recent_moments=[],
        core_facts={},
    )

    assert res.is_utterance is False
    assert res.text.startswith("*")


@pytest.mark.asyncio
async def test_play_prefers_body_language_without_llm(monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("llm should not be called for play")

    monkeypatch.setattr(respond.__globals__["client"], "complete", _boom)

    res = await respond(
        _pet(),
        "play",
        None,
        recent_events=[],
        recent_moments=[],
        core_facts={},
    )

    assert res.is_utterance is False
    assert res.text.startswith("*")
    assert res.text in {
        line
        for lines in ACTION_LANGUAGE["play"].values()
        for line in lines
    }


@pytest.mark.asyncio
async def test_message_can_use_llm_for_short_reply(monkeypatch):
    async def _ok(*args, **kwargs):
        return "mm. still here."

    monkeypatch.setattr(respond.__globals__["client"], "complete", _ok)

    res = await respond(
        _pet(arousal=58, valence=66),
        "message",
        "hi cat",
        recent_events=[],
        recent_moments=[],
        core_facts={},
    )

    assert res.is_utterance is True
    assert res.text == "mm. still here."


def test_prompt_context_includes_callback_guidance():
    from app.runtime.prompt import Context, build_user_message

    msg = build_user_message(
        Context(
            pet=_pet(),
            mood_arousal=55,
            mood_valence=64,
            animation_state="sitting",
            recent_events=[],
            recent_moments=[],
            user_action="message",
            user_text="hello",
            response_mode="callback",
            response_guidance="Make it oblique.",
            callback_moment="the walk we took — I remember the air",
        )
    )

    assert "Response mode: callback." in msg
    assert "Callback memory: the walk we took — I remember the air." in msg
