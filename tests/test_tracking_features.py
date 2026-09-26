import pytest

from app.services.tracking_features import (
    build_feature_set,
    compute_ball_relationship_features,
    compute_movement_features,
    compute_pose_features,
)


def sample(t, player=None, ball=None, player_conf=0.9, ball_conf=0.9, pose=None, bbox=None):
    return {
        "t_seconds": t,
        "player_bbox": bbox,
        "player_center": player,
        "player_confidence": player_conf if player else None,
        "ball_center": ball,
        "ball_confidence": ball_conf if ball else None,
        "pose_keypoints": pose,
    }


def test_movement_features_insufficient_with_fewer_than_two_points():
    result = compute_movement_features([sample(0.0, player=[0.5, 0.5])])
    assert result["status"] == "insufficient_data"
    assert result["distance_traveled_units"] is None


def test_movement_features_computes_distance_and_speed():
    samples = [
        sample(0.0, player=[0.1, 0.5]),
        sample(1.0, player=[0.2, 0.5]),
        sample(2.0, player=[0.3, 0.5]),
    ]
    result = compute_movement_features(samples)
    assert result["status"] == "ok"
    assert result["distance_traveled_units"] == pytest.approx(0.2)
    assert result["average_speed_units_per_s"] == pytest.approx(0.1)
    assert result["peak_speed_units_per_s"] == pytest.approx(0.1)
    assert result["direction_change_count"] == 0


def test_movement_features_counts_sharp_direction_change():
    samples = [
        sample(0.0, player=[0.0, 0.5]),
        sample(1.0, player=[0.3, 0.5]),  # moving right
        sample(2.0, player=[0.3, 0.8]),  # moving down — 90 degree turn
    ]
    result = compute_movement_features(samples)
    assert result["direction_change_count"] == 1


def test_movement_features_ignores_low_confidence_samples():
    samples = [
        sample(0.0, player=[0.1, 0.5], player_conf=0.9),
        sample(1.0, player=[0.9, 0.5], player_conf=0.1),  # excluded: low confidence
        sample(2.0, player=[0.2, 0.5], player_conf=0.9),
    ]
    result = compute_movement_features(samples)
    # Only 2 usable points remain (t=0 and t=2), not the noisy jump at t=1.
    assert result["usable_sample_count"] == 2
    assert result["distance_traveled_units"] == pytest.approx(0.1)


def test_ball_relationship_insufficient_data_when_never_paired():
    samples = [sample(0.0, player=[0.5, 0.5]), sample(1.0, ball=[0.1, 0.1])]
    result = compute_ball_relationship_features(samples)
    assert result["status"] == "insufficient_data"
    assert result["touch_count"] is None


def test_ball_relationship_detects_possession_interval_and_touch():
    samples = [
        sample(0.0, player=[0.5, 0.5], ball=[0.9, 0.9]),  # far
        sample(1.0, player=[0.5, 0.5], ball=[0.52, 0.52]),  # close -> touch
        sample(2.0, player=[0.5, 0.5], ball=[0.51, 0.51]),  # still close
        sample(3.0, player=[0.5, 0.5], ball=[0.9, 0.9]),  # far again
    ]
    result = compute_ball_relationship_features(samples)
    assert result["status"] == "ok"
    assert result["touch_count"] == 1
    assert len(result["possession_intervals"]) == 1
    assert result["possession_intervals"][0]["start_t"] == 1.0
    assert result["possession_intervals"][0]["end_t"] == 3.0


def test_ball_relationship_does_not_double_count_touches_too_close_in_time():
    samples = [
        sample(0.0, player=[0.5, 0.5], ball=[0.9, 0.9]),
        sample(0.1, player=[0.5, 0.5], ball=[0.51, 0.51]),  # touch 1
        sample(0.2, player=[0.5, 0.5], ball=[0.9, 0.9]),  # brief flicker away
        sample(0.3, player=[0.5, 0.5], ball=[0.51, 0.51]),  # too soon to count again
    ]
    result = compute_ball_relationship_features(samples)
    assert result["touch_count"] == 1


def test_pose_features_insufficient_when_no_keypoints():
    result = compute_pose_features([sample(0.0, player=[0.5, 0.5])])
    assert result["status"] == "insufficient_data"
    assert result["pose_coverage_ratio"] == 0.0


def test_pose_features_computes_upright_torso_lean_near_zero():
    keypoints = [
        {"name": "leftShoulder", "x": 0.45, "y": 0.3},
        {"name": "rightShoulder", "x": 0.55, "y": 0.3},
        {"name": "leftHip", "x": 0.45, "y": 0.5},
        {"name": "rightHip", "x": 0.55, "y": 0.5},
    ]
    samples = [sample(0.0, player=[0.5, 0.4], pose=keypoints, bbox=[0.40, 0.25, 0.20, 0.35])]
    result = compute_pose_features(samples)
    assert result["status"] == "ok"
    assert result["pose_coverage_ratio"] == 1.0
    assert result["average_torso_lean_deg"] == pytest.approx(0.0, abs=1.0)


