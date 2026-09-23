from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.player_assessments as player_assessments_router
from app.database import Base, get_db
from app.db_models import PlayerAssessmentDB, PlayerDB, PlayerMetricDB, UserDB
from app.services.auth_service import hash_password, utcnow
from app.services.player_metric_service import PlayerMetricService
from main import CSRF_COOKIE_NAME, app

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402


def make_test_video_bytes(seconds: float = 2.0, fps: float = 10.0) -> bytes:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "clip.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, fps, (64, 48))
        for i in range(int(seconds * fps)):
            writer.write(np.full((48, 64, 3), i % 256, dtype=np.uint8))
        writer.release()
        return path.read_bytes()


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


TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
)

Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()


PLAYER_PROFILE_KWARGS = dict(
    physical_profile={
        "height_cm": 140.0,
        "weight_kg": 35.0,
        "dominant_foot": "right",
        "speed": 70.0,
        "acceleration": 72.0,
        "agility": 68.0,
        "stamina": 75.0,
        "strength": 60.0,
    },
    technical_profile={
        "ball_control": 70.0,
        "dribbling": 72.0,
        "passing": 68.0,
        "shooting": 65.0,
        "finishing": 67.0,
    },
    mental_profile={
        "decision_making": 70.0,
        "concentration": 72.0,
        "composure": 68.0,
        "positioning": 71.0,
        "vision": 74.0,
        "awareness": 70.0,
        "game_reading": 70.0,
        "coachability": 70.0,
    },
    match_performance={
        "minutes_played": 0,
        "goals": 0,
        "assists": 0,
        "shots": 0,
        "shots_on_target": 0,
        "passes_attempted": 0,
        "passes_completed": 0,
        "tackles": 0,
        "interceptions": 0,
        "rating": 0.0,
    },
)


@pytest.fixture(scope="module")
def client():
    """Authenticated TestClient wired to this module's own in-memory DB
    and seeded with one player, scoped to setup/teardown so it doesn't
    leak into other test modules sharing the process-wide `app` object.
    """
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id="TEST_ADMIN",
        username="testadmin",
        password_hash=hash_password("TestAdminPassword123!"),
        role="admin",
        active=True,
        created_at=now,
        updated_at=now,
    ))
    db.add(PlayerDB(
        player_id="P001",
        first_name_ar="كريم",
        last_name_ar="السيد",
        first_name_en="Karim",
        last_name_en="Elsayed",
        date_of_birth=date(2015, 5, 10),
        sex="male",
        **PLAYER_PROFILE_KWARGS,
    ))
    db.commit()
    db.close()

    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = app.state.auth_session_factory

    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = TestingSessionLocal

    test_client = TestClient(app)
    login_response = test_client.post(
        "/auth/login",
        json={
            "username": "testadmin",
            "password": "TestAdminPassword123!",
        },
    )
    assert login_response.status_code == 200
    test_client.headers.update({
        "X-CSRF-Token": test_client.cookies.get(CSRF_COOKIE_NAME),
    })

    yield test_client

    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override

    app.state.auth_session_factory = previous_session_factory


@pytest.fixture
def anonymous_client(client):
    return TestClient(app)


def yoyo_payload(**overrides):
    payload = {
        "test_date": "2026-09-01",
        "level": 12,
        "shuttle": 3,
        "total_distance_m": 640.0,
        "notes": "Good effort, cool weather.",
    }
    payload.update(overrides)
    return payload


def ball_mastery_payload(**overrides):
    payload = {
        "test_date": "2026-09-01",
        "sole_rolls": 4,
        "inside_outside_cuts": 3,
        "l_turn": 3,
        "drag_back": 2,
        "notes": "Head up on sole rolls, still watching the ball on cuts.",
    }
    payload.update(overrides)
    return payload


# --- Yo-Yo Kids (physical / endurance) ---------------------------------


