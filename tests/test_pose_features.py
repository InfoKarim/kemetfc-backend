import pytest

from app.pose_features import (
    LANDMARK_INDEX,
    calculate_frame_features,
    extract_pose_features,
)


IMAGE_WIDTH = 200
IMAGE_HEIGHT = 100


def make_landmarks(**points):
    landmarks = [
        {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "visibility": 1.0,
            "presence": 1.0,
        }
        for _ in range(33)
    ]

    for name, coordinates in points.items():
        x, y, *z = coordinates
        landmarks[LANDMARK_INDEX[name]].update(
            {
                "x": x / IMAGE_WIDTH,
                "y": y / IMAGE_HEIGHT,
                "z": (z[0] if z else 0.0) / IMAGE_WIDTH,
            }
        )

    return landmarks


def full_pose_landmarks():
    return make_landmarks(
        left_shoulder=(80, 20),
        right_shoulder=(120, 20),
        left_elbow=(70, 40),
        right_elbow=(130, 40),
        left_wrist=(80, 40),
        right_wrist=(120, 40),
        left_hip=(80, 50),
        right_hip=(120, 50),
        left_knee=(80, 75),
        right_knee=(120, 75),
        left_ankle=(105, 75),
        right_ankle=(145, 75),
    )


def test_calculate_frame_features_corrects_for_image_dimensions():
    features = calculate_frame_features(
        landmarks=full_pose_landmarks(),
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT,
    )

    assert features["left_knee_angle_degrees"] == pytest.approx(90.0)
    assert features["right_knee_angle_degrees"] == pytest.approx(90.0)
    assert features["trunk_lean_degrees"] == pytest.approx(0.0)
    assert features["shoulder_tilt_degrees"] == pytest.approx(0.0)
    assert features["hip_tilt_degrees"] == pytest.approx(0.0)
    assert features["stance_width_shoulder_ratio"] == pytest.approx(1.0)
    assert features["knee_angle_asymmetry_degrees"] == pytest.approx(0.0)
    assert features["left_knee_hip_vertical_ratio"] == pytest.approx(
        25 / 30
    )
    assert features["right_ankle_hip_vertical_ratio"] == pytest.approx(
        25 / 30
    )


def test_unreliable_landmark_excludes_affected_measurements():
    landmarks = full_pose_landmarks()
    landmarks[LANDMARK_INDEX["left_knee"]]["visibility"] = 0.2

    features = calculate_frame_features(
        landmarks=landmarks,
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT,
    )

    assert "left_knee_angle_degrees" not in features
    assert "left_hip_angle_degrees" not in features
    assert "right_knee_angle_degrees" in features
    assert "knee_angle_asymmetry_degrees" not in features


def test_tilt_is_independent_of_left_right_image_orientation():
    landmarks = full_pose_landmarks()
    left = landmarks[LANDMARK_INDEX["left_shoulder"]]
    right = landmarks[LANDMARK_INDEX["right_shoulder"]]
    left["x"], right["x"] = right["x"], left["x"]

    features = calculate_frame_features(
        landmarks=landmarks,
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT,
    )

    assert features["shoulder_tilt_degrees"] == pytest.approx(0.0)


def test_extract_pose_features_summarizes_frames():
    landmark_frames = [
        {
            "frame_index": 0,
            "timestamp_ms": 0,
            "landmarks": full_pose_landmarks(),
        },
        {
            "frame_index": 3,
            "timestamp_ms": 100,
            "landmarks": full_pose_landmarks(),
        },
    ]

    result = extract_pose_features(
        landmark_frames=landmark_frames,
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT,
    )

    assert result["frames_with_features"] == 2
    assert result["feature_coverage"] == 1.0
    assert len(result["frames"]) == 2
    knee_summary = result["summary"]["left_knee_angle_degrees"]
    assert knee_summary["count"] == 2
    assert knee_summary["mean"] == pytest.approx(90.0)
    assert knee_summary["standard_deviation"] == pytest.approx(0.0)


def test_extract_pose_features_handles_no_landmarks():
    result = extract_pose_features(
        landmark_frames=[],
        image_width=IMAGE_WIDTH,
        image_height=IMAGE_HEIGHT,
    )

    assert result["frames_with_features"] == 0
    assert result["feature_coverage"] == 0.0
    assert result["summary"] == {}
    assert result["frames"] == []


def test_pose_features_require_valid_dimensions():
    with pytest.raises(ValueError, match="image dimensions"):
        calculate_frame_features(
            landmarks=full_pose_landmarks(),
            image_width=0,
            image_height=IMAGE_HEIGHT,
        )
