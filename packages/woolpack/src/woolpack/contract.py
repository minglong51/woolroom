from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PackEnvironment:
    fx_vocab_version: int
    fx_modes: frozenset[str]
    quirk_emit_types: frozenset[str]
    action_ids: frozenset[str]
    spot_ids: frozenset[str]
    condition_ids: frozenset[str]
    pose_keys: frozenset[str]
    pose_write_ops: frozenset[str]
    species_ids: frozenset[str]
    overlay_ids: frozenset[str]
    quirk_ids: frozenset[str]
    coat_label_ids: frozenset[str]


DEFAULT_ENVIRONMENT = PackEnvironment(
    fx_vocab_version=1,
    fx_modes=frozenset(
        {
            "greet",
            "petting",
            "kibble",
            "leash_tug",
            "call_ring",
            "message_ping",
            "zoomie",
            "flinch_away",
            "petting_melt",
            "head_tilt",
            "sigh_settle",
            "threshold_refusal",
            "stash",
            "carry",
            "lean_in",
            "side_eye",
            "ignored",
            "petting_head",
            "petting_ear",
            "petting_tail",
            "petting_belly",
            "warm_spot",
            "brushed_coat",
            "bowl",
            "leash",
            "phone_glow",
            "rumpled_rug",
        }
    ),
    quirk_emit_types=frozenset({"response"}),
    action_ids=frozenset({"greet", "feed", "pet", "walk", "call", "message", "play"}),
    spot_ids=frozenset({"body", "head", "ear", "tail", "belly"}),
    condition_ids=frozenset(
        {
            "action_in",
            "state_in",
            "state_not_in",
            "valence_gte",
            "valence_lt",
            "arousal_gte",
            "arousal_lt",
            "old_arousal_lt",
            "old_valence_lt",
            "enters_state",
            "fact_day_gate",
            "fact_exists",
            "any",
        }
    ),
    pose_keys=frozenset(
        {"body_lean", "head_shift_y", "ear_angle", "eye_style", "tail_motion", "focus_target"}
    ),
    pose_write_ops=frozenset({"set", "min", "set_if_default"}),
    species_ids=frozenset({"cat"}),
    overlay_ids=frozenset(),
    quirk_ids=frozenset(
        {
            "hides_small_things",
            "threshold_refuser",
            "one_eye_napper",
            "lean_in_greeter",
            "content_sigher",
            "fixated_watcher",
            "zoomie_initiator",
            "side_eye_judge",
        }
    ),
    coat_label_ids=frozenset({"tuxedo", "marmalade", "ash"}),
)
