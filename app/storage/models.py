from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Observer-side partner-name overrides: {other_user_id_or_display_name: alias}.
    # Renders use this map when displaying partner names to *this* viewer.
    partner_aliases: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    # Which household room this human was last standing in (a pet id). Boot
    # lands there; null = the founding pet's room. Cross-device by design —
    # the room you left is the room you return to, phone or laptop.
    last_room_pet_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    temperament: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quirks: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    # cat (default) | pack-registered species. Identity: which silhouette and
    # voice the room renders. Every future pet kind rides the same household
    # plumbing.
    species: Mapped[str] = mapped_column(
        String(16), default="cat", server_default="cat"
    )
    # The pair's household: pets that live "next door" to each other share one
    # household id (the founding pet's own id). A second pet is adopted INTO
    # the household, never floated free.
    household_id: Mapped[str] = mapped_column(String(32))

    mood_arousal: Mapped[int] = mapped_column(Integer, default=40)
    mood_valence: Mapped[int] = mapped_column(Integer, default=60)
    animation_state: Mapped[str] = mapped_column(String(16), default="sleeping")
    # Coat id — the API layer validates it against the species registry (the
    # builtin cat: tuxedo | marmalade | ash). Identity, not dress-up: a
    # recolor of the same silhouette. The column default is a legacy value;
    # every creation path passes an explicit coat.
    coat: Mapped[str] = mapped_column(String(16), default="red", server_default="red")

    last_mood_drift_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PetParticipant(Base):
    __tablename__ = "pet_participants"
    # (pet_id, user_id) is the PK. A user may participate in SEVERAL pets of
    # one household (the founding cat and the second cat share their two
    # humans), but never in
    # pets of two households — that rule is enforced in repo.add_participant,
    # since it needs a household join SQLite can't express as a constraint.

    pet_id: Mapped[str] = mapped_column(
        ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    confirmed_adoption_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BufferEvent(Base):
    __tablename__ = "buffer_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    # When the OTHER participant saw this event. Two-person rooms make this a
    # single column: the author has seen their own event by definition. Only
    # meaningful for events that carry human content (message); stays NULL
    # forever on ambient action events.
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionReceipt(Base):
    __tablename__ = "action_receipts"

    pet_id: Mapped[str] = mapped_column(
        ForeignKey("pets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    origin_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    fragment: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    source_event_ids: Mapped[list[int]] = mapped_column(JSON, default=list)


class CoreFact(Base):
    __tablename__ = "core_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Outing(Base):
    __tablename__ = "outings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    story: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    triggered_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    issued_for: Mapped[str] = mapped_column(String(255))
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    pet_id: Mapped[str | None] = mapped_column(
        ForeignKey("pets.id", ondelete="CASCADE"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(16), default="login")  # login | invite | recovery
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class LLMCall(Base):
    """One row per LLM call. Lets us measure latency, validator-rejection rate,
    fallback rate per provider/model/prompt-version. Without this the only
    LLM observability the app has is a swallowed log.warning on exceptions."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    pet_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(16))  # anthropic | ollama
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))  # bumped manually on prompt edits
    prompt_hash: Mapped[str] = mapped_column(String(16))  # first 16 hex chars of sha256(system + "\n" + user)
    latency_ms: Mapped[int] = mapped_column(Integer)
    # ok | timeout | error | empty (= LLM returned None / empty string)
    status: Mapped[str] = mapped_column(String(16))
    # accepted | rejected | n/a (n/a when status != ok)
    validator_verdict: Mapped[str] = mapped_column(String(16), default="n/a")
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(String(280), nullable=True)


class EvalRun(Base):
    """One row per (corpus case, eval session). Powers the eval-harness diff:
    run the same corpus twice (e.g. before/after a prompt edit), then ask
    "which cases changed verdict?" — without re-running production traffic.

    Distinct from llm_calls (which captures real user-driven calls) so eval
    activity doesn't pollute the production stats.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    eval_session: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_hash: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    validator_verdict: Mapped[str] = mapped_column(String(16), default="n/a")
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(String(560), nullable=True)