def test_build_feature_set_combines_all_three_and_is_versioned():
    samples = [
        sample(0.0, player=[0.1, 0.5], ball=[0.11, 0.5]),
        sample(1.0, player=[0.2, 0.5], ball=[0.21, 0.5]),
    ]
    feature_set = build_feature_set(samples)
    assert "schema_version" in feature_set
    assert feature_set["movement"]["status"] == "ok"
    assert feature_set["ball_relationship"]["status"] == "ok"
    assert feature_set["sample_count"] == 2


# --- v2: honest units, heuristic labels, verified pose --------------------------------

from app.services.tracking_features import (  # noqa: E402
    FEATURE_SCHEMA_VERSION,
    build_feature_set,
    measurement_context,
    pose_belongs_to_player,
)

UPRIGHT = [
    {"name": "leftShoulder", "x": 0.45, "y": 0.3},
    {"name": "rightShoulder", "x": 0.55, "y": 0.3},
    {"name": "leftHip", "x": 0.45, "y": 0.5},
    {"name": "rightHip", "x": 0.55, "y": 0.5},
]


def test_feature_set_declares_that_nothing_is_in_metres():
    features = build_feature_set([sample(float(t), player=[0.3 + 0.01 * t, 0.5]) for t in range(5)])

    assert features["schema_version"] == FEATURE_SCHEMA_VERSION == "tracking-features-v2"
    context = features["measurement_context"]
    assert context["space"] == "normalized_image"
    assert context["meters_available"] is False and context["calibrated"] is False
    assert context["distance_unit"] == "normalized_image_units"
    assert context["speed_unit"] == "normalized_image_units_per_second"
    # No metric-unit keys anywhere in the movement payload.
    assert not any(key.endswith(("_m", "_meters", "_mps", "_kmh")) for key in features["movement"])


def test_a_calibration_scale_alone_never_produces_metres():
    context = measurement_context("calibrated", 0.5)
    assert context["calibration_scale_present"] is True
    assert context["meters_available"] is False, "a single scale factor is not a validated conversion"


def test_ball_proximity_numbers_are_labelled_as_heuristic_image_space():
    samples = [sample(t * 0.1, player=[0.5, 0.5], ball=[0.55, 0.5]) for t in range(5)]
    result = compute_ball_relationship_features(samples)
    assert result["touch_count_basis"] == "proximity_heuristic_image_space"
    assert result["possession_basis"] == "proximity_heuristic_image_space"
    assert result["possession_distance_threshold_units"] == 0.10
    # ...also when there is nothing to compute:
    assert compute_ball_relationship_features([sample(0.0, player=[0.5, 0.5])])["touch_count_basis"] == "proximity_heuristic_image_space"


def test_no_ball_means_no_possession_or_touch_metrics_are_invented():
    result = compute_ball_relationship_features([sample(t * 0.1, player=[0.5, 0.5]) for t in range(20)])
    assert result["status"] == "insufficient_data"
    assert result["touch_count"] is None and result["possession_seconds"] is None


def test_a_pose_without_a_player_box_in_the_same_sample_is_not_counted():
    result = compute_pose_features([sample(0.0, player=[0.5, 0.4], pose=UPRIGHT)])   # no bbox: cannot be verified
    assert result["status"] == "insufficient_data"
    assert result["pose_coverage_ratio"] == 0.0
    assert result["pose_unverified_sample_count"] == 1
    assert result["average_torso_lean_deg"] is None, "no pose metric is invented from an unverified pose"


def test_a_pose_outside_the_tracked_players_box_is_not_counted():
    elsewhere = sample(0.0, player=[0.2, 0.4], pose=UPRIGHT, bbox=[0.10, 0.25, 0.12, 0.35])   # joints are around x=0.5
    result = compute_pose_features([elsewhere])
    assert result["pose_unverified_sample_count"] == 1
    assert result["status"] == "insufficient_data"


def test_pose_belongs_to_player_needs_enough_joints_and_a_box():
    box = [0.40, 0.25, 0.20, 0.35]
    assert pose_belongs_to_player(sample(0.0, pose=UPRIGHT, bbox=box)) is True
    assert pose_belongs_to_player(sample(0.0, pose=UPRIGHT[:3], bbox=box)) is False
    assert pose_belongs_to_player(sample(0.0, pose=UPRIGHT, bbox=None)) is False
    assert pose_belongs_to_player(sample(0.0, pose=None, bbox=box)) is False


def test_only_verified_poses_count_toward_coverage():
    good = sample(0.0, player=[0.5, 0.4], pose=UPRIGHT, bbox=[0.40, 0.25, 0.20, 0.35])
    bad = sample(0.1, player=[0.2, 0.4], pose=UPRIGHT, bbox=[0.10, 0.25, 0.12, 0.35])
    result = compute_pose_features([good, bad])
    assert result["pose_coverage_ratio"] == 0.5
    assert result["pose_unverified_sample_count"] == 1
