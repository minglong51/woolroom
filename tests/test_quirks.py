from app.engine.mood import MoodState
from app.engine.quirks import (
    get_action_quirk_effect,
    get_pose_detail,
    get_quirk_events,
    get_scheduler_quirk_effect,
)
from app.time import utc_now

def test_content_sigher_triggers_on_sleep():
    now = utc_now()
    old = MoodState(arousal=40, valence=60, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=20, valence=60, animation_state="sleeping", last_drift_at=now)
    
    # Without quirk
    events = get_quirk_events(old, new, [])
    assert len(events) == 0
    
    # With quirk
    events = get_quirk_events(old, new, ["content_sigher"])
    assert len(events) == 1
    assert events[0].type == "response"
    assert events[0].data["text"] == "*sinks into the nap with one long, audible sigh*"

def test_content_sigher_does_not_trigger_if_already_sleeping():
    now = utc_now()
    old = MoodState(arousal=20, valence=60, animation_state="sleeping", last_drift_at=now)
    new = MoodState(arousal=15, valence=60, animation_state="sleeping", last_drift_at=now)
    
    events = get_quirk_events(old, new, ["content_sigher"])
    assert len(events) == 0


def test_one_eye_napper_marks_sleeping_pose():
    detail = get_pose_detail(20, 60, "sleeping", ["one_eye_napper"])
    assert detail["eye_style"] == "one_eye"


def test_fixated_watcher_freezes_tail_and_sets_focus_target():
    detail = get_pose_detail(58, 48, "alert", ["fixated_watcher"])
    assert detail["focus_target"] == "mote"
    assert detail["tail_motion"] == "still"


def test_side_eye_judge_changes_face_when_valence_is_low():
    detail = get_pose_detail(50, 35, "sitting", ["side_eye_judge"])
    assert detail["eye_style"] == "side_eye"
    assert detail["ear_angle"] == "back"


def test_lean_in_greeter_shifts_body_forward_when_content():
    detail = get_pose_detail(52, 70, "sitting", ["lean_in_greeter"])
    assert detail["body_lean"] > 0


def test_lean_in_greeter_overrides_greet_response():
    now = utc_now()
    old = MoodState(arousal=48, valence=65, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=54, valence=68, animation_state="sitting", last_drift_at=now)

    effect = get_action_quirk_effect("greet", old, new, ["lean_in_greeter"])

    assert effect is not None
    assert effect.text == "*leans its whole shoulder into your shin and calls that hello*"
    assert effect.valence_delta == 2


def test_threshold_refuser_can_cancel_walk_energy():
    now = utc_now()
    old = MoodState(arousal=30, valence=42, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=45, valence=46, animation_state="sitting", last_drift_at=now)

    effect = get_action_quirk_effect("walk", old, new, ["threshold_refuser"])

    assert effect is not None
    assert effect.arousal_delta < 0
    assert effect.text == "*sits down at the threshold, and the doorway loses the argument*"


def test_side_eye_judge_overrides_call_when_cat_is_cool():
    now = utc_now()
    old = MoodState(arousal=46, valence=40, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=56, valence=41, animation_state="alert", last_drift_at=now)

    effect = get_action_quirk_effect("call", old, new, ["side_eye_judge"])

    assert effect is not None
    assert effect.text == "*holds you in a long side-eye, then looks away first, slowly*"


def test_zoomie_initiator_triggers_on_transition_into_playful():
    now = utc_now()
    old = MoodState(arousal=54, valence=59, animation_state="sitting", last_drift_at=now)
    new = MoodState(arousal=74, valence=64, animation_state="playful", last_drift_at=now)

    effect = get_scheduler_quirk_effect(
        old,
        new,
        ["zoomie_initiator"],
        facts={},
        now=now,
    )

    assert effect is not None
    assert effect.scene_fx is not None
    assert effect.scene_fx["mode"] == "zoomie"
    assert effect.response_text == "*rips across the room out of nowhere, cornering like it stole something*"
