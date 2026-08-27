from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.data_models import TrainingPlanData
from app.services.training_plan_service import TrainingPlanService


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TrainingPlanService(db=TestingSessionLocal())


def make_plan(plan_id="PLAN001"):
    return TrainingPlanData(
        plan_id=plan_id,
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
                "drills": [],
            }
        ],
    )


def test_add_and_get_training_plan():
    service = make_service()
    plan = make_plan()

    service.add_plan(plan)

    saved = service.get_plan("PLAN001")

    assert saved == plan


def test_get_all_training_plans():
    service = make_service()
    service.add_plan(make_plan())

    plans = service.get_all_plans()

    assert len(plans) == 1
    assert plans[0].plan_id == "PLAN001"


def test_update_training_plan():
    service = make_service()
    plan = make_plan()
    service.add_plan(plan)

    plan.status = "active"
    updated = service.update_plan(plan)

    assert updated is True
    assert service.get_plan("PLAN001").status == "active"


def test_delete_training_plan():
    service = make_service()
    service.add_plan(make_plan())

    deleted = service.delete_plan("PLAN001")

    assert deleted is True
    assert service.get_plan("PLAN001") is None


def test_missing_training_plan_operations():
    service = make_service()

    assert service.get_plan("DOES_NOT_EXIST") is None
    assert service.update_plan(
        make_plan("DOES_NOT_EXIST")
    ) is False
    assert service.delete_plan("DOES_NOT_EXIST") is False


def test_get_training_plans_by_player():
    service = make_service()
    service.add_plan(make_plan())

    plans = service.get_plans_by_player("P001")

    assert len(plans) == 1
    assert plans[0].player_id == "P001"


def test_get_training_plans_by_analysis():
    service = make_service()
    service.add_plan(make_plan())

    plans = service.get_plans_by_analysis("AN001")

    assert len(plans) == 1
    assert plans[0].analysis_id == "AN001"
