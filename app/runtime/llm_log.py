"""Persistent LLM-call log.

Records one row in `llm_calls` per LLM invocation: provider, model, prompt
version + hash, latency, status, validator verdict (when known), short
response excerpt. Lets the eval harness (scripts/eval.py, and any future
tooling) ask "how often does our prompt produce something the validator
accepts?" without re-running anything.

Best-effort: record() never raises. A failing observability table must
never break a request.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import insert

from app.storage.db import SessionLocal
from app.storage.models import LLMCall

# Bump on every meaningful prompt or validator change. The hash captures the
# exact (system, user) bytes that hit the model; the version captures
# semantic generations of the prompt builder.
PROMPT_VERSION = "v1-2026-05-17"

log = logging.getLogger(__name__)


@dataclass
class CallRecord:
    provider: str
    model: str
    prompt_hash: str
    latency_ms: int
    status: str  # ok | timeout | error | empty
    error_class: str | None = None
    response_excerpt: str | None = None
    validator_verdict: str = "n/a"
    pet_id: str | None = None


def hash_prompt(system_prompt: str, user_msg: str) -> str:
    """16 hex chars of sha256(system + \\n + user). Short enough to inspect by
    eye, long enough to dedupe identical calls."""
    h = hashlib.sha256()
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\n")
    h.update(user_msg.encode("utf-8"))
    return h.hexdigest()[:16]


async def record(rec: CallRecord) -> None:
    try:
        async with SessionLocal() as session:
            await session.execute(
                insert(LLMCall).values(
                    pet_id=rec.pet_id,
                    provider=rec.provider,
                    model=rec.model,
                    prompt_version=PROMPT_VERSION,
                    prompt_hash=rec.prompt_hash,
                    latency_ms=rec.latency_ms,
                    status=rec.status,
                    validator_verdict=rec.validator_verdict,
                    error_class=rec.error_class,
                    response_excerpt=rec.response_excerpt,
                )
            )
            await session.commit()
    except Exception:
        log.exception("llm_log: failed to record call (continuing)")


@asynccontextmanager
async def measure(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_msg: str,
    pet_id: str | None = None,
):
    """Yields a mutable CallRecord. Caller sets `status`, optionally
    `response_excerpt` and `error_class`; the context manager fills latency
    and records on exit. Validator verdict is updated post-hoc via update_verdict.
    """
    import time

    rec = CallRecord(
        provider=provider,
        model=model,
        prompt_hash=hash_prompt(system_prompt, user_msg),
        latency_ms=0,
        status="error",
        pet_id=pet_id,
    )
    start = time.perf_counter()
    try:
        yield rec
    finally:
        rec.latency_ms = int((time.perf_counter() - start) * 1000)
        await record(rec)


async def calls_today(pet_id: str | None) -> int:
    """LLM calls recorded for this pet since UTC midnight. Powers the daily
    budget circuit-breaker in respond(). Best-effort: fails open (returns 0) —
    observability must never break a request."""
    try:
        from sqlalchemy import func, select

        async with SessionLocal() as session:
            return (
                await session.execute(
                    select(func.count(LLMCall.id))
                    .where(LLMCall.pet_id == pet_id)
                    .where(func.date(LLMCall.ts) == func.date("now"))
                )
            ).scalar_one()
    except Exception:
        log.exception("llm_log: failed to count today's calls (failing open)")
        return 0


async def update_verdict(prompt_hash: str, verdict: str) -> None:
    """Set validator_verdict on the most recent llm_calls row with this hash.
    Called from respond.py after the validator decides. Best-effort: silently
    skips if no matching row (e.g., the recorder failed earlier)."""
    try:
        from sqlalchemy import select, update

        async with SessionLocal() as session:
            most_recent = (
                await session.execute(
                    select(LLMCall.id)
                    .where(LLMCall.prompt_hash == prompt_hash)
                    .order_by(LLMCall.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if most_recent is None:
                return
            await session.execute(
                update(LLMCall).where(LLMCall.id == most_recent).values(validator_verdict=verdict)
            )
            await session.commit()
    except Exception:
        log.exception("llm_log: failed to update verdict (continuing)")