def test_record_yoyo_kids_assessment(client):
    response = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["player_id"] == "P001"
    assert body["pillar"] == "physical"
    assert body["test_category"] == "endurance"
    assert body["test_type"] == "yoyo_kids"
    assert body["raw_data"] == {
        "level": 12,
        "shuttle": 3,
        "total_distance_m": 640.0,
    }
    assert body["calculated_metrics"] == {}
    assert body["age_at_assessment_years"] == 11
    assert body["recorded_by_user_id"] == "TEST_ADMIN"
    assert "vo2max" not in str(body).lower()


def test_record_yoyo_kids_requires_authentication(anonymous_client):
    response = anonymous_client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )

    assert response.status_code == 401


def test_record_yoyo_kids_rejects_unknown_player(client):
    response = client.post(
        "/players/NOPE/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )

    assert response.status_code == 404


def test_record_yoyo_kids_rejects_future_test_date(client):
    response = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2099-01-01"),
    )

    assert response.status_code == 422


def test_record_yoyo_kids_rejects_negative_distance(client):
    response = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(total_distance_m=-5),
    )

    assert response.status_code == 422


# --- Ball Mastery (technical) -------------------------------------------


def test_record_ball_mastery_assessment(client):
    response = client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["player_id"] == "P001"
    assert body["pillar"] == "technical"
    assert body["test_category"] == "ball_mastery"
    assert body["test_type"] == "ball_mastery"
    assert body["raw_data"] == {
        "sole_rolls": 4,
        "inside_outside_cuts": 3,
        "l_turn": 3,
        "drag_back": 2,
    }
    assert body["calculated_metrics"] == {}
    assert body["ai_assisted"] is False


def test_record_ball_mastery_can_be_marked_ai_assisted(client):
    response = client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(ai_assisted=True),
    )

    assert response.status_code == 201
    assert response.json()["ai_assisted"] is True


def test_record_ball_mastery_requires_authentication(anonymous_client):
    response = anonymous_client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(),
    )

    assert response.status_code == 401


def test_record_ball_mastery_rejects_out_of_range_rating(client):
    response = client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(sole_rolls=6),
    )

    assert response.status_code == 422


# --- Cross-pillar listing -------------------------------------------------


def test_list_assessments_orders_newest_first_across_pillars(client):
    client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2026-01-01", total_distance_m=500.0),
    )
    client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-06-01"),
    )

    response = client.get("/players/P001/assessments")

    assert response.status_code == 200
    dates = [item["test_date"] for item in response.json()["assessments"]]
    assert dates == sorted(dates, reverse=True)


def test_list_assessments_can_filter_by_pillar(client):
    client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )
    client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(),
    )

    response = client.get("/players/P001/assessments?pillar=technical")

    assert response.status_code == 200
    pillars = {item["pillar"] for item in response.json()["assessments"]}
    assert pillars == {"technical"}


def test_list_assessments_requires_authentication(anonymous_client):
    response = anonymous_client.get("/players/P001/assessments")

    assert response.status_code == 401


# --- Delete ---------------------------------------------------------------


def test_delete_player_assessment(client):
    created = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    ).json()

    response = client.delete(
        f"/player-assessments/{created['assessment_id']}"
    )

    assert response.status_code == 200

    remaining_ids = [
        item["assessment_id"]
        for item in client.get(
            "/players/P001/assessments"
        ).json()["assessments"]
    ]
    assert created["assessment_id"] not in remaining_ids


def test_delete_unknown_player_assessment_returns_404(client):
    response = client.delete("/player-assessments/ASSESS_NOPE")

    assert response.status_code == 404


# --- PlayerMetricDB (normalized, trend-queryable metric rows) -------------


def test_record_yoyo_kids_creates_a_real_distance_metric_not_an_invented_score(client):
    created = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(total_distance_m=640.0),
    ).json()

    metrics = PlayerMetricService(TestingSessionLocal()).list_for_player("P001")
    yoyo_metrics = [m for m in metrics if m.assessment_id == created["assessment_id"]]

    # Exactly one metric: the raw, objective, measured distance — never a
    # fabricated "fitness score" (no validated pediatric conversion exists,
    # see player_assessment_service.py).
    assert len(yoyo_metrics) == 1
    assert yoyo_metrics[0].metric_name == "yoyo_kids_distance_m"
    assert yoyo_metrics[0].category == "physical"
    assert yoyo_metrics[0].metric_score == 640.0
    assert yoyo_metrics[0].source == "field_test"


