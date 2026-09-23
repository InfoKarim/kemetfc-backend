from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import PlayerAssessmentDB, PlayerDB, PlayerMetricDB
from app.ml_prediction import MINIMUM_TRAINING_SAMPLES, InsufficientTrainingDataError
from app.services.ml_prediction_service import (
    MODEL_SCOPE_PLAYER_SPECIFIC,
    MODEL_SCOPE_POPULATION,
    MLPredictionService,
    NoRecordedMetricError,
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


def make_player(session, player_id: str):
    session.add(PlayerDB(
        player_id=player_id,
        first_name_ar="a", last_name_ar="b",
        first_name_en="Test", last_name_en=player_id,
        date_of_birth=date(2015, 1, 1),
        sex="male",
        physical_profile={}, technical_profile={}, mental_profile={},
        match_performance={},
    ))
    session.commit()


def add_metric(session, player_id: str, assessment_id: str, name: str, score: float, recorded_at: date):
    session.add(PlayerAssessmentDB(
        assessment_id=assessment_id,
        player_id=player_id,
        pillar="technical",
        test_category="ball_mastery",
        test_type="ball_mastery",
        methodology_version="v1",
        test_date=recorded_at,
        age_at_assessment_years=11,
        raw_data={}, calculated_metrics={},
        created_at=datetime.now(),
    ))
    session.commit()
    session.add(PlayerMetricDB(
        player_id=player_id,
        assessment_id=assessment_id,
        metric_name=name,
        category="technical",
        metric_score=score,
        recorded_at=recorded_at,
        source="coach_rating",
        created_at=datetime.now(),
    ))
    session.commit()


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_predict_raises_when_player_has_no_metric_history(db):
    make_player(db, "P001")

    with pytest.raises(NoRecordedMetricError):
        MLPredictionService(db).predict("P001", "ball_mastery_sole_rolls", forecast_days=30)


def test_predict_raises_when_forecast_days_not_positive(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))

    with pytest.raises(ValueError, match="forecast_days must be positive"):
        MLPredictionService(db).predict("P001", "ball_mastery_sole_rolls", forecast_days=0)


def test_predict_raises_for_unknown_model_scope(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))

    with pytest.raises(ValueError, match="Unknown or unimplemented model_scope"):
        MLPredictionService(db).predict(
            "P001", "ball_mastery_sole_rolls", forecast_days=30, model_scope="age_group"
        )


def test_predict_player_specific_requires_that_players_own_minimum_samples(db):
    make_player(db, "P001")
    for i in range(MINIMUM_TRAINING_SAMPLES - 1):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 2 + i, date(2026, 1, 1) + timedelta(days=i * 10))

    with pytest.raises(InsufficientTrainingDataError):
        MLPredictionService(db).predict(
            "P001", "ball_mastery_sole_rolls", forecast_days=30,
            model_scope=MODEL_SCOPE_PLAYER_SPECIFIC,
        )


def test_predict_player_specific_succeeds_and_persists(db):
    make_player(db, "P001")
    for i in range(MINIMUM_TRAINING_SAMPLES):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + i, date(2026, 1, 1) + timedelta(days=i * 10))

    prediction = MLPredictionService(db).predict(
        "P001", "ball_mastery_sole_rolls", forecast_days=30,
        model_scope=MODEL_SCOPE_PLAYER_SPECIFIC,
    )

    assert prediction.player_id == "P001"
    assert prediction.model_scope == MODEL_SCOPE_PLAYER_SPECIFIC
    assert prediction.current_score == 1 + (MINIMUM_TRAINING_SAMPLES - 1)
    assert prediction.training_sample_count == MINIMUM_TRAINING_SAMPLES
    assert prediction.confidence_score is None
    assert prediction.lower_bound is None
    assert prediction.upper_bound is None
    assert prediction.forecast_days == 30

    stored = MLPredictionService(db).list_for_player("P001")
    assert len(stored) == 1
    assert stored[0].prediction_id == prediction.prediction_id


def test_predict_population_scope_pools_across_players_own_timelines(db):
    # Each player individually has too few readings for player_specific,
    # but pooled across players (each relative to their OWN first
    # reading) there is enough for a population fit.
    for idx, player_id in enumerate(["P001", "P002", "P003"]):
        make_player(db, player_id)
        for i in range(2):
            add_metric(
                db, player_id, f"{player_id}_A{i}", "ball_mastery_sole_rolls",
                2 + i, date(2026, 1, 1) + timedelta(days=i * 10),
            )

    prediction = MLPredictionService(db).predict(
        "P001", "ball_mastery_sole_rolls", forecast_days=30,
        model_scope=MODEL_SCOPE_POPULATION,
    )

    assert prediction.model_scope == MODEL_SCOPE_POPULATION
    assert prediction.training_sample_count == 6  # 3 players x 2 readings
    # current_score must come from P001's OWN latest reading, never a
    # population average.
    assert prediction.current_score == 3


def test_predict_population_scope_still_requires_the_target_players_own_current_score(db):
    make_player(db, "P001")
    make_player(db, "P002")
    for i in range(MINIMUM_TRAINING_SAMPLES):
        add_metric(db, "P002", f"A{i}", "ball_mastery_sole_rolls", 1 + i, date(2026, 1, 1) + timedelta(days=i * 10))

    # P001 has zero readings of this metric — population data exists
    # (from P002), but there is still no current_score to report for
    # P001 specifically.
    with pytest.raises(NoRecordedMetricError):
        MLPredictionService(db).predict(
            "P001", "ball_mastery_sole_rolls", forecast_days=30,
            model_scope=MODEL_SCOPE_POPULATION,
        )


def test_list_for_player_filters_by_metric_name(db):
    make_player(db, "P001")
    for i in range(MINIMUM_TRAINING_SAMPLES):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + i, date(2026, 1, 1) + timedelta(days=i * 10))
        add_metric(db, "P001", f"B{i}", "yoyo_kids_distance_m", 400 + i * 10, date(2026, 1, 1) + timedelta(days=i * 10))

    service = MLPredictionService(db)
    service.predict("P001", "ball_mastery_sole_rolls", forecast_days=30)
    service.predict("P001", "yoyo_kids_distance_m", forecast_days=30)

    only_ball_mastery = service.list_for_player("P001", metric_name="ball_mastery_sole_rolls")
    assert len(only_ball_mastery) == 1
    assert only_ball_mastery[0].metric_name == "ball_mastery_sole_rolls"
