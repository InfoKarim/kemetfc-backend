import pytest

from app.agility_ladder_analysis import analyze_agility_ladder


def make_agility_features():
    frame_count = 30
    left_ankle = [2.0] * frame_count
    right_ankle = [2.0] * frame_count

    for start, end in ((3, 5), (15, 17)):
        for index in range(start, end + 1):
            left_ankle[index] = 1.4

    for start, end in ((9, 11), (21, 23)):
        for index in range(start, end + 1):
            right_ankle[index] = 1.4

    frames = []

    for index in range(frame_count):
        frames.append(
            {
                "frame_index": index * 3,
                "timestamp_ms": index * 100,
                "measurements": {
                    "left_ankle_hip_vertical_ratio": left_ankle[index],
                    "right_ankle_hip_vertical_ratio": right_ankle[index],
                    "left_knee_hip_vertical_ratio": (
                        1.0 if left_ankle[index] == 2.0 else 0.5
                    ),
                    "right_knee_hip_vertical_ratio": (
                        1.0 if right_ankle[index] == 2.0 else 0.5
                    ),
                    "trunk_lean_degrees": 10.0,
                    "hip_tilt_degrees": -2.0,
                    "shoulder_tilt_degrees": 3.0,
                    "knee_angle_asymmetry_degrees": 5.0,
                },
            }
        )

    return {"frames": frames}


def test_detects_alternating_agility_ladder_steps():
    result = analyze_agility_ladder(make_agility_features())

    assert result["status"] == "completed"
    assert result["requires_coach_review"] is True
    assert result["summary"]["step_count"] == 4
    assert result["summary"]["left_step_count"] == 2
    assert result["summary"]["right_step_count"] == 2
    assert result["summary"]["cadence_steps_per_minute"] == pytest.approx(
        100.0
    )
    assert result["summary"]["alternation_rate"] == 1.0
    assert result["summary"]["step_count_imbalance"] == 0.0
    assert [step["foot"] for step in result["steps"]] == [
        "left",
        "right",
        "left",
        "right",
    ]


def test_agility_ladder_reports_posture_measurements():
    result = analyze_agility_ladder(make_agility_features())
    summary = result["summary"]

    assert summary["mean_foot_lift_torso_ratio"] == pytest.approx(0.6)
    assert summary["mean_knee_drive_torso_ratio"] == pytest.approx(0.5)
    assert summary["mean_absolute_trunk_lean_degrees"] == 10.0
    assert summary["mean_absolute_hip_tilt_degrees"] == 2.0
    assert summary["mean_absolute_shoulder_tilt_degrees"] == 3.0
    assert summary["mean_knee_asymmetry_degrees"] == 5.0


def test_agility_ladder_requires_enough_pose_frames():
    result = analyze_agility_ladder({"frames": []})

    assert result["status"] == "insufficient_pose_data"
    assert result["summary"]["step_count"] == 0


def test_agility_ladder_reports_no_steps_for_static_pose():
    features = make_agility_features()

    for frame in features["frames"]:
        frame["measurements"]["left_ankle_hip_vertical_ratio"] = 2.0
        frame["measurements"]["right_ankle_hip_vertical_ratio"] = 2.0

    result = analyze_agility_ladder(features)

    assert result["status"] == "no_steps_detected"
    assert result["summary"]["step_count"] == 0
