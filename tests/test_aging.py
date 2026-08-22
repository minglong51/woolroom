from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.aging import pet_age_years, life_stage, render_scale, stage_proportions
from app.time import utc_now


def _ago(days: int) -> datetime:
    return utc_now() - timedelta(days=days)


def test_pet_age_years_is_zero_at_adoption() -> None:
    assert pet_age_years(_ago(0)) < 0.01


def test_pet_age_years_compresses_one_month_to_one_year() -> None:
    assert abs(pet_age_years(_ago(30)) - 1.0) < 0.05
    assert abs(pet_age_years(_ago(90)) - 3.0) < 0.1


def test_life_stage_thresholds() -> None:
    assert life_stage(_ago(0)) == "kitten"
    assert life_stage(_ago(60)) == "kitten"     # ~2 pet years
    assert life_stage(_ago(120)) == "young"     # ~4 pet years
    assert life_stage(_ago(180)) == "young"     # ~6 pet years
    assert life_stage(_ago(270)) == "adult"     # ~9 pet years
    assert life_stage(_ago(400)) == "senior"    # ~13 pet years


def test_render_scale_per_stage() -> None:
    assert render_scale("kitten") < render_scale("young") < render_scale("adult")
    assert render_scale("senior") < render_scale("adult")


def test_aging_handles_missing_adopted_at() -> None:
    assert pet_age_years(None) == 0.0
    assert life_stage(None) == "kitten"


def test_stage_proportions_capture_silhouette_intent() -> None:
    kitten = stage_proportions("kitten")
    adult = stage_proportions("adult")
    senior = stage_proportions("senior")

    # Kitten has a relatively bigger head than the smaller body — that's the
    # whole point of kitten proportions.
    assert kitten["headScale"] > adult["headScale"]
    assert kitten["bodyScaleX"] < adult["bodyScaleX"]
    assert kitten["tailScale"] < adult["tailScale"]

    # Senior carries head and ears lower (positive offsetY = downward shift).
    assert senior["headOffsetY"] > adult["headOffsetY"]
    assert senior["earOffsetY"] > adult["earOffsetY"]


def test_stage_proportions_unknown_stage_falls_back_to_adult() -> None:
    weird = stage_proportions("middle-aged")
    adult = stage_proportions("adult")
    assert weird == adult
