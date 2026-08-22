from types import SimpleNamespace

from app.engine.outings import generate_outing_story


def _pet(*, pet_id: str = "pet-1", quirks: list[str] | None = None):
    return SimpleNamespace(id=pet_id, quirks=quirks or [])


def test_outing_story_is_deterministic_for_same_pet_and_day():
    pet = _pet(quirks=["hides_small_things", "lean_in_greeter"])

    first = generate_outing_story(pet, "2026-04-23")
    second = generate_outing_story(pet, "2026-04-23")

    assert first == second


def test_outing_story_varies_across_days():
    pet = _pet(quirks=["hides_small_things", "lean_in_greeter"])

    first = generate_outing_story(pet, "2026-04-23")
    second = generate_outing_story(pet, "2026-04-24")

    assert first != second


def test_outing_story_includes_quirk_flavor():
    pet = _pet(quirks=["zoomie_initiator", "side_eye_judge"])

    story = generate_outing_story(pet, "2026-04-25")

    assert any(
        phrase in story
        for phrase in (
            "side-eyed",
            "accused",
            "sideways sprint",
            "moving much faster",
        )
    )
