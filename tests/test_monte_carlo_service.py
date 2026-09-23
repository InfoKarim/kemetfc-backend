from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import MLPredictionDB, PlayerAssessmentDB, PlayerDB, PlayerMetricDB
from app.ml_prediction import InsufficientTrainingDataError
from app.monte_carlo_simulation import InsufficientUncertaintyDataError
from app.services.ml_prediction_service import NoRecordedMetricError
from app.services.monte_carlo_service import MonteCarloService

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


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_simulate_raises_when_player_has_no_metric_history(db):
    make_player(db, "P001")
    with pytest.raises(NoRecordedMetricError):
        MonteCarloService(db).simulate("P001", "ball_mastery_sole_rolls", forecast_days=30)


def test_simulate_raises_insufficient_training_data_before_attempting_simulation(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))

    with pytest.raises(InsufficientTrainingDataError):
        MonteCarloService(db).simulate(
            "P001", "ball_mastery_sole_rolls", forecast_days=30, model_scope="player_specific"
        )

    # No prediction should have been created either — the point
    # prediction itself failed its own gate.
    assert db.query(MLPredictionDB).count() == 0


def test_simulate_raises_insufficient_uncertainty_data_but_keeps_the_created_prediction(db):
    make_player(db, "P001")
    # Enough for a Step 5 point prediction (>=5) but not enough for
    # Step 7's walk-forward residuals (needs points beyond the first 5).
    for i in range(5):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + i, date(2026, 1, 1) + timedelta(days=i * 10))

    with pytest.raises(InsufficientUncertaintyDataError):
        MonteCarloService(db).simulate(
            "P001", "ball_mastery_sole_rolls", forecast_days=30, model_scope="player_specific"
        )

    predictions = db.query(MLPredictionDB).all()
    assert len(predictions) == 1
    assert predictions[0].lower_bound is None
    assert predictions[0].upper_bound is None


def test_simulate_succeeds_and_persists_simulation_and_updates_prediction_bounds(db):
    make_player(db, "P001")
    for i in range(9):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + (i % 5), date(2026, 1, 1) + timedelta(days=i * 10))

    simulation = MonteCarloService(db).simulate(
        "P001", "ball_mastery_sole_rolls", forecast_days=30,
        model_scope="player_specific", num_simulations=500, seed=42,
    )

    assert simulation.player_id == "P001"
    assert simulation.metric_name == "ball_mastery_sole_rolls"
    assert simulation.num_simulations == 500
    assert simulation.p05 <= simulation.median <= simulation.p95
    assert simulation.simulation_method == "bootstrap_walk_forward_residual_resampling"
    assert simulation.residual_sample_count == 9 - 5

    prediction = db.get(MLPredictionDB, simulation.prediction_id)
    assert prediction.lower_bound == simulation.p05
    assert prediction.upper_bound == simulation.p95
    assert prediction.confidence_score is None


def test_simulate_is_deterministic_with_fixed_seed(db):
    make_player(db, "P001")
    for i in range(9):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + (i % 5), date(2026, 1, 1) + timedelta(days=i * 10))

    first = MonteCarloService(db).simulate(
        "P001", "ball_mastery_sole_rolls", forecast_days=30,
        model_scope="player_specific", num_simulations=500, seed=42,
    )
    second = MonteCarloService(db).simulate(
        "P001", "ball_mastery_sole_rolls", forecast_days=30,
        model_scope="player_specific", num_simulations=500, seed=42,
    )

    assert first.median == second.median
    assert first.p05 == second.p05
    assert first.p95 == second.p95


def test_list_for_player_filters_by_metric_name(db):
    make_player(db, "P001")
    for i in range(9):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + (i % 5), date(2026, 1, 1) + timedelta(days=i * 10))
        add_metric(db, "P001", f"B{i}", "yoyo_kids_distance_m", 400 + i * 5, date(2026, 1, 1) + timedelta(days=i * 10))

    service = MonteCarloService(db)
    service.simulate("P001", "ball_mastery_sole_rolls", forecast_days=30, model_scope="player_specific", seed=1)
    service.simulate("P001", "yoyo_kids_distance_m", forecast_days=30, model_scope="player_specific", seed=1)

    only_ball_mastery = service.list_for_player("P001", metric_name="ball_mastery_sole_rolls")
    assert len(only_ball_mastery) == 1
    assert only_ball_mastery[0].metric_name == "ball_mastery_sole_rolls"
