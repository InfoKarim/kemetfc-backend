import pytest

from app.video_analysis_publication import project_analysis_result


def test_projects_agility_result_into_player_attributes():
    result = {
        "summary": {"detection_rate": 0.9},
        "movement_analysis": {
            "analysis_type": "agility_ladder",
            "summary": {
                "cadence_steps_per_minute": 160,
                "alternation_rate": 0.9,
                "step_count_imbalance": 0.4,
            },
        },
    }

    projected = project_analysis_result(result)

    assert projected["confidence_score"] == 0.9
    assert projected["overall_score"] == 76.67
    assert projected["strengths"] == [
        {"attribute": "Agility", "score": 80.0},
        {"attribute": "Coordination", "score": 90.0},
    ]
    assert projected["weaknesses"] == [
        {"attribute": "Balance", "score": 60.0},
    ]


def test_projects_generic_pose_detection_quality():
    projected = project_analysis_result(
        {
            "analysis_type": "pose_estimation",
            "summary": {"detection_rate": 0.65},
        }
    )

    assert projected["overall_score"] == 65.0
    assert projected["weaknesses"] == [
        {"attribute": "Movement Visibility", "score": 65.0},
    ]
    assert projected["recommendations"] == [
        "Prioritize Movement Visibility training.",
    ]


def test_projects_pose_estimation_joint_symmetry_from_measurements():
    projected = project_analysis_result(
        {
            "analysis_type": "pose_estimation",
            "summary": {"detection_rate": 0.9},
            "features": {
                "summary": {
                    "left_knee_angle_degrees": {"mean": 170.0},
                    "right_knee_angle_degrees": {"mean": 130.0},
                    "left_hip_angle_degrees": {"mean": 160.0},
                    "right_hip_angle_degrees": {"mean": 158.0},
                }
            },
        }
    )

    attributes = {
        item["attribute"]: item["score"]
        for item in projected["strengths"] + projected["weaknesses"]
    }

    # 40 degree knee gap -> heavily penalized; 2 degree hip gap -> fine.
    assert attributes["Knee Symmetry"] == 20.0
    assert attributes["Hip Symmetry"] == 96.0
    assert attributes["Movement Visibility"] == 90.0


def test_projects_speed_and_agility_from_frame_by_frame_movement():
    # body_height_pixels=150 with a real height of 150cm -> 1 pixel == 1 cm,
    # so a steady 5cm hip move every 100ms is a constant, easy-to-check 0.5 m/s.
    frames = [
        {
            "timestamp_ms": index * 100,
            "measurements": {
                "body_height_pixels": 150.0,
                "hip_center_x_normalized": 0.05 * index,
                "hip_center_y_normalized": 0.5,
            },
        }
        for index in range(6)
    ]

    projected = project_analysis_result(
        {
            "analysis_type": "pose_estimation",
            "summary": {"detection_rate": 0.9},
            "video": {"image_width": 100, "image_height": 100},
            "features": {"summary": {}, "frames": frames},
        },
        player_height_cm=150.0,
    )

    attributes = {
        item["attribute"]: item["score"]
        for item in projected["strengths"] + projected["weaknesses"]
    }

    assert attributes["Speed"] == pytest.approx(0.5 / 6.0 * 100, abs=0.1)
    assert attributes["Acceleration"] == pytest.approx(0.0, abs=0.1)
    assert "Agility" in attributes


def test_speed_and_acceleration_omitted_without_player_height():
    frames = [
        {
            "timestamp_ms": index * 100,
            "measurements": {
                "body_height_pixels": 150.0,
                "hip_center_x_normalized": 0.05 * index,
                "hip_center_y_normalized": 0.5,
            },
        }
        for index in range(6)
    ]

    projected = project_analysis_result(
        {
            "analysis_type": "pose_estimation",
            "summary": {"detection_rate": 0.9},
            "video": {"image_width": 100, "image_height": 100},
            "features": {"summary": {}, "frames": frames},
        }
    )

    attribute_names = {
        item["attribute"]
        for item in projected["strengths"] + projected["weaknesses"]
    }

    assert "Speed" not in attribute_names
    assert "Acceleration" not in attribute_names


def test_projects_full_match_target_metrics():
    projected = project_analysis_result(
        {
            "analysis_type": "full_match",
            "summary": {
                "analysis_confidence": 0.82,
                "player_detection_rate": 0.9,
            },
            "target_player": {
                "pass_completion_rate": 0.75,
                "ball_involvement_rate": 0.4,
                "high_speed_run_count": 3,
            },
        }
    )

    assert projected["confidence_score"] == 0.82
    assert projected["overall_score"] is not None
    assert any(
        item["attribute"] == "Passing"
        for item in projected["strengths"]
    )


def test_abstained_analysis_does_not_generate_player_scores():
    projected = project_analysis_result(
        {
            "analysis_type": "full_match",
            "summary": {"analysis_confidence": 0.3},
            "quality_control": {
                "abstained": True,
                "reasons": ["ball_detection_rate_below_threshold"],
            },
            "target_player": {
                "pass_completion_rate": 0.9,
                "ball_involvement_rate": 0.8,
                "high_speed_run_count": 5,
            },
        }
    )

    assert projected["overall_score"] is None
    assert projected["strengths"] == []
    assert projected["weaknesses"] == []
    assert "withheld" in projected["recommendations"][0]
