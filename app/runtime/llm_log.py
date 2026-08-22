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

from app.config import settings
from app.storage.db import SessionLocal
from app.storage.models import LLMCall
from app.time import utc_now

# In-process attempt counter, {pet_id: (utc_date_iso, count)}. The DB rows
# are best-effort observability; this counter is what keeps the daily budget
# cap honest when the DB write or read fails — the cap must never fail open
# on a metered key. Single-worker deployment makes process-local correct.
_attempts_today: dict[str | None, tuple[str, int]] = {}


def _bump_attempts(pet_id: str | None) -> None:
    today = utc_now().date().isoformat()
    day, count = _attempts_today.get(pet_id, (today, 0))
    _attempts_today[pet_id] = (today, (count if day == today else 0) + 1)


def _attempts(pet_id: str | None) -> int:
    day, count = _attempts_today.get(pet_id, ("", 0))
    return count if day == utc_now().date().isoformat() else 0

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
    _bump_attempts(rec.pet_id)
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
    """LLM attempts for this pet since UTC midnight. Powers the daily budget
    circuit-breaker in respond(). Fails CLOSED: if the DB count is
    unavailable, reports at least the cap so the respond path falls back to
    the phrasebook — indistinguishable to the user, and a metered key never
    becomes uncapped spend because an observability table hiccuped."""
    try:
        from sqlalchemy import func, select

        async with SessionLocal() as session:
            counted = (
                await session.execute(
                    select(func.count(LLMCall.id))
                    .where(LLMCall.pet_id == pet_id)
                    .where(func.date(LLMCall.ts) == func.date("now"))
                )
            ).scalar_one()
        return max(counted, _attempts(pet_id))
    except Exception:
        log.exception("llm_log: failed to count today's calls (failing closed)")
        return max(settings.llm_daily_call_cap, _attempts(pet_id))


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
