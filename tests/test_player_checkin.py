from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import PlayerCheckInTokenDB, UserDB
from app.services.auth_service import hash_password, hash_session_token, utcnow
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


TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _login_client(username, password, role):
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id=f"CHK_{username.upper()}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        active=True,
        email=f"{username}@example.com" if role == "guardian" else None,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    login_response = client.post("/auth/login", json={"username": username, "password": password})
    assert login_response.status_code == 200
    client.headers.update({"X-CSRF-Token": client.cookies.get(CSRF_COOKIE_NAME)})
    return client


@pytest.fixture(scope="module")
def coach_client():
    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = getattr(app.state, "auth_session_factory", None)
    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = TestingSessionLocal

    client = _login_client("chk.coach", "ChkCoachPassword123!", "coach")

    yield client

    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override
    app.state.auth_session_factory = previous_session_factory


def create_test_player(client, player_id):
    payload = {
        "player_id": player_id,
        "first_name_ar": "لاعب",
        "last_name_ar": "تجربة",
        "first_name_en": "CheckIn",
        "last_name_en": "Test",
        "date_of_birth": "2015-06-01",
        "sex": "male",
        "physical_profile": {
            "height_cm": 140.0, "weight_kg": 35.0, "dominant_foot": "right",
            "speed": 70.0, "acceleration": 72.0, "agility": 68.0, "stamina": 75.0, "strength": 60.0,
        },
        "technical_profile": {
            "ball_control": 70.0, "dribbling": 72.0, "passing": 68.0, "shooting": 65.0, "finishing": 67.0,
        },
        "mental_profile": {
            "decision_making": 70.0, "concentration": 72.0, "composure": 68.0, "positioning": 71.0,
            "vision": 74.0, "awareness": 70.0, "game_reading": 70.0, "coachability": 70.0,
        },
        "match_performance": {
            "minutes_played": 90, "goals": 1, "assists": 1, "shots": 3, "shots_on_target": 2,
            "passes_attempted": 40, "passes_completed": 34, "tackles": 3, "interceptions": 2, "rating": 8.2,
        },
        "tactical_profile": {
            "positioning_spatial_intelligence": 70.0, "attacking_contribution_in_possession": 68.0,
            "attacking_contribution_off_ball": 72.0, "defensive_tactical_contribution": 69.0,
            "transitions": 71.0, "decision_quality": 70.0, "collective_coordination": 68.0, "set_piece_contribution": 65.0,
        },
        "weak_foot_profile": {
            "weak_foot_usage_pct": 20.0, "weak_foot_passing": 60.0, "weak_foot_receiving": 62.0,
            "weak_foot_dribbling": 58.0, "weak_foot_finishing": 55.0,
        },
    }
    response = client.post("/players", json=payload)
    assert response.status_code == 201


def test_mint_then_resolve_returns_player_profile(coach_client):
    create_test_player(coach_client, "CHK_P001")

    mint_response = coach_client.post("/players/CHK_P001/checkin-token")
    assert mint_response.status_code == 200
    token = mint_response.json()["token"]
    assert token  # a real opaque token was returned
    assert "CHK_P001" not in token  # the QR payload never carries the raw player_id

    resolve_response = coach_client.post("/players/checkin-token/resolve", json={"token": token})
    assert resolve_response.status_code == 200
    body = resolve_response.json()
    assert body["player_id"] == "CHK_P001"
    assert body["first_name_en"] == "CheckIn"
    assert body["last_name_en"] == "Test"


def test_resolve_unknown_token_is_invalid(coach_client):
    response = coach_client.post("/players/checkin-token/resolve", json={"token": "not-a-real-token"})
    assert response.status_code == 404
    assert response.json()["reason"] == "invalid"


def test_minting_a_new_token_revokes_the_previous_one(coach_client):
    create_test_player(coach_client, "CHK_P002")

    first_token = coach_client.post("/players/CHK_P002/checkin-token").json()["token"]
    second_token = coach_client.post("/players/CHK_P002/checkin-token").json()["token"]
    assert first_token != second_token

    stale_response = coach_client.post("/players/checkin-token/resolve", json={"token": first_token})
    assert stale_response.status_code == 410
    assert stale_response.json()["reason"] == "revoked"

    fresh_response = coach_client.post("/players/checkin-token/resolve", json={"token": second_token})
    assert fresh_response.status_code == 200
    assert fresh_response.json()["player_id"] == "CHK_P002"


def test_resolve_expired_token(coach_client):
    create_test_player(coach_client, "CHK_P003")
    token = coach_client.post("/players/CHK_P003/checkin-token").json()["token"]

    db = TestingSessionLocal()
    record = (
        db.query(PlayerCheckInTokenDB)
        .filter(PlayerCheckInTokenDB.token_hash == hash_session_token(token))
        .one()
    )
    record.expires_at = utcnow() - timedelta(days=1)
    db.commit()
    db.close()

    response = coach_client.post("/players/checkin-token/resolve", json={"token": token})
    assert response.status_code == 410
    assert response.json()["reason"] == "expired"


def test_guardian_cannot_mint_or_resolve_checkin_tokens(coach_client):
    guardian_client = _login_client("chk.guardian", "ChkGuardianPassword123!", "guardian")
    mint_response = guardian_client.post("/players/CHK_P001/checkin-token")
    assert mint_response.status_code == 403

    resolve_response = guardian_client.post("/players/checkin-token/resolve", json={"token": "anything"})
    assert resolve_response.status_code == 403
