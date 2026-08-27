from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.data_models import TeamData
from app.services.team_service import TeamService


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
    return TeamService(db=db)


def make_team():
    return TeamData(
        team_id="TEAM_U10",
        name="TrainingBuddy U10",
        age_group="U10",
        coach_name="Coach Ahmed",
        season_id="2026-2027",
        active=True,
    )


def test_add_and_get_team():
    service = make_service()
    team = make_team()

    service.add_team(team)

    saved_team = service.get_team("TEAM_U10")

    assert saved_team is not None
    assert saved_team.team_id == "TEAM_U10"
    assert saved_team.name == "TrainingBuddy U10"
    assert saved_team.age_group == "U10"
    assert saved_team.coach_name == "Coach Ahmed"



def test_get_unknown_team_returns_none():
    service = make_service()

    assert service.get_team("DOES_NOT_EXIST") is None


def test_get_all_teams():
    service = make_service()
    service.add_team(make_team())

    teams = service.get_all_teams()

    assert len(teams) == 1
    assert teams[0].team_id == "TEAM_U10"


def test_update_team():
    service = make_service()
    team = make_team()
    service.add_team(team)

    team.name = "TrainingBuddy Academy U10"
    team.coach_name = "Coach Karim"

    updated = service.update_team(team)
    saved_team = service.get_team("TEAM_U10")

    assert updated is True
    assert saved_team is not None
    assert saved_team.name == "TrainingBuddy Academy U10"
    assert saved_team.coach_name == "Coach Karim"


def test_update_unknown_team_returns_false():
    service = make_service()

    assert service.update_team(make_team()) is False


def test_delete_team():
    service = make_service()
    service.add_team(make_team())

    deleted = service.delete_team("TEAM_U10")

    assert deleted is True
    assert service.get_team("TEAM_U10") is None


def test_delete_unknown_team_returns_false():
    service = make_service()

    assert service.delete_team("DOES_NOT_EXIST") is False
