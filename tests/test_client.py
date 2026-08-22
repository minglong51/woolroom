"""Tests for the LLM provider switch in app.runtime.client."""

from __future__ import annotations

import importlib
import json
import sys

import httpx
import pytest


def _reload_app_with_env(monkeypatch, env: dict[str, str]) -> object:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    return importlib.import_module("app.runtime.client")


@pytest.mark.asyncio
async def test_ollama_provider_calls_openai_compat_endpoint_and_parses_response(monkeypatch):
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode("utf-8")) if request.content else None
        return httpx.Response(
            200,
            json={
                "id": "x",
                "choices": [
                    {"message": {"role": "assistant", "content": "*tail thumps once*"}, "finish_reason": "stop"}
                ],
            },
        )

    transport = httpx.MockTransport(_handler)
    client = _reload_app_with_env(
        monkeypatch,
        {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://ollama.example:11434",
            "OLLAMA_MODEL": "qwen-test",
        },
    )

    real_async_client = httpx.AsyncClient

    def _patched_async_client(**kwargs):
        kwargs["transport"] = transport
        return real_async_client(**kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _patched_async_client)

    text = await client.complete("system", "user msg")
    assert text == "*tail thumps once*"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["url"].startswith("http://ollama.example:11434")
    assert captured["json"]["model"] == "qwen-test"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["content"] == "user msg"
    assert captured["json"]["stream"] is False


@pytest.mark.asyncio
async def test_ollama_provider_returns_none_on_5xx(monkeypatch):
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "model loading"})

    transport = httpx.MockTransport(_handler)
    client = _reload_app_with_env(
        monkeypatch,
        {"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://x:1", "OLLAMA_MODEL": "m"},
    )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )

    assert await client.complete("s", "u") is None


@pytest.mark.asyncio
async def test_anthropic_provider_returns_none_when_key_missing(monkeypatch):
    """Sanity: if you pick anthropic without a key, complete() returns None
    so the body-language fallback path runs."""
    client = _reload_app_with_env(monkeypatch, {"LLM_PROVIDER": "anthropic"})
    assert await client.complete("s", "u") is None
