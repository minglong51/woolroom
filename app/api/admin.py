"""Token-gated /admin/* operator routes: user/pet triage, merges, hard
deletes, recovery-link management, LLM telemetry. Separate from http.py so
the ops surface and the product surface read apart; auth is the
X-Admin-Token header against settings.admin_token (empty disables admin
entirely), throttled by main.py's auth-failure middleware."""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db
from app.api.http import _absolute_url
from app.config import settings
from app.storage import repo
from app.storage.models import User
from app.time import utc_now

router = APIRouter()

class RegenRecoveryIn(BaseModel):
    user_id: str | None = None
    display_name: str | None = None


def _require_admin(token_header: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="admin disabled")
    if not token_header or not _secrets.compare_digest(token_header, settings.admin_token):
        raise HTTPException(status_code=403, detail="bad admin token")


@router.get("/admin/users")
async def admin_list_users(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """List every user with whether they're a participant, what pet, and when
    they were last seen. For triage when display_name lookup is ambiguous
    (e.g. a test user and a real user sharing one display name)."""
    _require_admin(x_admin_token)
    from app.storage.models import PetParticipant
    q = (
        _sa_select(User, PetParticipant.pet_id)
        .outerjoin(PetParticipant, PetParticipant.user_id == User.id)
        .order_by(User.created_at.asc(), User.id.asc())
    )
    rows = (await session.execute(q)).all()
    results: list[dict] = []
    for user, pet_id in rows:
        pet = await repo.get_pet(session, pet_id) if pet_id else None
        results.append({
            "user_id": user.id,
            "display_name": user.display_name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
            "is_participant": pet is not None,
            "pet_id": pet.id if pet else None,
            "pet_name": pet.name if pet else None,
        })
    return {"users": results}


@router.delete("/admin/user/{user_id}")
async def admin_delete_user(
    user_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """Hard-delete a user. Cascades by hand since the schema doesn't declare
    ON DELETE CASCADE. PetParticipant rows are deleted (orphan participant
    rows would dangle). BufferEvent + Outing user_id is nulled (we keep the
    events, just untie them from the deleted user). MagicLink rows for that
    user are deleted (no FK, plain string match on issued_for)."""
    _require_admin(x_admin_token)
    from sqlalchemy import delete, update
    from app.storage.models import BufferEvent, MagicLink, Outing, PetParticipant
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="no such user")
    await session.execute(delete(PetParticipant).where(PetParticipant.user_id == user_id))
    await session.execute(update(BufferEvent).where(BufferEvent.user_id == user_id).values(user_id=None))
    await session.execute(update(Outing).where(Outing.triggered_by_user_id == user_id).values(triggered_by_user_id=None))
    await session.execute(delete(MagicLink).where(MagicLink.issued_for == user_id))
    await session.delete(user)
    await session.commit()
    return {"deleted_user_id": user_id, "display_name": user.display_name}


class MergePetIn(BaseModel):
    source_pet_id: str
    target_pet_id: str


@router.post("/admin/merge-pet")
async def admin_merge_pet(
    body: MergePetIn,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """Reparent everything from source_pet_id onto target_pet_id, then delete
    the source pet. Useful for "someone accidentally adopted a duplicate pet —
    fold it into the shared one." The (pet_id, user_id) PK is honored: if
    source's participant is already in target, source's row is dropped
    instead of moved."""
    _require_admin(x_admin_token)
    from sqlalchemy import delete, update
    from app.storage.models import (
        BufferEvent, CoreFact, MagicLink, Moment, Outing, Pet, PetParticipant,
    )
    if body.source_pet_id == body.target_pet_id:
        raise HTTPException(status_code=400, detail="source and target are the same")
    source = await session.get(Pet, body.source_pet_id)
    target = await session.get(Pet, body.target_pet_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="source or target pet not found")

    moved_events = 0
    moved_moments = 0
    moved_participants = 0
    dropped_participants = 0

    # Participants: per-user unique constraint means a user can be in only one
    # pet at a time. Use raw UPDATE (not ORM delete+insert) so the unique index
    # never sees both rows simultaneously.
    target_user_ids_subq = (
        _sa_select(PetParticipant.user_id)
        .where(PetParticipant.pet_id == body.target_pet_id)
        .scalar_subquery()
    )
    r = await session.execute(
        update(PetParticipant)
        .where(
            PetParticipant.pet_id == body.source_pet_id,
            PetParticipant.user_id.notin_(target_user_ids_subq),
        )
        .values(pet_id=body.target_pet_id)
    )
    moved_participants = int(r.rowcount or 0)
    r = await session.execute(
        delete(PetParticipant).where(PetParticipant.pet_id == body.source_pet_id)
    )
    dropped_participants = int(r.rowcount or 0)

    # Events + moments: reparent to target so the activity history follows.
    r = await session.execute(
        update(BufferEvent)
        .where(BufferEvent.pet_id == body.source_pet_id)
        .values(pet_id=body.target_pet_id)
    )
    moved_events = int(r.rowcount or 0)
    r = await session.execute(
        update(Moment)
        .where(Moment.pet_id == body.source_pet_id)
        .values(pet_id=body.target_pet_id)
    )
    moved_moments = int(r.rowcount or 0)

    # Core facts: key conflicts likely — drop source's facts to avoid overwriting
    # target's lovingly-curated adopted_by, etc.
    await session.execute(delete(CoreFact).where(CoreFact.pet_id == body.source_pet_id))
    # Outings + magic_links scoped to source: drop. Stale once source is gone.
    await session.execute(delete(Outing).where(Outing.pet_id == body.source_pet_id))
    await session.execute(delete(MagicLink).where(MagicLink.pet_id == body.source_pet_id))

    source_name = source.name
    await session.delete(source)
    await session.commit()
    return {
        "source_pet_id": body.source_pet_id,
        "source_name": source_name,
        "target_pet_id": body.target_pet_id,
        "target_name": target.name,
        "moved_participants": moved_participants,
        "dropped_participants": dropped_participants,
        "moved_events": moved_events,
        "moved_moments": moved_moments,
    }


@router.delete("/admin/pet/{pet_id}")
async def admin_delete_pet(
    pet_id: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """Hard-delete a pet. Cascades through every per-pet row (participants,
    events, moments, core_facts, outings, pet-scoped magic_links). The Pet
    itself goes last so foreign-key constraints don't fire mid-transaction."""
    _require_admin(x_admin_token)
    from sqlalchemy import delete
    from app.storage.models import (
        BufferEvent, CoreFact, MagicLink, Moment, Outing, Pet, PetParticipant,
    )
    pet = await session.get(Pet, pet_id)
    if not pet:
        raise HTTPException(status_code=404, detail="no such pet")
    await session.execute(delete(PetParticipant).where(PetParticipant.pet_id == pet_id))
    await session.execute(delete(BufferEvent).where(BufferEvent.pet_id == pet_id))
    await session.execute(delete(Moment).where(Moment.pet_id == pet_id))
    await session.execute(delete(CoreFact).where(CoreFact.pet_id == pet_id))
    await session.execute(delete(Outing).where(Outing.pet_id == pet_id))
    await session.execute(delete(MagicLink).where(MagicLink.pet_id == pet_id))
    pet_name = pet.name
    await session.delete(pet)
    await session.commit()
    return {"deleted_pet_id": pet_id, "name": pet_name}


@router.post("/admin/regenerate-recovery")
async def admin_regenerate_recovery(
    request: Request,
    body: RegenRecoveryIn,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """Mint a fresh recovery URL for one or more users. Use when a user has lost
    their cookie AND their saved recovery URL — the only way back to their
    original account otherwise is direct DB intervention.

    Auth: X-Admin-Token header must match settings.admin_token.
    Match: either user_id (single) or display_name (may match multiple).
    """
    _require_admin(x_admin_token)
    if body.user_id:
        existing = await session.get(User, body.user_id)
        users = [existing] if existing else []
    elif body.display_name:
        q = _sa_select(User).where(User.display_name == body.display_name)
        users = list((await session.execute(q)).scalars().all())
    else:
        raise HTTPException(status_code=400, detail="user_id or display_name required")
    if not users:
        raise HTTPException(status_code=404, detail="no matching user")

    results: list[dict] = []
    for u in users:
        # Additive — older URLs for this user stay valid. Use admin_revoke
        # for the rare "I think this leaked" case.
        link = await repo.mint_recovery_link(session, u.id)
        pet = await repo.get_pet_for_user(session, u.id)
        results.append({
            "user_id": u.id,
            "display_name": u.display_name,
            "pet_id": pet.id if pet else None,
            "pet_name": pet.name if pet else None,
            "is_participant": pet is not None,
            "recovery_url": _absolute_url(request, f"/r/{link.token}"),
        })
    await session.commit()
    return {"results": results}


@router.post("/admin/revoke-recovery")
async def admin_revoke_recovery(
    body: RegenRecoveryIn,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(db),
) -> dict:
    """Nuke every recovery token for a user. Use after a suspected leak.
    Pair with regenerate-recovery to issue a fresh URL afterward."""
    _require_admin(x_admin_token)
    if body.user_id:
        target_ids = [body.user_id]
    elif body.display_name:
        q = _sa_select(User.id).where(User.display_name == body.display_name)
        target_ids = list((await session.execute(q)).scalars().all())
    else:
        raise HTTPException(status_code=400, detail="user_id or display_name required")
    if not target_ids:
        raise HTTPException(status_code=404, detail="no matching user")
    from sqlalchemy import delete
    from app.storage.models import MagicLink as _ML
    total = 0
    for uid in target_ids:
        result = await session.execute(
            delete(_ML).where(_ML.issued_for == uid, _ML.purpose == "recovery")
        )
        total += int(result.rowcount or 0)
    await session.commit()
    return {"revoked_count": total, "user_ids": target_ids}


@router.get("/admin/llm/stats")
async def admin_llm_stats(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    hours: int = 24,
    session: AsyncSession = Depends(db),
) -> dict:
    """Aggregate LLM-call telemetry over the last N hours (default 24).

    Returns total calls, status distribution, validator acceptance rate,
    latency percentiles, and per-(provider, model, prompt_version) breakdown.
    Backs the "we measure our LLM stack" claim on the portfolio page.
    """
    _require_admin(x_admin_token)
    from datetime import timedelta
    from sqlalchemy import func as _f
    from app.storage.models import LLMCall

    cutoff = utc_now() - timedelta(hours=max(1, hours))

    total = (
        await session.execute(
            _sa_select(_f.count(LLMCall.id)).where(LLMCall.ts >= cutoff)
        )
    ).scalar_one()

    status_rows = (
        await session.execute(
            _sa_select(LLMCall.status, _f.count(LLMCall.id))
            .where(LLMCall.ts >= cutoff)
            .group_by(LLMCall.status)
        )
    ).all()

    verdict_rows = (
        await session.execute(
            _sa_select(LLMCall.validator_verdict, _f.count(LLMCall.id))
            .where(LLMCall.ts >= cutoff, LLMCall.status == "ok")
            .group_by(LLMCall.validator_verdict)
        )
    ).all()

    # Per-(provider, model, prompt_version) latency + status snapshot.
    breakdown_rows = (
        await session.execute(
            _sa_select(
                LLMCall.provider,
                LLMCall.model,
                LLMCall.prompt_version,
                _f.count(LLMCall.id),
                _f.avg(LLMCall.latency_ms),
                _f.max(LLMCall.latency_ms),
                _f.min(LLMCall.latency_ms),
            )
            .where(LLMCall.ts >= cutoff)
            .group_by(LLMCall.provider, LLMCall.model, LLMCall.prompt_version)
        )
    ).all()

    ok_latencies = (
        await session.execute(
            _sa_select(LLMCall.latency_ms)
            .where(LLMCall.ts >= cutoff, LLMCall.status == "ok")
            .order_by(LLMCall.latency_ms.asc())
        )
    ).scalars().all()

    def _pct(values: list[int], q: float) -> int | None:
        if not values:
            return None
        i = min(len(values) - 1, int(len(values) * q))
        return int(values[i])

    accepted = next((c for v, c in verdict_rows if v == "accepted"), 0)
    rejected = next((c for v, c in verdict_rows if v == "rejected"), 0)
    pending_verdict = next((c for v, c in verdict_rows if v == "n/a"), 0)

    return {
        "window_hours": hours,
        "total_calls": int(total),
        "status_counts": {s: int(c) for s, c in status_rows},
        "validator": {
            "accepted": int(accepted),
            "rejected": int(rejected),
            "pending_or_na": int(pending_verdict),
            "acceptance_rate": (
                round(accepted / (accepted + rejected), 4)
                if (accepted + rejected) > 0
                else None
            ),
        },
        "latency_ms": {
            "p50": _pct(list(ok_latencies), 0.50),
            "p95": _pct(list(ok_latencies), 0.95),
            "max": int(max(ok_latencies)) if ok_latencies else None,
            "n": len(ok_latencies),
        },
        "by_prompt_version": [
            {
                "provider": p,
                "model": m,
                "prompt_version": v,
                "count": int(c),
                "avg_latency_ms": int(avg) if avg is not None else None,
                "min_latency_ms": int(lo) if lo is not None else None,
                "max_latency_ms": int(hi) if hi is not None else None,
            }
            for p, m, v, c, avg, hi, lo in breakdown_rows
        ],
    }
