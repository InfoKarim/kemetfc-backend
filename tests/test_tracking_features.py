import pytest

from app.services.tracking_features import (
    build_feature_set,
    compute_ball_relationship_features,
    compute_movement_features,
    compute_pose_features,
)


def sample(t, player=None, ball=None, player_conf=0.9, ball_conf=0.9, pose=None):
    return {
        "t_seconds": t,
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
    samples = [sample(0.0, player=[0.5, 0.4], pose=keypoints)]
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
