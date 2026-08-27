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
