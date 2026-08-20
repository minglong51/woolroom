from datetime import datetime, timedelta

from app.engine.mood import MoodState, drift, nudge, pick_animation
from app.time import utc_now

def test_pick_animation():
    assert pick_animation(10, 50) == "sleeping"
    assert pick_animation(40, 50) == "sitting"
    assert pick_animation(60, 70) == "playful"
    assert pick_animation(60, 40) == "alert"

def test_nudge():
    start = utc_now()
    state = MoodState(arousal=50, valence=50, animation_state="sitting", last_drift_at=start)
    new_state = nudge(state, arousal_delta=10, valence_delta=-10)
    assert new_state.arousal == 60
    assert new_state.valence == 40
    assert new_state.animation_state == "alert"

def test_drift_arousal_pulls_to_diurnal():
    # 3 AM should be low arousal
    night = datetime(2026, 4, 21, 3, 0)
    state = MoodState(arousal=80, valence=50, animation_state="alert", last_drift_at=night - timedelta(hours=3))
    new_state = drift(state, night)
    # It should have drifted down from 80
    assert new_state.arousal < 80

    # 11 AM should be high arousal
    noon = datetime(2026, 4, 21, 11, 0)
    state = MoodState(arousal=20, valence=50, animation_state="sleeping", last_drift_at=noon - timedelta(hours=3))
    new_state = drift(state, noon)
    # It should have drifted up from 20
    assert new_state.arousal > 20
