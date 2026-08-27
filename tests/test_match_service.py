from datetime import datetime

from app.data_models import MatchData
from app.services.match_service import MatchService

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base

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

Base.metadata.create_all(bind=engine)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    return MatchService(db=db)

def test_add_and_get_match():
    service = make_service()

    match = MatchData(
        match_id="M001",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    service.add_match(match)

    result = service.get_match("M001")

    assert result == match

def test_get_unknown_match_returns_none():
    service = make_service()

    result = service.get_match("UNKNOWN")

    assert result is None
def test_get_all_matches():
    service = make_service()

    match1 = MatchData(
        match_id="M001",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    match2 = MatchData(
        match_id="M002",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM003",
        away_team_id="TEAM004",
        match_date=datetime(2026, 8, 11, 20, 0),
        venue_id="VENUE002",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    service.add_match(match1)
    service.add_match(match2)

    results = service.get_all_matches()

    assert results == [match1, match2]

def test_delete_match():
    service = make_service()

    match = MatchData(
        match_id="M001",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    service.add_match(match)

    deleted = service.delete_match("M001")

    assert deleted is True
    assert service.get_match("M001") is None

def test_delete_unknown_match_returns_false():
    service = make_service()

    deleted = service.delete_match("UNKNOWN")

    assert deleted is False

def test_update_match():
    service = make_service()

    match = MatchData(
        match_id="M001",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    service.add_match(match)

    updated_match = MatchData(
        match_id="M001",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="completed",
        home_score=2,
        away_score=1,
    )

    updated = service.update_match(updated_match)

    assert updated is True
    assert service.get_match("M001") == updated_match

def test_update_unknown_match_returns_false():
    service = make_service()

    match = MatchData(
        match_id="UNKNOWN",
        competition_id="COMP001",
        season_id="2026",
        home_team_id="TEAM001",
        away_team_id="TEAM002",
        match_date=datetime(2026, 8, 10, 20, 0),
        venue_id="VENUE001",
        status="scheduled",
        home_score=None,
        away_score=None,
    )

    updated = service.update_match(match)

    assert updated is False


