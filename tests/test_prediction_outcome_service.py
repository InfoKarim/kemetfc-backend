from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import (
    MLPredictionDB,
    PlayerAssessmentDB,
    PlayerDB,
    PlayerMetricDB,
    PredictionOutcomeDB,
)
from app.services.prediction_outcome_service import (
    OUTCOME_MATCH_WINDOW_DAYS,
    PredictionOutcomeService,
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


def make_prediction(
    session,
    prediction_id: str,
    player_id: str,
    metric_name: str,
    predicted_score: float,
    prediction_date: date,
    forecast_days: int,
) -> MLPredictionDB:
    prediction = MLPredictionDB(
        prediction_id=prediction_id,
        player_id=player_id,
        metric_name=metric_name,
        model_scope="player_specific",
        current_score=predicted_score,
        predicted_score=predicted_score,
        prediction_date=prediction_date,
        target_date=prediction_date + timedelta(days=forecast_days),
        forecast_days=forecast_days,
        model_name="ridge_regression",
        model_version="baseline_v1",
        training_sample_count=5,
        confidence_score=None,
        lower_bound=None,
        upper_bound=None,
        created_at=datetime.now(),
    )
    session.add(prediction)
    session.commit()
    return prediction


def add_metric_reading(session, player_id, name, score, recorded_at, assessment_id="A"):
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


def test_reconcile_returns_zero_with_no_predictions(db):
    make_player(db, "P001")
    assert PredictionOutcomeService(db).reconcile("P001") == 0


def test_reconcile_skips_prediction_whose_horizon_has_not_arrived(db):
    make_player(db, "P001")
    make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=date.today() - timedelta(days=5), forecast_days=30,
    )
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 4.0, date.today())

    assert PredictionOutcomeService(db).reconcile("P001") == 0
    assert db.query(PredictionOutcomeDB).count() == 0


def test_reconcile_creates_outcome_with_correct_signed_error(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=40)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    actual_date = prediction.target_date + timedelta(days=2)
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, actual_date)

    created = PredictionOutcomeService(db).reconcile("P001")
    assert created == 1

    outcome = db.query(PredictionOutcomeDB).one()
    assert outcome.prediction_id == "PRED1"
    assert outcome.actual_score == 5.0
    assert outcome.actual_date == actual_date
    assert outcome.prediction_error == 1.0  # actual - predicted = 5 - 4
    assert outcome.absolute_error == 1.0


def test_reconcile_ignores_readings_recorded_before_the_prediction_was_made(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=40)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    # This reading happens to land within the match window of
    # target_date, but it was recorded BEFORE the prediction was even
    # made — it's the training data, not a real future outcome.
    add_metric_reading(
        db, "P001", "ball_mastery_sole_rolls", 99.0,
        prediction.target_date - timedelta(days=100),
        assessment_id="OLD",
    )

    assert PredictionOutcomeService(db).reconcile("P001") == 0


def test_reconcile_ignores_readings_outside_the_match_window(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=60)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    too_far = prediction.target_date + timedelta(days=OUTCOME_MATCH_WINDOW_DAYS + 5)
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, too_far)

    assert PredictionOutcomeService(db).reconcile("P001") == 0


def test_reconcile_picks_the_closest_reading_when_multiple_are_in_window(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=60)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 3.0, prediction.target_date - timedelta(days=5), "A1")
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, prediction.target_date + timedelta(days=1), "A2")

    PredictionOutcomeService(db).reconcile("P001")

    outcome = db.query(PredictionOutcomeDB).one()
    assert outcome.actual_score == 5.0  # the +1 day match, not the -5 day one


def test_reconcile_is_idempotent(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=40)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, prediction.target_date)

    first_run = PredictionOutcomeService(db).reconcile("P001")
    second_run = PredictionOutcomeService(db).reconcile("P001")

    assert first_run == 1
    assert second_run == 0
    assert db.query(PredictionOutcomeDB).count() == 1


def test_list_for_player_reconciles_automatically_and_orders_by_actual_date(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=40)
    prediction = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, prediction.target_date)

    assert db.query(PredictionOutcomeDB).count() == 0
    outcomes = PredictionOutcomeService(db).list_for_player("P001")

    assert len(outcomes) == 1
    assert outcomes[0].prediction_id == "PRED1"


def test_list_for_player_filters_by_metric_name(db):
    make_player(db, "P001")
    prediction_date = date.today() - timedelta(days=40)
    p1 = make_prediction(
        db, "PRED1", "P001", "ball_mastery_sole_rolls", predicted_score=4.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    p2 = make_prediction(
        db, "PRED2", "P001", "yoyo_kids_distance_m", predicted_score=500.0,
        prediction_date=prediction_date, forecast_days=30,
    )
    add_metric_reading(db, "P001", "ball_mastery_sole_rolls", 5.0, p1.target_date, "A1")
    add_metric_reading(db, "P001", "yoyo_kids_distance_m", 520.0, p2.target_date, "A2")

    outcomes = PredictionOutcomeService(db).list_for_player("P001", metric_name="ball_mastery_sole_rolls")

    assert len(outcomes) == 1
    assert outcomes[0].metric_name == "ball_mastery_sole_rolls"
