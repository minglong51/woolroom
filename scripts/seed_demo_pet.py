#!/usr/bin/env python3
"""Seed the read-only DEMO pet for guest mode.

Guests must never see a real household's pet — guest mode serves a dedicated
demo pet instead. This script creates it (idempotent, matched by name) and
prints its id so ops can paste it into GUEST_PET_ID.

Usage:
    python scripts/seed_demo_pet.py

Env:
    DEMO_PET_NAME   distinctive name to match/create (default "biscuit")
    DEMO_PET_COAT   one of tuxedo | marmalade | ash (default "marmalade")

Safe to run repeatedly: a second run prints the existing id and changes
nothing. The demo pet gets NO participants — it is nobody's pet.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure app/ is on the path when run as `python scripts/seed_demo_pet.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

COATS = {"tuxedo", "marmalade", "ash"}
# Pleasant, warm defaults — the demo pet should be a good first impression.
DEMO_QUIRKS = ["lean_in_greeter", "content_sigher"]


async def _main() -> int:
    from sqlalchemy import select

    from app.config import settings
    from app.storage import repo
    from app.storage.db import SessionLocal, engine
    from app.storage.models import Base, Pet

    name = os.environ.get("DEMO_PET_NAME", "biscuit").strip()[:64] or "biscuit"
    coat = os.environ.get("DEMO_PET_COAT", "marmalade").strip() or "marmalade"
    if coat not in COATS:
        print(f"error: DEMO_PET_COAT must be one of {sorted(COATS)}, got {coat!r}")
        return 2

    # Dev/test databases may predate the schema; prod is alembic-only and
    # always has tables by the time this runs (scripts/migrate.py first).
    if not settings.is_prod:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    try:
        async with SessionLocal() as session:
            existing = (
                await session.execute(
                    select(Pet).where(
                        Pet.name == name,
                        Pet.is_demo.is_(True),
                    )
                )
            ).scalars().first()
            if existing is not None:
                print(
                    f"demo pet already exists: id={existing.id} "
                    f"name={existing.name!r} coat={existing.coat} (nothing changed)"
                )
                print(existing.id)
                return 0

            pet = await repo.create_pet(session, name, DEMO_QUIRKS, coat=coat)
            pet.is_demo = True
            # Deliberately no participants, no core facts, no buffer events —
            # it's a demo pet, not anyone's pet.
            await session.commit()
            print(f"created demo pet: id={pet.id} name={pet.name!r} coat={pet.coat}")
            print(pet.id)
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
