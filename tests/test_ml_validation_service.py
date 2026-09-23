from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import PlayerAssessmentDB, PlayerDB, PlayerMetricDB
from app.ml_validation import MINIMUM_SAMPLES_FOR_VALIDATION, InsufficientValidationDataError
from app.services.ml_validation_service import MLValidationService

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


def test_validate_raises_for_unknown_model_scope(db):
    with pytest.raises(ValueError, match="Unknown or unimplemented model_scope"):
        MLValidationService(db).validate("ball_mastery_sole_rolls", model_scope="age_group")


def test_validate_player_specific_requires_a_player_id(db):
    with pytest.raises(ValueError, match="player_id is required"):
        MLValidationService(db).validate(
            "ball_mastery_sole_rolls", model_scope="player_specific", player_id=None
        )


def test_validate_raises_insufficient_data_with_too_few_samples(db):
    make_player(db, "P001")
    add_metric(db, "P001", "A1", "ball_mastery_sole_rolls", 2, date(2026, 1, 1))

    with pytest.raises(InsufficientValidationDataError):
        MLValidationService(db).validate(
            "ball_mastery_sole_rolls", model_scope="player_specific", player_id="P001"
        )


def test_validate_population_pools_across_players(db):
    for idx, player_id in enumerate(["P001", "P002", "P003", "P004", "P005"]):
        make_player(db, player_id)
        for i in range(2):
            add_metric(
                db, player_id, f"{player_id}_A{i}", "ball_mastery_sole_rolls",
                1 + i, date(2026, 1, 1) + timedelta(days=i * 10),
            )

    result = MLValidationService(db).validate("ball_mastery_sole_rolls", model_scope="population")

    assert result.total_sample_count == MINIMUM_SAMPLES_FOR_VALIDATION  # 5 players x 2
    assert result.data_quality in {"limited", "good"}


def test_validate_player_specific_uses_only_that_players_own_history(db):
    make_player(db, "P001")
    make_player(db, "P002")
    for i in range(MINIMUM_SAMPLES_FOR_VALIDATION):
        add_metric(db, "P001", f"A{i}", "ball_mastery_sole_rolls", 1 + i, date(2026, 1, 1) + timedelta(days=i * 10))
    # P002 has none of this metric — must not leak into P001's player-specific validation.
    result = MLValidationService(db).validate(
        "ball_mastery_sole_rolls", model_scope="player_specific", player_id="P001"
    )

    assert result.total_sample_count == MINIMUM_SAMPLES_FOR_VALIDATION