def test_record_ball_mastery_creates_four_skill_metrics(client):
    created = client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(
            sole_rolls=4, inside_outside_cuts=3, l_turn=3, drag_back=2
        ),
    ).json()

    metrics = PlayerMetricService(TestingSessionLocal()).list_for_player("P001")
    ball_mastery_metrics = {
        m.metric_name: m for m in metrics if m.assessment_id == created["assessment_id"]
    }

    assert set(ball_mastery_metrics) == {
        "ball_mastery_sole_rolls",
        "ball_mastery_inside_outside_cuts",
        "ball_mastery_l_turn",
        "ball_mastery_drag_back",
    }
    assert ball_mastery_metrics["ball_mastery_sole_rolls"].metric_score == 4
    assert ball_mastery_metrics["ball_mastery_drag_back"].metric_score == 2
    assert all(m.category == "technical" for m in ball_mastery_metrics.values())
    assert all(m.source == "coach_rating" for m in ball_mastery_metrics.values())


def test_record_ball_mastery_ai_assisted_marks_metric_source(client):
    created = client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(ai_assisted=True),
    ).json()

    metrics = PlayerMetricService(TestingSessionLocal()).list_for_player("P001")
    assessment_metrics = [m for m in metrics if m.assessment_id == created["assessment_id"]]

    assert all(m.source == "ai_assisted" for m in assessment_metrics)


def test_delete_player_assessment_also_deletes_its_metrics(client):
    created = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    ).json()

    response = client.delete(f"/player-assessments/{created['assessment_id']}")
    assert response.status_code == 200

    db = TestingSessionLocal()
    remaining = (
        db.query(PlayerMetricDB)
        .filter(PlayerMetricDB.assessment_id == created["assessment_id"])
        .all()
    )
    assert remaining == []


def test_list_metric_names_returns_distinct_sorted_names(client):
    client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )
    client.post(
        "/players/P001/technical-assessments/ball-mastery",
        json=ball_mastery_payload(),
    )

    names = PlayerMetricService(TestingSessionLocal()).list_metric_names("P001")

    assert names == sorted(names)
    assert "yoyo_kids_distance_m" in names
    assert "ball_mastery_sole_rolls" in names


# --- Development history / trends API (Step 4: chart data) ----------------


def create_extra_player(player_id: str):
    db = TestingSessionLocal()
    db.add(PlayerDB(
        player_id=player_id,
        first_name_ar="ك", last_name_ar="س",
        first_name_en="Extra", last_name_en="Player",
        date_of_birth=date(2015, 5, 10),
        sex="male",
        **PLAYER_PROFILE_KWARGS,
    ))
    db.commit()
    db.close()


def test_development_history_requires_authentication(anonymous_client):
    response = anonymous_client.get("/players/P001/development-history")
    assert response.status_code == 401


def test_development_history_rejects_unknown_player(client):
    response = client.get("/players/NOPE/development-history")
    assert response.status_code == 404


def test_development_history_rejects_invalid_period(client):
    response = client.get(
        "/players/P001/development-history", params={"period": "3 weeks"}
    )
    assert response.status_code == 422


def test_development_history_returns_all_metrics_by_default(client):
    create_extra_player("P_HISTORY")
    client.post(
        "/players/P_HISTORY/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2026-01-01", total_distance_m=500.0),
    )
    client.post(
        "/players/P_HISTORY/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-05"),
    )

    response = client.get("/players/P_HISTORY/development-history")

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "P_HISTORY"
    assert body["period"] == "all"
    assert body["metrics"]["yoyo_kids_distance_m"] == [
        {"recorded_at": "2026-01-01", "score": 500.0}
    ]
    assert body["metrics"]["ball_mastery_sole_rolls"] == [
        {"recorded_at": "2026-01-05", "score": 4}
    ]


