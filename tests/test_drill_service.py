from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.data_models import DrillData
from app.services.drill_service import DrillService


TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()

    return DrillService(db=db)


def make_drill():
    return DrillData(
        drill_id="DRILL001",
        name="Scanning Before Receiving",
        category="Vision",
        description="Player scans before receiving the ball.",
        min_age=7,
        max_age=13,
        difficulty="beginner",
        duration_minutes=10,
        equipment=["ball", "cones"],
        video_url="/drills/scanning_before_receiving.mp4",
        active=True,
    )


def test_add_and_get_drill():
    service = make_service()
    drill = make_drill()

    service.add_drill(drill)

    saved_drill = service.get_drill("DRILL001")

    assert saved_drill is not None
    assert saved_drill.drill_id == "DRILL001"
    assert saved_drill.name == "Scanning Before Receiving"
    assert saved_drill.category == "Vision"
    assert saved_drill.video_url == "/drills/scanning_before_receiving.mp4"

def test_get_unknown_drill_returns_none():
    service = make_service()

    result = service.get_drill("DOES_NOT_EXIST")

    assert result is None

def test_get_all_drills():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.get_all_drills()

    assert len(drills) == 1
    assert drills[0].drill_id == "DRILL001"



def test_delete_drill():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    deleted = service.delete_drill("DRILL001")

    assert deleted is True
    assert service.get_drill("DRILL001") is None


def test_update_drill():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    updated_drill = make_drill()
    updated_drill.name = "Advanced Scanning Drill"
    updated_drill.duration_minutes = 15

    updated = service.update_drill(updated_drill)

    saved_drill = service.get_drill("DRILL001")

    assert updated is True
    assert saved_drill is not None
    assert saved_drill.name == "Advanced Scanning Drill"
    assert saved_drill.duration_minutes == 15


def test_delete_missing_drill_returns_false():
    service = make_service()

    deleted = service.delete_drill("DOES_NOT_EXIST")

    assert deleted is False


def test_update_missing_drill_returns_false():
    service = make_service()

    drill = make_drill()
    drill.drill_id = "DOES_NOT_EXIST"

    updated = service.update_drill(drill)

    assert updated is False


def test_get_drills_by_category():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.get_drills_by_category("Vision")

    assert len(drills) == 1
    assert drills[0].drill_id == "DRILL001"
    assert drills[0].category == "Vision"


def test_get_drills_for_age():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.get_drills_for_age(10)

    assert len(drills) == 1
    assert drills[0].drill_id == "DRILL001"
    assert drills[0].min_age <= 10 <= drills[0].max_age


def test_get_drills_for_age_excludes_out_of_range():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.get_drills_for_age(16)

    assert drills == []


def test_get_drills_by_difficulty():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.get_drills_by_difficulty("beginner")

    assert len(drills) == 1
    assert drills[0].drill_id == "DRILL001"
    assert drills[0].difficulty == "beginner"


def test_find_suitable_drills():
    service = make_service()

    drill = make_drill()
    service.add_drill(drill)

    drills = service.find_suitable_drills(
        category="Vision",
        age=10,
        difficulty="beginner",
    )

    assert len(drills) == 1
    assert drills[0].drill_id == "DRILL001"
    assert drills[0].category == "Vision"
    assert drills[0].min_age <= 10 <= drills[0].max_age
    assert drills[0].difficulty == "beginner"


def test_find_suitable_drills_excludes_inactive():
    service = make_service()

    drill = make_drill()
    drill.active = False
    service.add_drill(drill)

    drills = service.find_suitable_drills(
        category="Vision",
        age=10,
        difficulty="beginner",
    )

    assert drills == []
