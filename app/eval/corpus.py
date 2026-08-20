"""Corpus loader for the eval harness.

YAML schema (per case):

```yaml
- id: pet-while-sleepy
  description: pet action while the cat is in low-arousal sleeping state
  pet:
    name: Purl
    breed_archetype: window cat                    # optional, default = "window cat"
    description: ...                          # optional
    quirks: [threshold_refuser, one_eye_napper]
    mood_arousal: 25
    mood_valence: 60
    animation_state: sleeping
    adopted_at: "2026-04-01T00:00:00"        # optional ISO; default ~30 days ago
  action: pet                                 # one of pet|feed|walk|greet|call|message
  text: null                                  # optional user_text for `message` action
  response_mode: body_language                # one of body_language|utterance|callback
  response_guidance: ""                       # optional override
```

Cases are pure data; the runner constructs a fake-in-memory Pet object
from them so no DB rows are written for the eval (and the eval is safe
to run against prod data without writing fixtures).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

# PyYAML is in the existing dependency tree (uvicorn pulls it transitively
# via watchfiles; if not, our own pyproject.toml lists it for tests).
import yaml


@dataclass(frozen=True)
class PetSpec:
    """The fields the prompt builder reads off a Pet. Kept narrow so the
    eval doesn't need to mock SQLAlchemy."""

    name: str
    quirks: list[str]
    mood_arousal: int
    mood_valence: int
    animation_state: str
    adopted_at: datetime
    temperament: dict[str, Any]


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    pet: PetSpec
    action: str
    text: str | None
    response_mode: str
    response_guidance: str


def load_corpus(path: str | Path) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"corpus root must be a list, got {type(raw).__name__}")
    return [_parse_case(entry) for entry in raw]


def _parse_case(entry: dict) -> Case:
    pet_raw = entry.get("pet") or {}
    adopted_at_str = pet_raw.get("adopted_at")
    if adopted_at_str:
        adopted_at = datetime.fromisoformat(adopted_at_str)
    else:
        adopted_at = datetime.utcnow() - timedelta(days=30)

    temperament: dict[str, Any] = {
        "breed_archetype": pet_raw.get("breed_archetype", "window cat"),
        "description": pet_raw.get(
            "description",
            "A self-possessed window cat. Calm but watchful, affectionate on its own schedule.",
        ),
    }

    pet = PetSpec(
        name=pet_raw.get("name", "Purl"),
        quirks=list(pet_raw.get("quirks", []) or []),
        mood_arousal=int(pet_raw.get("mood_arousal", 50)),
        mood_valence=int(pet_raw.get("mood_valence", 60)),
        animation_state=pet_raw.get("animation_state", "idle"),
        adopted_at=adopted_at,
        temperament=temperament,
    )

    return Case(
        id=str(entry["id"]),
        description=str(entry.get("description", "")),
        pet=pet,
        action=str(entry["action"]),
        text=entry.get("text"),
        response_mode=str(entry.get("response_mode", "body_language")),
        response_guidance=str(entry.get("response_guidance", "")),
    )


def case_ids(cases: Iterable[Case]) -> list[str]:
    return [c.id for c in cases]
