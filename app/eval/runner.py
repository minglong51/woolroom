"""Eval runner: corpus → prompt → LLM → validator → eval_runs row.

`run_session` executes every case in the corpus, persists one row per case
to `eval_runs` tagged with a shared `eval_session` id, and returns a
summary (accept count, reject count, error count, mean latency).

`diff_sessions` compares two sessions case-by-case: which cases changed
verdict, which got faster / slower, and a short text diff of response
excerpts.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from app.config import settings
from app.eval.corpus import Case, PetSpec
from app.runtime import client, llm_log, prompt, validator
from app.storage.db import SessionLocal
from app.storage.models import EvalRun


# Minimal Pet-shaped duck object for the prompt builder. Avoids constructing
# real SQLAlchemy rows for the eval (which would require sessions + commits
# and could pollute the prod DB).
class _FakePet:
    def __init__(self, spec: PetSpec) -> None:
        self.id = f"eval-{spec.name}"
        self.name = spec.name
        self.adopted_at = spec.adopted_at
        self.temperament = spec.temperament
        self.quirks = spec.quirks
        self.mood_arousal = spec.mood_arousal
        self.mood_valence = spec.mood_valence
        self.animation_state = spec.animation_state


@dataclass
class CaseResult:
    case_id: str
    status: str
    validator_verdict: str
    latency_ms: int
    response_excerpt: str | None
    model: str
    provider: str
    prompt_hash: str


@dataclass
class SessionSummary:
    eval_session: str
    label: str | None
    n_cases: int
    accepted: int
    rejected: int
    errors: int
    mean_latency_ms: float


async def run_session(
    cases: Iterable[Case],
    *,
    label: str | None = None,
    llm_client: object | None = None,
) -> tuple[SessionSummary, list[CaseResult]]:
    """Run every case. Records to eval_runs. Returns (summary, per-case rows)."""
    eval_session = secrets.token_urlsafe(12)
    case_list = list(cases)
    results: list[CaseResult] = []

    for case in case_list:
        result = await _run_case(case, eval_session=eval_session, label=label, llm_client=llm_client)
        results.append(result)

    accepted = sum(1 for r in results if r.validator_verdict == "accepted")
    rejected = sum(1 for r in results if r.validator_verdict == "rejected")
    errors = sum(1 for r in results if r.status not in ("ok",))
    mean_latency = (sum(r.latency_ms for r in results) / len(results)) if results else 0.0

    return (
        SessionSummary(
            eval_session=eval_session,
            label=label,
            n_cases=len(case_list),
            accepted=accepted,
            rejected=rejected,
            errors=errors,
            mean_latency_ms=mean_latency,
        ),
        results,
    )


async def _run_case(
    case: Case,
    *,
    eval_session: str,
    label: str | None,
    llm_client: object | None,
) -> CaseResult:
    pet = _FakePet(case.pet)
    ctx = prompt.Context(
        pet=pet,  # type: ignore[arg-type]
        mood_arousal=case.pet.mood_arousal,
        mood_valence=case.pet.mood_valence,
        animation_state=case.pet.animation_state,
        recent_events=[],
        recent_moments=[],
        user_action=case.action,
        user_text=case.text,
        response_mode=case.response_mode,
        response_guidance=case.response_guidance or _default_guidance(case.response_mode),
        callback_moment=None,
    )
    system = prompt.build_system_prompt(pet)  # type: ignore[arg-type]
    user_msg = prompt.build_user_message(ctx)
    prompt_hash = llm_log.hash_prompt(system, user_msg)

    import time

    start = time.perf_counter()

    if llm_client is not None:
        # Test-mode override (e.g. a deterministic stub).
        text = llm_client.complete(system, user_msg)  # type: ignore[attr-defined]
        status = "ok" if text else "empty"
        error_class = None
    else:
        # Real client. Uses production routing (anthropic / ollama per settings).
        text = await client.complete(system, user_msg)
        status = "ok" if text else "empty"
        error_class = None

    latency_ms = int((time.perf_counter() - start) * 1000)

    verdict = "n/a"
    excerpt: str | None = None
    if text:
        cleaned = validator.clean(text.splitlines()[0])
        verdict = "accepted" if validator.validate(cleaned) else "rejected"
        excerpt = (text[:540])

    provider = "anthropic" if settings.llm_provider != "ollama" else "ollama"
    model = settings.llm_model if provider == "anthropic" else settings.ollama_model

    # Persist.
    async with SessionLocal() as session:  # type: AsyncSession
        session.add(
            EvalRun(
                eval_session=eval_session,
                label=label,
                case_id=case.id,
                provider=provider,
                model=model,
                prompt_version=llm_log.PROMPT_VERSION,
                prompt_hash=prompt_hash,
                latency_ms=latency_ms,
                status=status,
                validator_verdict=verdict,
                error_class=error_class,
                response_excerpt=excerpt,
            )
        )
        await session.commit()

    return CaseResult(
        case_id=case.id,
        status=status,
        validator_verdict=verdict,
        latency_ms=latency_ms,
        response_excerpt=excerpt,
        model=model,
        provider=provider,
        prompt_hash=prompt_hash,
    )


def _default_guidance(mode: str) -> str:
    if mode == "body_language":
        return "Prefer body language only. Do not speak unless there is an unusually strong reason."
    if mode == "callback":
        return "If you speak, make it a small sideways callback to the memory. Do not narrate or explain it."
    if mode == "utterance":
        return "If you speak, keep it brief and slightly oblique. Do not answer the human's message directly."
    return ""


# ─────────── diff ───────────


@dataclass
class CaseDelta:
    case_id: str
    before_verdict: str
    after_verdict: str
    before_latency_ms: int
    after_latency_ms: int
    before_excerpt: str | None
    after_excerpt: str | None
    changed: bool


async def diff_sessions(session_a: str, session_b: str) -> list[CaseDelta]:
    """Return per-case delta between two sessions."""
    async with SessionLocal() as session:
        rows_a = (
            await session.execute(
                select(EvalRun).where(EvalRun.eval_session == session_a)
            )
        ).scalars().all()
        rows_b = (
            await session.execute(
                select(EvalRun).where(EvalRun.eval_session == session_b)
            )
        ).scalars().all()

    a_by_case = {r.case_id: r for r in rows_a}
    b_by_case = {r.case_id: r for r in rows_b}
    all_case_ids = sorted(set(a_by_case) | set(b_by_case))

    deltas: list[CaseDelta] = []
    for cid in all_case_ids:
        a = a_by_case.get(cid)
        b = b_by_case.get(cid)
        deltas.append(
            CaseDelta(
                case_id=cid,
                before_verdict=a.validator_verdict if a else "(missing)",
                after_verdict=b.validator_verdict if b else "(missing)",
                before_latency_ms=a.latency_ms if a else 0,
                after_latency_ms=b.latency_ms if b else 0,
                before_excerpt=a.response_excerpt if a else None,
                after_excerpt=b.response_excerpt if b else None,
                changed=(
                    (not a or not b)
                    or a.validator_verdict != b.validator_verdict
                    or (a.response_excerpt or "") != (b.response_excerpt or "")
                ),
            )
        )
    return deltas


def run_session_sync(cases: Iterable[Case], **kwargs) -> tuple[SessionSummary, list[CaseResult]]:
    """Sync wrapper for CLI use."""
    return asyncio.run(run_session(cases, **kwargs))


def diff_sessions_sync(session_a: str, session_b: str) -> list[CaseDelta]:
    return asyncio.run(diff_sessions(session_a, session_b))
