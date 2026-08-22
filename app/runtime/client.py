"""LLM client. Two providers: Anthropic (cloud Haiku) and Ollama (self-hosted).
Both expose the same `complete(system_prompt, user_msg)` surface.

Anthropic path uses prompt caching so the per-pet system block stays warm.
Ollama path uses Ollama's OpenAI-compatible /v1/chat/completions endpoint —
no caching, but local inference makes that irrelevant.

Every call is recorded to the `llm_calls` table via app.runtime.llm_log so we
can measure latency, error rates, and (later, post-validator) the
accept/reject ratio.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from anthropic import AsyncAnthropic

from app.config import settings
from app.runtime import llm_log

log = logging.getLogger(__name__)

_anthropic_client: AsyncAnthropic | None = None


def _get_anthropic_client() -> AsyncAnthropic | None:
    global _anthropic_client
    if not settings.anthropic_api_key:
        return None
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


async def _complete_anthropic(
    system_prompt: str,
    user_msg: str,
    timeout_s: float,
    pet_id: str | None,
) -> str | None:
    client = _get_anthropic_client()
    if client is None:
        return None
    async with llm_log.measure(
        provider="anthropic",
        model=settings.llm_model,
        system_prompt=system_prompt,
        user_msg=user_msg,
        pet_id=pet_id,
    ) as rec:
        try:
            resp = await asyncio.wait_for(
                client.messages.create(
                    model=settings.llm_model,
                    max_tokens=settings.llm_max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_msg}],
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning("anthropic timeout")
            rec.status = "timeout"
            rec.error_class = "TimeoutError"
            return None
        except Exception as exc:
            log.warning("anthropic error: %s", exc)
            rec.status = "error"
            rec.error_class = type(exc).__name__
            return None

        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        text = "".join(parts).strip()
        if not text:
            rec.status = "empty"
            return None
        rec.status = "ok"
        rec.response_excerpt = text[:280]
        return text


async def _complete_ollama(
    system_prompt: str,
    user_msg: str,
    timeout_s: float,
    pet_id: str | None,
) -> str | None:
    """Ollama via OpenAI-compatible /v1/chat/completions. No prompt caching —
    the local box is free, so re-tokenizing every turn is fine."""
    base = settings.ollama_base_url.rstrip("/")
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": settings.llm_max_tokens,
        "temperature": 0.85,
        "stream": False,
    }
    async with llm_log.measure(
        provider="ollama",
        model=settings.ollama_model,
        system_prompt=system_prompt,
        user_msg=user_msg,
        pet_id=pet_id,
    ) as rec:
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(f"{base}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.TimeoutException, asyncio.TimeoutError):
            log.warning("ollama timeout")
            rec.status = "timeout"
            rec.error_class = "TimeoutError"
            return None
        except Exception as exc:
            log.warning("ollama error: %s", exc)
            rec.status = "error"
            rec.error_class = type(exc).__name__
            return None

        choices = data.get("choices") or []
        if not choices:
            rec.status = "empty"
            return None
        msg = choices[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            rec.status = "empty"
            return None
        rec.status = "ok"
        rec.response_excerpt = text[:280]
        return text


async def complete(
    system_prompt: str,
    user_msg: str,
    timeout_s: float | None = None,
    pet_id: str | None = None,
) -> str | None:
    """One-shot completion. Returns trimmed text or None on failure/unavailable.

    pet_id is best-effort context for the call log; not required.
    """
    timeout = timeout_s if timeout_s is not None else settings.llm_timeout_s
    provider = settings.llm_provider.strip().casefold()
    if provider == "ollama":
        return await _complete_ollama(system_prompt, user_msg, timeout, pet_id)
    if provider == "anthropic":
        if not settings.anthropic_api_key.strip():
            return None
        return await _complete_anthropic(system_prompt, user_msg, timeout, pet_id)
    return None