def test_development_history_can_filter_to_one_metric(client):
    create_extra_player("P_HISTORY_FILTER")
    client.post(
        "/players/P_HISTORY_FILTER/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2026-01-01"),
    )
    client.post(
        "/players/P_HISTORY_FILTER/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-01"),
    )

    response = client.get(
        "/players/P_HISTORY_FILTER/development-history",
        params={"metric_name": "yoyo_kids_distance_m"},
    )

    body = response.json()
    assert list(body["metrics"].keys()) == ["yoyo_kids_distance_m"]


def test_development_history_period_excludes_old_points(client):
    create_extra_player("P_HISTORY_PERIOD")
    old_date = (date.today() - timedelta(days=400)).isoformat()
    recent_date = (date.today() - timedelta(days=5)).isoformat()
    client.post(
        "/players/P_HISTORY_PERIOD/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date=old_date, total_distance_m=300.0),
    )
    client.post(
        "/players/P_HISTORY_PERIOD/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date=recent_date, total_distance_m=600.0),
    )

    response = client.get(
        "/players/P_HISTORY_PERIOD/development-history",
        params={"period": "30d"},
    )

    points = response.json()["metrics"]["yoyo_kids_distance_m"]
    assert len(points) == 1
    assert points[0]["recorded_at"] == recent_date


def test_development_trends_requires_authentication(anonymous_client):
    response = anonymous_client.get("/players/P001/development-trends")
    assert response.status_code == 401


def test_development_trends_rejects_unknown_player(client):
    response = client.get("/players/NOPE/development-trends")
    assert response.status_code == 404


def test_development_trends_returns_direction_for_a_configured_metric(client):
    create_extra_player("P_TRENDS")
    client.post(
        "/players/P_TRENDS/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-01", sole_rolls=2),
    )
    client.post(
        "/players/P_TRENDS/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-02-01", sole_rolls=4),
    )

    response = client.get("/players/P_TRENDS/development-trends")

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "P_TRENDS"
    trend_by_name = {t["metric_name"]: t for t in body["trends"]}
    assert trend_by_name["ball_mastery_sole_rolls"]["direction"] == "improving"
    assert trend_by_name["ball_mastery_sole_rolls"]["current_score"] == 4


def test_development_trends_lists_metrics_with_no_configured_threshold(client):
    create_extra_player("P_TRENDS_UNCONFIGURED")
    # Simulates a future metric name that has real recorded data but no
    # defensible minimum_meaningful_change yet — inserted directly since
    # no wired assessment type produces one today.
    db = TestingSessionLocal()
    db.add(PlayerAssessmentDB(
        assessment_id="ASSESS_UNCONFIGURED",
        player_id="P_TRENDS_UNCONFIGURED",
        pillar="tactical",
        test_category="vision",
        test_type="vision_rating",
        methodology_version="v1",
        test_date=date(2026, 1, 1),
        age_at_assessment_years=11,
        raw_data={}, calculated_metrics={},
        created_at=datetime.now(),
    ))
    db.commit()
    db.add(PlayerMetricDB(
        player_id="P_TRENDS_UNCONFIGURED",
        assessment_id="ASSESS_UNCONFIGURED",
        metric_name="vision_rating",
        category="tactical",
        metric_score=70,
        recorded_at=date(2026, 1, 1),
        source="coach_rating",
        created_at=datetime.now(),
    ))
    db.commit()
    db.close()

    response = client.get("/players/P_TRENDS_UNCONFIGURED/development-trends")

    body = response.json()
    assert body["metrics_without_trend"] == ["vision_rating"]
    assert "vision_rating" not in {t["metric_name"] for t in body["trends"]}


# --- Predictions API (Step 5: baseline ML model) ---------------------------


def test_create_prediction_requires_authentication(anonymous_client):
    response = anonymous_client.post(
        "/players/P001/predictions",
        json={"metric_name": "ball_mastery_sole_rolls", "forecast_days": 30},
    )
    assert response.status_code == 401


def test_create_prediction_rejects_unknown_player(client):
    response = client.post(
        "/players/NOPE/predictions",
        json={"metric_name": "ball_mastery_sole_rolls", "forecast_days": 30},
    )
    assert response.status_code == 404


def test_create_prediction_rejects_unimplemented_model_scope(client):
    response = client.post(
        "/players/P001/predictions",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "age_group",
        },
    )
    assert response.status_code == 422


