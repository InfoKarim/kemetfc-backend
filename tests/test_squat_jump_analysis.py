import pytest

from app.squat_jump_analysis import (
    analyze_squat_jumps,
    interpolate_short_gaps,
    smooth_signal,
)


def make_pose_features(ankle_values=None):
    knee_angles = [
        170,
        168,
        160,
        145,
        125,
        100,
        90,
        95,
        115,
        140,
        158,
        168,
        170,
    ]
    ankle_values = ankle_values or [
        0.90,
        0.90,
        0.90,
        0.90,
        0.90,
        0.90,
        0.89,
        0.84,
        0.80,
        0.84,
        0.88,
        0.90,
        0.90,
    ]
    frames = []

    for index, (knee_angle, ankle_y) in enumerate(
        zip(knee_angles, ankle_values)
    ):
        frames.append(
            {
                "frame_index": index * 3,
                "timestamp_ms": index * 100,
                "measurements": {
                    "left_knee_angle_degrees": knee_angle,
                    "right_knee_angle_degrees": knee_angle,
                    "ankle_center_y_normalized": ankle_y,
                    "hip_center_y_normalized": (
                        0.5 + (170 - knee_angle) / 400
                    ),
                    "knee_angle_asymmetry_degrees": 0.0,
                    "trunk_lean_degrees": 10.0,
                },
            }
        )

    return {"frames": frames}


def test_interpolate_short_gaps():
    assert interpolate_short_gaps(
        [1.0, None, None, 4.0],
        max_gap=2,
    ) == [1.0, 2.0, 3.0, 4.0]


def test_interpolate_preserves_long_gaps():
    assert interpolate_short_gaps(
        [1.0, None, None, None, 5.0],
        max_gap=2,
    ) == [1.0, None, None, None, 5.0]


def test_smooth_signal_rejects_even_window():
    with pytest.raises(ValueError, match="positive odd"):
        smooth_signal([1.0, 2.0], window_size=2)


def test_detects_squat_jump_cycle_and_phases():
    result = analyze_squat_jumps(make_pose_features())

    assert result["status"] == "completed"
    assert result["requires_coach_review"] is True
    assert result["summary"] == {
        "movement_cycle_count": 1,
        "jump_count": 1,
    }
    repetition = result["repetitions"][0]
    assert repetition["jump_detected"] is True
    assert repetition["measurements"][
        "minimum_knee_angle_degrees"
    ] == pytest.approx(100.0)
    assert [phase["name"] for phase in repetition["phases"]] == [
        "descent",
        "ascent",
        "flight",
        "landing_recovery",
    ]


def test_squat_cycle_without_ankle_lift_is_not_a_jump():
    result = analyze_squat_jumps(
        make_pose_features(ankle_values=[0.9] * 13)
    )

    assert result["summary"] == {
        "movement_cycle_count": 1,
        "jump_count": 0,
    }
    assert result["repetitions"][0]["jump_detected"] is False


def test_reports_insufficient_movement():
    frames = [
        {
            "frame_index": index * 3,
            "timestamp_ms": index * 100,
            "measurements": {
                "left_knee_angle_degrees": 170 - index,
                "right_knee_angle_degrees": 170 - index,
            },
        }
        for index in range(8)
    ]

    result = analyze_squat_jumps({"frames": frames})

    assert result["status"] == "insufficient_movement"
    assert result["summary"]["movement_cycle_count"] == 0
