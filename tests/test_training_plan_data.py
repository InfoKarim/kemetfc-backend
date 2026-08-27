from datetime import datetime

from app.data_models import TrainingPlanData


def test_valid_training_plan_data():
    plan = TrainingPlanData(
        plan_id="PLAN001",
        player_id="P001",
        analysis_id="AN001",
        created_at=datetime.now(),
        status="draft",
        player_difficulty="beginner",
        target_duration=30,
        available_equipment=["ball", "cones"],
        recommendations=[
            {
                "weakness": "Vision",
                "weakness_score": 50,
                "drills": [
                    {
                        "drill_id": "DRILL001",
                        "name": "Vision Drill",
                    }
                ],
            }
        ],
    )

    assert plan.plan_id == "PLAN001"
    assert plan.player_id == "P001"
    assert plan.status == "draft"
    assert plan.target_duration == 30


def make_plan(**changes):
    values = {
        "plan_id": "PLAN001",
        "player_id": "P001",
        "analysis_id": "AN001",
        "created_at": datetime.now(),
        "status": "draft",
        "player_difficulty": "beginner",
        "target_duration": 30,
        "available_equipment": ["ball", "cones"],
        "recommendations": [],
    }
    values.update(changes)
    return TrainingPlanData(**values)


def test_training_plan_ids_cannot_be_empty():
    import pytest

    with pytest.raises(ValueError):
        make_plan(plan_id="")


def test_training_plan_rejects_invalid_status():
    import pytest

    with pytest.raises(ValueError):
        make_plan(status="unknown")


def test_training_plan_duration_must_be_positive():
    import pytest

    with pytest.raises(ValueError):
        make_plan(target_duration=0)


def test_training_plan_rejects_invalid_difficulty():
    import pytest

    with pytest.raises(ValueError):
        make_plan(player_difficulty="impossible")