def test_create_prediction_returns_422_with_insufficient_data(client):
    create_extra_player("P_PRED_INSUFFICIENT")
    client.post(
        "/players/P_PRED_INSUFFICIENT/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-01"),
    )

    response = client.post(
        "/players/P_PRED_INSUFFICIENT/predictions",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 422
    assert "Insufficient historical data" in response.json()["detail"]


def test_create_and_list_prediction_end_to_end(client):
    create_extra_player("P_PRED_OK")
    for i in range(5):
        client.post(
            "/players/P_PRED_OK/technical-assessments/ball-mastery",
            json=ball_mastery_payload(
                test_date=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                sole_rolls=min(5, 1 + i),
            ),
        )

    create_response = client.post(
        "/players/P_PRED_OK/predictions",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
        },
    )

    assert create_response.status_code == 201
    body = create_response.json()
    assert body["player_id"] == "P_PRED_OK"
    assert body["model_scope"] == "player_specific"
    assert body["training_sample_count"] == 5
    assert body["confidence_score"] is None
    assert body["lower_bound"] is None
    assert body["upper_bound"] is None

    list_response = client.get("/players/P_PRED_OK/predictions")
    assert list_response.status_code == 200
    predictions = list_response.json()["predictions"]
    assert len(predictions) == 1
    assert predictions[0]["prediction_id"] == body["prediction_id"]


# --- Model validation API (Step 6) -----------------------------------------


def test_model_validation_requires_authentication(anonymous_client):
    response = anonymous_client.get(
        "/players/P001/model-validation",
        params={"metric_name": "ball_mastery_sole_rolls"},
    )
    assert response.status_code == 401


def test_model_validation_rejects_unknown_player(client):
    response = client.get(
        "/players/NOPE/model-validation",
        params={"metric_name": "ball_mastery_sole_rolls"},
    )
    assert response.status_code == 404


