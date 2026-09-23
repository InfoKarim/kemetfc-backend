from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data_models import DrillData
from app.database import Base
from app.db_models import PlayerAssessmentDB, PlayerDB, PlayerMetricDB
from app.services.drill_service import DrillService
from app.services.trend_drill_recommendation_service import (
    PlayerNotFoundError,
    TrendDrillRecommendationService,
)

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def enable_test_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(bind=test_engine)


PLAYER_PROFILE_KWARGS = dict(
    physical_profile={
        "height_cm": 140.0, "weight_kg": 35.0, "dominant_foot": "right",
        "speed": 70.0, "acceleration": 72.0, "agility": 68.0,
        "stamina": 75.0, "strength": 60.0,
    },
    technical_profile={
        "ball_control": 70.0, "dribbling": 72.0, "passing": 68.0,
        "shooting": 65.0, "finishing": 67.0,
    },
    mental_profile={
        "decision_making": 70.0, "concentration": 72.0, "composure": 68.0,
        "positioning": 71.0, "vision": 74.0, "awareness": 70.0,
        "game_reading": 70.0, "coachability": 70.0,
    },
    match_performance={
        "minutes_played": 0, "goals": 0, "assists": 0, "shots": 0,
        "shots_on_target": 0, "passes_attempted": 0, "passes_completed": 0,
        "tackles": 0, "interceptions": 0, "rating": 0.0,
    },
)


def make_player(session, player_id: str, birth_year: int = 2015):
    session.add(PlayerDB(
        player_id=player_id,
        first_name_ar="a", last_name_ar="b",
        first_name_en="Test", last_name_en=player_id,
        date_of_birth=date(birth_year, 1, 1),
        sex="male",
        **PLAYER_PROFILE_KWARGS,
    ))
    session.commit()


def add_metric(session, player_id, assessment_id, name, score, recorded_at):
    session.add(PlayerAssessmentDB(
        assessment_id=assessment_id, player_id=player_id,
        pillar="technical", test_category="ball_mastery", test_type="ball_mastery",
        methodology_version="v1", test_date=recorded_at, age_at_assessment_years=11,
        raw_data={}, calculated_metrics={}, created_at=datetime.now(),
    ))
    session.commit()
    session.add(PlayerMetricDB(
        player_id=player_id, assessment_id=assessment_id, metric_name=name,
        category="technical", metric_score=score, recorded_at=recorded_at,
        source="coach_rating", created_at=datetime.now(),
    ))
    session.commit()


def make_drill(db, drill_id, category, min_age=5, max_age=18, difficulty="beginner", duration_minutes=20, equipment=None):
    DrillService(db).add_drill(DrillData(
        drill_id=drill_id,
        name=f"Drill {drill_id}",
        category=category,
        description="A real drill.",
        min_age=min_age,
        max_age=max_age,
        difficulty=difficulty,
        duration_minutes=duration_minutes,
        equipment=equipment or [],
        video_url="https://example.com/video.mp4",
        active=True,
    ))


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_recommend_raises_for_unknown_player(db):
    with pytest.raises(PlayerNotFoundError):
        TrendDrillRecommendationService(db).recommend("NOPE")


def test_recommend_returns_empty_when_no_declining_or_plateaued_metrics(db):
    make_player(db, "P001")
    # A single reading has "insufficient_data" direction (Step 3) — not
    # declining, so nothing should be flagged.
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 3, date(2026, 1, 1))
    make_drill(db, "DRILL1", "Ball Control")

    result = TrendDrillRecommendationService(db).recommend("P001")

    assert result["player_id"] == "P001"
    assert result["recommendations"] == []
    assert result["unranked_training_needs"] == []


def test_recommend_ranks_real_drills_for_a_declining_metric(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 4, date(2026, 1, 1))
    add_metric(db, "P001", "A2", "ball_mastery_sole_rolls", 2, date(2026, 2, 1))  # declining
    make_drill(db, "DRILL_MATCH", "Ball Control")
    make_drill(db, "DRILL_OTHER", "Shooting")

    result = TrendDrillRecommendationService(db).recommend("P001")

    assert len(result["recommendations"]) == 1
    group = result["recommendations"][0]
    assert group["weakness"] == "Ball Control"
    assert group["source_metrics"] == ["ball_mastery_sole_rolls"]
    # rank_drills() (existing, unmodified) scores rather than hard-
    # filters by category — the matching drill must rank first.
    drill_ids = [d["drill_id"] for d in group["drills"]]
    assert drill_ids[0] == "DRILL_MATCH"


def test_recommend_reports_unranked_training_need_for_unscaled_metric(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "yoyo_kids_distance_m", 600, date(2026, 1, 1))
    add_metric(db, "P001", "A2", "yoyo_kids_distance_m", 400, date(2026, 2, 1))  # declining

    result = TrendDrillRecommendationService(db).recommend("P001")

    assert result["recommendations"] == []
    assert len(result["unranked_training_needs"]) == 1
    assert result["unranked_training_needs"][0]["metric_name"] == "yoyo_kids_distance_m"
    assert result["unranked_training_needs"][0]["reason"] == "declining"


def test_recommend_excludes_drills_outside_the_players_age_range(db):
    make_player(db, "P001", birth_year=2015)  # ~11 years old
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 4, date(2026, 1, 1))
    add_metric(db, "P001", "A2", "ball_mastery_sole_rolls", 2, date(2026, 2, 1))
    make_drill(db, "DRILL_TOO_OLD", "Ball Control", min_age=16, max_age=18)
    make_drill(db, "DRILL_FITS", "Ball Control", min_age=8, max_age=14)

    result = TrendDrillRecommendationService(db).recommend("P001")

    drill_ids = [d["drill_id"] for d in result["recommendations"][0]["drills"]]
    assert "DRILL_FITS" in drill_ids
    assert "DRILL_TOO_OLD" not in drill_ids
