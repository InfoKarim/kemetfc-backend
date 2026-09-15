from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import PlayerDB, UserDB
from app.services.auth_service import hash_password, utcnow
from main import CSRF_COOKIE_NAME, app


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


def test_record_yoyo_kids_assessment(client):
    response = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["player_id"] == "P001"
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


def test_list_physical_assessments_orders_newest_first(client):
    client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2026-01-01", total_distance_m=500.0),
    )
    client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(test_date="2026-06-01", total_distance_m=560.0),
    )

    response = client.get("/players/P001/physical-assessments")

    assert response.status_code == 200
    dates = [item["test_date"] for item in response.json()["assessments"]]
    assert dates == sorted(dates, reverse=True)


def test_list_physical_assessments_requires_authentication(anonymous_client):
    response = anonymous_client.get("/players/P001/physical-assessments")

    assert response.status_code == 401


def test_delete_physical_assessment(client):
    created = client.post(
        "/players/P001/physical-assessments/yoyo-kids",
        json=yoyo_payload(),
    ).json()

    response = client.delete(
        f"/physical-assessments/{created['assessment_id']}"
    )

    assert response.status_code == 200

    remaining_ids = [
        item["assessment_id"]
        for item in client.get(
            "/players/P001/physical-assessments"
        ).json()["assessments"]
    ]
    assert created["assessment_id"] not in remaining_ids


def test_delete_unknown_physical_assessment_returns_404(client):
    response = client.delete("/physical-assessments/PHYS_NOPE")

    assert response.status_code == 404