def test_model_validation_returns_422_with_insufficient_data(client):
    create_extra_player("P_VALIDATE_INSUFFICIENT")
    client.post(
        "/players/P_VALIDATE_INSUFFICIENT/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-01"),
    )

    response = client.get(
        "/players/P_VALIDATE_INSUFFICIENT/model-validation",
        params={
            "metric_name": "ball_mastery_sole_rolls",
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 422
    assert "Insufficient historical data" in response.json()["detail"]


def test_model_validation_end_to_end(client):
    create_extra_player("P_VALIDATE_OK")
    for i in range(10):
        client.post(
            "/players/P_VALIDATE_OK/technical-assessments/ball-mastery",
            json=ball_mastery_payload(
                test_date=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                sole_rolls=min(5, 1 + (i % 5)),
            ),
        )

    response = client.get(
        "/players/P_VALIDATE_OK/model-validation",
        params={
            "metric_name": "ball_mastery_sole_rolls",
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_sample_count"] == 10
    assert body["train"]["sample_count"] >= 2
    assert body["validation"]["sample_count"] >= 2
    assert body["test"]["sample_count"] >= 2
    assert "overfitting_detected" in body
    assert "data_quality" in body
    # No verdict field anywhere — the API must not derive "the model is
    # good" from R^2 for the client.
    assert "verdict" not in body
    assert "quality_label" not in body


# --- Monte Carlo simulation API (Step 7) ------------------------------


def test_monte_carlo_requires_authentication(anonymous_client):
    response = anonymous_client.post(
        "/players/P001/monte-carlo",
        json={"metric_name": "ball_mastery_sole_rolls", "forecast_days": 30},
    )
    assert response.status_code == 401


def test_monte_carlo_rejects_unknown_player(client):
    response = client.post(
        "/players/NOPE/monte-carlo",
        json={"metric_name": "ball_mastery_sole_rolls", "forecast_days": 30},
    )
    assert response.status_code == 404


def test_monte_carlo_returns_422_with_insufficient_training_data(client):
    create_extra_player("P_MC_INSUFFICIENT")
    client.post(
        "/players/P_MC_INSUFFICIENT/technical-assessments/ball-mastery",
        json=ball_mastery_payload(test_date="2026-01-01"),
    )

    response = client.post(
        "/players/P_MC_INSUFFICIENT/monte-carlo",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 422
    assert "Insufficient historical data" in response.json()["detail"]


def test_monte_carlo_returns_422_with_insufficient_uncertainty_data(client):
    create_extra_player("P_MC_NO_RESIDUALS")
    # Exactly enough for a Step 5 point prediction, not enough for
    # Step 7's walk-forward residuals.
    for i in range(5):
        client.post(
            "/players/P_MC_NO_RESIDUALS/technical-assessments/ball-mastery",
            json=ball_mastery_payload(
                test_date=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                sole_rolls=min(5, 1 + i),
            ),
        )

    response = client.post(
        "/players/P_MC_NO_RESIDUALS/monte-carlo",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 422
    assert "Insufficient validated measurement uncertainty data" in response.json()["detail"]

    # The prediction itself should still have been created.
    predictions = client.get("/players/P_MC_NO_RESIDUALS/predictions").json()["predictions"]
    assert len(predictions) == 1


def test_monte_carlo_end_to_end_and_updates_prediction_bounds(client):
    create_extra_player("P_MC_OK")
    for i in range(9):
        client.post(
            "/players/P_MC_OK/technical-assessments/ball-mastery",
            json=ball_mastery_payload(
                test_date=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                sole_rolls=min(5, 1 + (i % 5)),
            ),
        )

    response = client.post(
        "/players/P_MC_OK/monte-carlo",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
            "num_simulations": 500,
            "seed": 42,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["player_id"] == "P_MC_OK"
    assert body["num_simulations"] == 500
    assert body["p05"] <= body["median"] <= body["p95"]
    assert "uncertainty_level" in body
    assert body["simulation_method"] == "bootstrap_walk_forward_residual_resampling"

    list_response = client.get("/players/P_MC_OK/monte-carlo")
    assert list_response.status_code == 200
    simulations = list_response.json()["simulations"]
    assert len(simulations) == 1
    assert simulations[0]["simulation_id"] == body["simulation_id"]

    predictions = client.get("/players/P_MC_OK/predictions").json()["predictions"]
    prediction = next(p for p in predictions if p["prediction_id"] == body["prediction_id"])
    assert prediction["lower_bound"] == body["p05"]
    assert prediction["upper_bound"] == body["p95"]
    assert prediction["confidence_score"] is None


def test_monte_carlo_default_simulation_count_is_ten_thousand(client):
    create_extra_player("P_MC_DEFAULT_COUNT")
    for i in range(9):
        client.post(
            "/players/P_MC_DEFAULT_COUNT/technical-assessments/ball-mastery",
            json=ball_mastery_payload(
                test_date=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                sole_rolls=min(5, 1 + (i % 5)),
            ),
        )

    response = client.post(
        "/players/P_MC_DEFAULT_COUNT/monte-carlo",
        json={
            "metric_name": "ball_mastery_sole_rolls",
            "forecast_days": 30,
            "model_scope": "player_specific",
        },
    )

    assert response.status_code == 201
    assert response.json()["num_simulations"] == 10_000


# --- AI video analysis (Ball Mastery) --------------------------------------

FAKE_AI_SUGGESTION = {
    "sole_rolls": 4,
    "inside_outside_cuts": 3,
    "l_turn": 3,
    "drag_back": 2,
    "head_up_observed": True,
    "both_feet_observed": False,
    "notes": "Confident on sole rolls, watches the ball on cuts.",
}


def test_analyze_ball_mastery_video_returns_ai_suggestion(client, monkeypatch):
    monkeypatch.setattr(
        player_assessments_router,
        "is_provider_configured",
        lambda provider: True,
    )
    monkeypatch.setattr(
        player_assessments_router,
        "analyze_ball_mastery_video",
        lambda frames: FAKE_AI_SUGGESTION,
    )

    response = client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.mp4", make_test_video_bytes(), "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json() == FAKE_AI_SUGGESTION


def test_analyze_ball_mastery_video_requires_authentication(anonymous_client):
    response = anonymous_client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.mp4", make_test_video_bytes(), "video/mp4")},
    )

    assert response.status_code == 401


def test_analyze_ball_mastery_video_requires_configured_provider(client, monkeypatch):
    monkeypatch.setattr(
        player_assessments_router,
        "is_provider_configured",
        lambda provider: False,
    )

    response = client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.mp4", make_test_video_bytes(), "video/mp4")},
    )

    assert response.status_code == 404


def test_analyze_ball_mastery_video_rejects_unsupported_format(client, monkeypatch):
    monkeypatch.setattr(
        player_assessments_router,
        "is_provider_configured",
        lambda provider: True,
    )

    response = client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.txt", b"not a video", "text/plain")},
    )

    assert response.status_code == 400


def test_analyze_ball_mastery_video_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setattr(
        player_assessments_router,
        "is_provider_configured",
        lambda provider: True,
    )

    response = client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.mp4", b"not actually an mp4 container", "video/mp4")},
    )

    assert response.status_code == 400


def test_analyze_ball_mastery_video_surfaces_ai_error_as_502(client, monkeypatch):
    from app.services.smart_recommendation_service import RecommendationError

    monkeypatch.setattr(
        player_assessments_router,
        "is_provider_configured",
        lambda provider: True,
    )

    def raise_error(frames):
        raise RecommendationError("Could not parse AI video analysis")

    monkeypatch.setattr(
        player_assessments_router, "analyze_ball_mastery_video", raise_error
    )

    response = client.post(
        "/players/P001/technical-assessments/ball-mastery/analyze-video",
        files={"video": ("clip.mp4", make_test_video_bytes(), "video/mp4")},
    )

    assert response.status_code == 502


# --- Development report + coach message -----------------------------------


def _seed_fresh_player(player_id: str) -> None:
    """A dedicated, never-assessed player for report tests, so their
    coverage/strengths reflect only what this test itself records —
    unlike P001, which accumulates assessments across the whole module
    (the `client` fixture's DB is shared for the module's lifetime).
    """
    db = TestingSessionLocal()
    db.add(PlayerDB(
        player_id=player_id,
        first_name_ar="لاعب",
        last_name_ar=player_id,
        first_name_en="Fresh",
        last_name_en=player_id,
        date_of_birth=date(2016, 1, 1),
        sex="male",
        **PLAYER_PROFILE_KWARGS,
    ))
    db.commit()
    db.close()


def test_development_report_has_no_assessments_initially(client):
    _seed_fresh_player("P_REPORT_EMPTY")

    response = client.get("/players/P_REPORT_EMPTY/development-report")

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "P_REPORT_EMPTY"
    assert body["coverage"]["coverage_percent"] == 0
    assert body["strengths"] == []
    assert body["priorities"] == []
    assert body["coach_message"] == {
        "message": None,
        "next_focus": [],
        "updated_at": None,
    }
    assert "score" not in body


def test_development_report_reflects_recorded_assessments(client):
    _seed_fresh_player("P_REPORT_FILLED")
    client.post(
        "/players/P_REPORT_FILLED/technical-assessments/ball-mastery",
        json=ball_mastery_payload(sole_rolls=5, l_turn=5),
    )

    response = client.get("/players/P_REPORT_FILLED/development-report")

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"]["coverage_percent"] == 25
    titles = {s["title"] for s in body["strengths"]}
    assert "Sole Rolls" in titles


def test_development_report_requires_authentication(anonymous_client):
    response = anonymous_client.get("/players/P001/development-report")

    assert response.status_code == 401


def test_coach_message_can_be_set_via_api(client):
    response = client.put(
        "/players/P001/coach-message",
        json={"message": "Great effort this week.", "next_focus": ["Scanning"]},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Great effort this week."

    report = client.get("/players/P001/development-report").json()
    assert report["coach_message"]["message"] == "Great effort this week."
    assert report["coach_message"]["next_focus"] == ["Scanning"]


def test_coach_message_rejects_more_than_three_focus_items(client):
    response = client.put(
        "/players/P001/coach-message",
        json={
            "message": None,
            "next_focus": ["A", "B", "C", "D"],
        },
    )

    assert response.status_code == 422
