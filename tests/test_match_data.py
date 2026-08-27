import pytest
from datetime import datetime

from app.data_models import MatchData


def make_valid_match(**overrides):
    data = {
        "match_id": "M001",
        "competition_id": "COMP001",
        "season_id": "2026",
        "home_team_id": "TEAM001",
        "away_team_id": "TEAM002",
        "match_date": datetime(2026, 8, 10, 20, 0),
        "venue_id": "VENUE001",
        "status": "scheduled",
        "home_score": None,
        "away_score": None,
    }

    data.update(overrides)
    return MatchData(**data)


def test_valid_match_data():
    match = make_valid_match()

    assert match.match_id == "M001"
    assert match.home_team_id == "TEAM001"
    assert match.away_team_id == "TEAM002"
    assert match.status == "scheduled"
    assert match.home_score is None
    assert match.away_score is None


def test_match_invalid_status():
    with pytest.raises(ValueError):
        make_valid_match(status="unknown")


def test_match_teams_must_be_different():
    with pytest.raises(ValueError):
        make_valid_match(
            home_team_id="TEAM001",
            away_team_id="TEAM001",
        )


def test_match_home_score_cannot_be_negative():
    with pytest.raises(ValueError):
        make_valid_match(
            status="completed",
            home_score=-1,
            away_score=0,
        )


def test_match_away_score_cannot_be_negative():
    with pytest.raises(ValueError):
        make_valid_match(
            status="completed",
            home_score=0,
            away_score=-1,
        )


def test_scheduled_match_cannot_have_score():
    with pytest.raises(ValueError):
        make_valid_match(
            status="scheduled",
            home_score=1,
            away_score=0,
        )


def test_completed_match_requires_home_score():
    with pytest.raises(ValueError):
        make_valid_match(
            status="completed",
            home_score=None,
            away_score=0,
        )


def test_completed_match_requires_away_score():
    with pytest.raises(ValueError):
        make_valid_match(
            status="completed",
            home_score=1,
            away_score=None,
        )


def test_valid_completed_match():
    match = make_valid_match(
        status="completed",
        home_score=2,
        away_score=1,
    )

    assert match.home_score == 2
    assert match.away_score == 1
