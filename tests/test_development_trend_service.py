from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import PlayerAssessmentDB, PlayerDB, PlayerMetricDB
from app.development_trend import DIRECTION_IMPROVING, DIRECTION_INSUFFICIENT_DATA
from app.services.development_trend_service import DevelopmentTrendService

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


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    session.add(PlayerDB(
        player_id="P001",
        first_name_ar="a", last_name_ar="b",
        first_name_en="Test", last_name_en="Player",
        date_of_birth=date(2015, 1, 1),
        sex="male",
        physical_profile={}, technical_profile={}, mental_profile={},
        match_performance={},
    ))
    session.commit()
    session.add(PlayerAssessmentDB(
        assessment_id="ASSESS1",
        player_id="P001",
        pillar="technical",
        test_category="ball_mastery",
        test_type="ball_mastery",
        methodology_version="v1",
        test_date=date(2026, 1, 1),
        age_at_assessment_years=11,
        raw_data={}, calculated_metrics={},
        created_at=datetime.now(),
    ))
    session.add(PlayerAssessmentDB(
        assessment_id="ASSESS2",
        player_id="P001",
        pillar="technical",
        test_category="ball_mastery",
        test_type="ball_mastery",
        methodology_version="v1",
        test_date=date(2026, 2, 1),
        age_at_assessment_years=11,
        raw_data={}, calculated_metrics={},
        created_at=datetime.now(),
    ))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def add_metric(db, assessment_id, name, score, recorded_at, category="technical"):
    db.add(PlayerMetricDB(
        player_id="P001",
        assessment_id=assessment_id,
        metric_name=name,
        category=category,
        metric_score=score,
        recorded_at=recorded_at,
        source="coach_rating",
        created_at=datetime.now(),
    ))
    db.commit()


def test_get_trend_uses_the_configured_default_threshold(db):
    add_metric(db, "ASSESS1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))
    add_metric(db, "ASSESS2", "ball_mastery_sole_rolls", 4, date(2026, 2, 1))

    trend = DevelopmentTrendService(db).get_trend("P001", "ball_mastery_sole_rolls")

    assert trend.direction == DIRECTION_IMPROVING
    assert trend.current_score == 4
    assert trend.previous_score == 2


def test_get_trend_raises_without_a_configured_or_supplied_threshold(db):
    add_metric(db, "ASSESS1", "some_future_metric", 50, date(2026, 1, 1))

    with pytest.raises(ValueError, match="No minimum_meaningful_change"):
        DevelopmentTrendService(db).get_trend("P001", "some_future_metric")


def test_get_trend_accepts_an_explicit_threshold_override(db):
    add_metric(db, "ASSESS1", "some_future_metric", 50, date(2026, 1, 1))
    add_metric(db, "ASSESS2", "some_future_metric", 51, date(2026, 2, 1))

    trend = DevelopmentTrendService(db).get_trend(
        "P001", "some_future_metric", minimum_meaningful_change=5.0
    )

    assert trend.direction != DIRECTION_INSUFFICIENT_DATA


def test_get_trend_with_no_recorded_metric_is_insufficient_data(db):
    trend = DevelopmentTrendService(db).get_trend("P001", "ball_mastery_sole_rolls")
    assert trend.direction == DIRECTION_INSUFFICIENT_DATA
    assert trend.points == []


def test_get_all_trends_skips_metrics_without_a_configured_threshold(db):
    add_metric(db, "ASSESS1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))
    add_metric(db, "ASSESS1", "some_unconfigured_metric", 10, date(2026, 1, 1))

    trends = DevelopmentTrendService(db).get_all_trends("P001")

    names = {t.metric_name for t in trends}
    assert "ball_mastery_sole_rolls" in names
    assert "some_unconfigured_metric" not in names
