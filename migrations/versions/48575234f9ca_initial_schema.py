"""initial schema

Revision ID: 48575234f9ca
Revises:
Create Date: 2026-08-20 00:00:00.000000

The complete woolroom schema for the public v1: users, pets,
pet_participants, buffer_events, action_receipts, moments, core_facts,
outings, magic_links, llm_calls, eval_runs. This mirrors
app/storage/models.py exactly — the dev create_all path and this revision
emit the same DDL, and alembic is the only thing that touches schema in a
deployed environment (scripts/migrate.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48575234f9ca'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("partner_aliases", sa.JSON(), nullable=False),
        sa.Column("last_room_pet_id", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("adopted_at", sa.DateTime(), nullable=True),
        sa.Column("temperament", sa.JSON(), nullable=False),
        sa.Column("quirks", sa.JSON(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("species", sa.String(length=16), server_default="cat", nullable=False),
        sa.Column("household_id", sa.String(length=32), nullable=False),
        sa.Column("mood_arousal", sa.Integer(), nullable=False),
        sa.Column("mood_valence", sa.Integer(), nullable=False),
        sa.Column("animation_state", sa.String(length=16), nullable=False),
        sa.Column("coat", sa.String(length=16), server_default="red", nullable=False),
        sa.Column(
            "last_mood_drift_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("prompt_hash", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validator_verdict", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("response_excerpt", sa.String(length=280), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_pet_id", "llm_calls", ["pet_id"])
    op.create_index("ix_llm_calls_ts", "llm_calls", ["ts"])
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("eval_session", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("prompt_hash", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validator_verdict", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("response_excerpt", sa.String(length=560), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_runs_eval_session", "eval_runs", ["eval_session"])
    op.create_index("ix_eval_runs_ts", "eval_runs", ["ts"])
    op.create_index("ix_eval_runs_case_id", "eval_runs", ["case_id"])
    op.create_table(
        "pet_participants",
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_adoption_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pet_id", "user_id"),
    )
    op.create_table(
        "buffer_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_buffer_events_pet_id", "buffer_events", ["pet_id"])
    op.create_index("ix_buffer_events_created_at", "buffer_events", ["created_at"])
    op.create_table(
        "action_receipts",
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("origin_id", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pet_id", "user_id", "origin_id"),
    )
    op.create_table(
        "moments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("fragment", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_moments_created_at", "moments", ["created_at"])
    op.create_index("ix_moments_pet_id", "moments", ["pet_id"])
    op.create_table(
        "core_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_core_facts_pet_id", "core_facts", ["pet_id"])
    op.create_table(
        "outings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("story", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("triggered_by_user_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outings_pet_id", "outings", ["pet_id"])
    op.create_table(
        "magic_links",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("issued_for", sa.String(length=255), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("pet_id", sa.String(length=32), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pet_id"], ["pets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_magic_links_token", "magic_links", ["token"], unique=True)


def downgrade() -> None:
    op.drop_table("magic_links")
    op.drop_table("outings")
    op.drop_table("core_facts")
    op.drop_table("moments")
    op.drop_table("action_receipts")
    op.drop_table("buffer_events")
    op.drop_table("pet_participants")
    op.drop_table("eval_runs")
    op.drop_table("llm_calls")
    op.drop_table("pets")
    op.drop_table("users")
