from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import (
    AuditEventDB,
    DataRecordDB,
    GuardianConsentDB,
    PlayerDB,
    TeamDB,
    UserDB,
    VideoDB,
)
from app.services.auth_service import hash_password, utcnow
from app.services.training_plan_service import TrainingPlanService
from app.services.privacy_service import PrivacyService
from main import CSRF_COOKIE_NAME, app


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def enable_test_foreign_keys(
    dbapi_connection,
    connection_record,
):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
)

Base.metadata.create_all(bind=test_engine)


def seed_test_dependencies():
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
    db.commit()

    db.add_all([
        TeamDB(
            team_id="TEAM001",
            name="TrainingBuddy Home",
            age_group="U10",
            coach_name="Coach Home",
            season_id="2026-2027",
            active=True,
        ),
        TeamDB(
            team_id="TEAM002",
            name="TrainingBuddy Away",
            age_group="U10",
            coach_name="Coach Away",
            season_id="2026-2027",
            active=True,
        ),
    ])
    db.commit()

    player = PlayerDB(
        player_id="P001",
        first_name_ar="كريم",
        last_name_ar="السيد",
        first_name_en="Karim",
        last_name_en="Elsayed",
        date_of_birth=date(2015, 5, 10),
        sex="male",
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
        },
        match_performance={
            "minutes_played": 90,
            "goals": 1,
            "assists": 1,
            "shots": 3,
            "shots_on_target": 2,
            "passes_attempted": 40,
            "passes_completed": 34,
            "tackles": 2,
            "interceptions": 1,
            "rating": 7.5,
        },
    )
    db.add(player)
    db.commit()

    record = DataRecordDB(
        record_id="REC001",
        player_id="P001",
        source_type="video",
        created_at=datetime.now(),
        data_type="match_performance",
        status="completed",
        original_file_path="/data/test.mp4",
        analysis_id="AN_SEED",
        schema_version="1.0",
        created_by="test",
    )
    db.add(record)
    db.commit()

    video = VideoDB(
        video_id="VID001",
        record_id="REC001",
        video_type="match",
        duration_seconds=60.0,
        recorded_at=datetime.now(),
        session_id="SESSION001",
        location_id="LOCATION001",
        capture_device="test-camera",
        resolution="1920x1080",
        frame_rate_fps=30.0,
        file_size_mb=10.0,
        file_format="mp4",
        file_path="/data/test.mp4",
        checksum="test-checksum",
        original_preserved=True,
        ai_processing_status="pending",
        ai_processed_at=None,
        ai_model_version=None,
        ai_confidence_score=None,
        requires_human_review=False,
        review_reason="",
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        analysis_approved=False,
        approved_by=None,
        approved_at=None,
    )
    db.add(video)
    db.commit()
    db.close()


def override_get_db():
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()


seed_test_dependencies()
app.dependency_overrides[get_db] = override_get_db
app.state.auth_session_factory = TestingSessionLocal
client = TestClient(app)
login_response = client.post(
    "/auth/login",
    json={
        "username": "testadmin",
        "password": "TestAdminPassword123!",
    },
)
assert login_response.status_code == 200
client.headers.update({
    "X-CSRF-Token": client.cookies.get(CSRF_COOKIE_NAME),
})

def create_test_player(player_id="P100"):
    player_data = {
        "player_id": player_id,
        "first_name_ar": "كريم",
        "last_name_ar": "السيد",
        "first_name_en": "Karim",
        "last_name_en": "Elsayed",
        "date_of_birth": "2015-05-10",
        "sex": "male",
        "physical_profile": {
            "height_cm": 140.0,
            "weight_kg": 35.0,
            "dominant_foot": "right",
            "speed": 70.0,
            "acceleration": 72.0,
            "agility": 68.0,
            "stamina": 75.0,
            "strength": 60.0,
        },
        "technical_profile": {
            "ball_control": 70.0,
            "dribbling": 72.0,
            "passing": 68.0,
            "shooting": 65.0,
            "finishing": 67.0,
        },
        "mental_profile": {
            "decision_making": 70.0,
            "concentration": 72.0,
            "composure": 68.0,
            "positioning": 71.0,
            "vision": 74.0,
        },
        "match_performance": {
            "minutes_played": 90,
            "goals": 1,
            "assists": 1,
            "shots": 3,
            "shots_on_target": 2,
            "passes_attempted": 40,
            "passes_completed": 34,
            "tackles": 3,
            "interceptions": 2,
            "rating": 8.2,
        },
        "tactical_profile": {
            "game_understanding": 70.0,
            "defensive_positioning": 68.0,
            "off_ball_movement": 72.0,
            "pressing_intensity": 69.0,
        },
    }

    response = client.post("/players", json=player_data)
    assert response.status_code == 201

    return player_data


def grant_video_consent(player_id: str, consent_id: str):
    response = client.post(
        f"/players/{player_id}/guardian-consents",
        json={
            "consent_id": consent_id,
            "guardian_name": "Test Parent",
            "guardian_email": "parent@example.com",
            "verification_method": "signed_form",
            "purposes": ["video_analysis"],
            "expires_at": None,
        },
    )
    assert response.status_code == 201
    return response.json()

def create_test_match(match_id="M100"):
    match_data = {
        "match_id": match_id,
        "competition_id": "COMP001",
        "season_id": "2026",
        "home_team_id": "TEAM001",
        "away_team_id": "TEAM002",
        "match_date": "2026-08-20T20:00:00",
        "venue_id": "VENUE001",
        "status": "scheduled",
        "home_score": None,
        "away_score": None,
    }

    response = client.post("/matches", json=match_data)

    assert response.status_code == 201

    return match_data


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


def test_readiness_check():
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "schema": "not_checked",
    }


def test_production_readiness_rejects_stale_schema(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database schema is not current"}


def test_anonymous_api_request_is_rejected():
    anonymous = TestClient(app)

    response = anonymous.get("/players")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_anonymous_page_redirects_to_login():
    anonymous = TestClient(app)

    response = anonymous.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/dashboard"


def test_anonymous_development_snapshot_redirects_to_login():
    anonymous = TestClient(app)

    response = anonymous.get(
        "/development-snapshot",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/login?next=/development-snapshot"
    )


def test_login_rejects_invalid_password():
    anonymous = TestClient(app)

    response = anonymous.post(
        "/auth/login",
        json={"username": "testadmin", "password": "incorrect"},
    )

    assert response.status_code == 401


def test_repeated_failed_logins_lock_account(monkeypatch):
    monkeypatch.setenv("AUTH_MAX_FAILED_ATTEMPTS", "3")
    create_response = client.post(
        "/auth/users",
        json={
            "username": "lockoutuser",
            "password": "SecureLockoutPassword123!",
            "role": "coach",
        },
    )
    assert create_response.status_code == 201
    login_client = TestClient(app)

    for _ in range(3):
        response = login_client.post(
            "/auth/login",
            json={"username": "lockoutuser", "password": "wrong"},
        )
        assert response.status_code == 401

    locked = login_client.post(
        "/auth/login",
        json={
            "username": "lockoutuser",
            "password": "SecureLockoutPassword123!",
        },
    )

    assert locked.status_code == 401
    assert locked.json() == {"detail": "Invalid username or password"}


def test_authenticated_user_can_read_identity():
    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "testadmin"
    assert response.json()["user"]["role"] == "admin"
    assert "csrf_token" not in response.json()["user"]


def test_mutation_requires_csrf_token():
    authenticated = TestClient(app)
    response = authenticated.post(
        "/auth/login",
        json={
            "username": "testadmin",
            "password": "TestAdminPassword123!",
        },
    )
    assert response.status_code == 200

    response = authenticated.post(
        "/teams",
        json={
            "team_id": "TEAM_CSRF_BLOCKED",
            "name": "Blocked Team",
            "age_group": "U10",
            "coach_name": "Coach",
            "season_id": "2026-2027",
            "active": True,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CSRF token"}


def test_admin_can_create_reviewer_and_reviewer_is_read_only():
    create_response = client.post(
        "/auth/users",
        json={
            "username": "testreviewer",
            "password": "ReviewerPassword123!",
            "role": "reviewer",
        },
    )
    assert create_response.status_code == 201

    reviewer = TestClient(app)
    login = reviewer.post(
        "/auth/login",
        json={
            "username": "testreviewer",
            "password": "ReviewerPassword123!",
        },
    )
    assert login.status_code == 200
    reviewer.headers.update({
        "X-CSRF-Token": reviewer.cookies.get(CSRF_COOKIE_NAME),
    })

    assert reviewer.get("/players").status_code == 200

    response = reviewer.post(
        "/teams",
        json={
            "team_id": "TEAM_REVIEWER_BLOCKED",
            "name": "Blocked Team",
            "age_group": "U10",
            "coach_name": "Coach",
            "season_id": "2026-2027",
            "active": True,
        },
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Write access required"}


def test_logout_revokes_session():
    authenticated = TestClient(app)
    login = authenticated.post(
        "/auth/login",
        json={
            "username": "testadmin",
            "password": "TestAdminPassword123!",
        },
    )
    assert login.status_code == 200
    authenticated.headers.update({
        "X-CSRF-Token": authenticated.cookies.get(CSRF_COOKIE_NAME),
    })

    response = authenticated.post("/auth/logout")

    assert response.status_code == 200
    assert authenticated.get("/auth/me").status_code == 401

def test_create_player():
    player_data = {
        "player_id": "P100",
        "first_name_ar": "كريم",
        "last_name_ar": "السيد",
        "first_name_en": "Karim",
        "last_name_en": "Elsayed",
        "date_of_birth": "2015-05-10",
        "sex": "male",
        "physical_profile": {
            "height_cm": 140.0,
            "weight_kg": 35.0,
            "dominant_foot": "right",
            "speed": 70.0,
            "acceleration": 72.0,
            "agility": 68.0,
            "stamina": 75.0,
            "strength": 60.0,
        },
        "technical_profile": {
            "ball_control": 70.0,
            "dribbling": 72.0,
            "passing": 68.0,
            "shooting": 65.0,
            "finishing": 67.0,
        },
        "mental_profile": {
            "decision_making": 70.0,
            "concentration": 72.0,
            "composure": 68.0,
            "positioning": 71.0,
            "vision": 74.0,
        },
        "match_performance": {
            "minutes_played": 90,
            "goals": 1,
            "assists": 1,
            "shots": 3,
            "shots_on_target": 2,
            "passes_attempted": 40,
            "passes_completed": 34,
            "tackles": 3,
            "interceptions": 2,
            "rating": 8.2,
        },
        "tactical_profile": {
            "game_understanding": 70.0,
            "defensive_positioning": 68.0,
            "off_ball_movement": 72.0,
            "pressing_intensity": 69.0,
        },
    }

    response = client.post("/players", json=player_data)

    assert response.status_code == 201
    assert response.json()["player_id"] == "P100"
    assert response.json()["first_name_en"] == "Karim"

def test_get_player():
    create_test_player("P200")

    response = client.get("/players/P200")

    assert response.status_code == 200
    assert response.json()["player_id"] == "P200"
    assert response.json()["first_name_en"] == "Karim"


def test_get_unknown_player_returns_404():
    response = client.get("/players/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}

def test_get_all_players():
    create_test_player("P300")
    create_test_player("P301")

    response = client.get("/players")

    assert response.status_code == 200

    players = response.json()
    player_ids = [player["player_id"] for player in players]

    assert "P300" in player_ids
    assert "P301" in player_ids

def test_update_player():
    player_data = create_test_player("P400")
    player_data["first_name_en"] = "Updated"
    player_data["technical_profile"]["shooting"] = 80.0

    response = client.put(
        "/players/P400",
        json=player_data,
    )

    assert response.status_code == 200
    assert response.json()["player_id"] == "P400"
    assert response.json()["first_name_en"] == "Updated"
    assert response.json()["technical_profile"]["shooting"] == 80.0


def test_update_unknown_player_returns_404():
    player_data = create_test_player("P401")

    response = client.put(
        "/players/DOES_NOT_EXIST",
        json=player_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
def test_delete_player():
    create_test_player("P500")

    response = client.delete("/players/P500")

    assert response.status_code == 200
    assert response.json() == {"message": "Player deleted"}

    get_response = client.get("/players/P500")
    assert get_response.status_code == 404


def test_delete_unknown_player_returns_404():
    response = client.delete("/players/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}

def test_create_match():
    match_data = create_test_match("M200")

    assert match_data["match_id"] == "M200"

def test_get_match():
    create_test_match("M300")

    response = client.get("/matches/M300")

    assert response.status_code == 200
    assert response.json()["match_id"] == "M300"

def test_get_unknown_match_returns_404():
    response = client.get("/matches/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Match not found"}
def test_get_all_matches():
    create_test_match("M400")
    create_test_match("M401")

    response = client.get("/matches")

    assert response.status_code == 200

    match_ids = [match["match_id"] for match in response.json()]

    assert "M400" in match_ids
    assert "M401" in match_ids

def test_update_match():
    match_data = create_test_match("M500")

    match_data["status"] = "completed"
    match_data["home_score"] = 2
    match_data["away_score"] = 1

    response = client.put(
        "/matches/M500",
        json=match_data,
    )

    assert response.status_code == 200
    assert response.json()["match_id"] == "M500"
    assert response.json()["status"] == "completed"
    assert response.json()["home_score"] == 2
    assert response.json()["away_score"] == 1

def test_update_unknown_match_returns_404():
    match_data = {
        "match_id": "UNKNOWN",
        "competition_id": "COMP001",
        "season_id": "2026",
        "home_team_id": "TEAM001",
        "away_team_id": "TEAM002",
        "match_date": "2026-08-20T20:00:00",
        "venue_id": "VENUE001",
        "status": "scheduled",
        "home_score": None,
        "away_score": None,
    }

    response = client.put(
        "/matches/DOES_NOT_EXIST",
        json=match_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Match not found"}

def test_delete_match():
    create_test_match("M600")

    response = client.delete("/matches/M600")

    assert response.status_code == 200
    assert response.json() == {"message": "Match deleted"}

    get_response = client.get("/matches/M600")
    assert get_response.status_code == 404


def test_delete_unknown_match_returns_404():
    response = client.delete("/matches/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Match not found"}

def create_test_analysis(analysis_id="AN100", player_id="P001"):
    analysis_data = {
        "analysis_id": analysis_id,
        "video_id": "VID001",
        "player_id": player_id,
        "created_at": "2026-08-12T10:00:00",
        "analysis_type": "player_performance",
        "model_name": "soccer_player_analyzer",
        "model_version": "1.0",
        "processing_status": "completed",
        "processed_at": "2026-08-12T10:01:00",
        "confidence_score": 0.95,
        "overall_score": 75.0,
        "strengths": [["Stamina", 80.0]],
        "weaknesses": [["Shooting", 65.0]],
        "recommendations": ["Practice shooting technique."],
        "raw_output_path": "/analysis/test.json",
        "requires_human_review": False,
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "approved": False,
        "approved_by": None,
        "approved_at": None,
    }

    response = client.post("/analyses", json=analysis_data)

    assert response.status_code == 201

    return analysis_data

def test_create_analysis():
    analysis_data = create_test_analysis("AN200")

    assert analysis_data["analysis_id"] == "AN200"


def test_smart_recommendations_not_configured_returns_404(monkeypatch):
    import main

    create_test_player("P600")
    analysis_data = create_test_analysis("AN600", player_id="P600")

    monkeypatch.setattr(main, "is_smart_recommendations_configured", lambda: False)

    response = client.get(
        f"/analyses/{analysis_data['analysis_id']}/smart-recommendations"
    )

    assert response.status_code == 404
    assert "not configured" in response.json()["detail"]


def test_smart_recommendations_returns_ai_focus_areas(monkeypatch):
    import main

    create_test_player("P601")
    analysis_data = create_test_analysis("AN601", player_id="P601")

    monkeypatch.setattr(main, "is_smart_recommendations_configured", lambda: True)

    def fake_get_smart_recommendations(**kwargs):
        assert kwargs["weaknesses"] == analysis_data["weaknesses"]
        return [
            {
                "title": "1v1 close control",
                "reason": "Shooting was flagged as a weak area.",
                "search_keywords": "youth soccer close control drills",
                "videos": [
                    {
                        "title": "Close Control Drill",
                        "channel": "Coach Example",
                        "url": "https://www.youtube.com/watch?v=abc123",
                        "thumbnail_url": "https://img.example/abc123.jpg",
                    }
                ],
            }
        ]

    monkeypatch.setattr(
        main, "get_smart_recommendations", fake_get_smart_recommendations
    )

    response = client.get(
        f"/analyses/{analysis_data['analysis_id']}/smart-recommendations"
    )

    assert response.status_code == 200
    focus_areas = response.json()["focus_areas"]
    assert focus_areas[0]["title"] == "1v1 close control"
    assert focus_areas[0]["videos"][0]["url"] == "https://www.youtube.com/watch?v=abc123"


def test_smart_recommendations_upstream_failure_returns_502(monkeypatch):
    import main
    from app.services.smart_recommendation_service import RecommendationError

    create_test_player("P602")
    analysis_data = create_test_analysis("AN602", player_id="P602")

    monkeypatch.setattr(main, "is_smart_recommendations_configured", lambda: True)

    def fake_get_smart_recommendations(**kwargs):
        raise RecommendationError("Anthropic API error (529): overloaded")

    monkeypatch.setattr(
        main, "get_smart_recommendations", fake_get_smart_recommendations
    )

    response = client.get(
        f"/analyses/{analysis_data['analysis_id']}/smart-recommendations"
    )

    assert response.status_code == 502


def test_smart_recommendations_missing_analysis_returns_404():
    response = client.get("/analyses/DOES_NOT_EXIST/smart-recommendations")
    assert response.status_code == 404


def test_profile_suggestions_maps_matching_attributes_from_latest_analysis():
    create_test_player("P610")
    create_test_analysis("AN610", player_id="P610")

    response = client.get("/players/P610/profile-suggestions")

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] == "AN610"
    assert body["suggestions"]["technical_profile"]["shooting"] == 65.0
    assert body["suggestions"]["physical_profile"]["stamina"] == 80.0


def test_profile_suggestions_ignores_unmapped_attributes():
    create_test_player("P611")
    analysis_data = {
        "analysis_id": "AN611",
        "video_id": "VID001",
        "player_id": "P611",
        "created_at": "2026-08-12T10:00:00",
        "analysis_type": "pose_estimation",
        "model_name": "soccer_player_analyzer",
        "model_version": "1.0",
        "processing_status": "completed",
        "processed_at": "2026-08-12T10:01:00",
        "confidence_score": 0.95,
        "overall_score": 75.0,
        "strengths": [{"attribute": "Movement Visibility", "score": 91.0}],
        "weaknesses": [{"attribute": "Knee Symmetry", "score": 60.0}],
        "recommendations": [],
        "raw_output_path": "/analysis/test.json",
        "requires_human_review": False,
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "approved": False,
        "approved_by": None,
        "approved_at": None,
    }
    assert client.post("/analyses", json=analysis_data).status_code == 201

    response = client.get("/players/P611/profile-suggestions")

    assert response.status_code == 200
    assert response.json()["suggestions"] == {}


def test_profile_suggestions_missing_analysis_returns_404():
    create_test_player("P612")

    response = client.get("/players/P612/profile-suggestions")

    assert response.status_code == 404


def test_profile_suggestions_unknown_player_returns_404():
    response = client.get("/players/DOES_NOT_EXIST/profile-suggestions")
    assert response.status_code == 404


def test_create_analysis_with_unknown_player_returns_404():
    analysis_data = {
        "analysis_id": "AN404",
        "video_id": "VID001",
        "player_id": "DOES_NOT_EXIST",
        "created_at": "2026-08-12T10:00:00",
        "analysis_type": "player_performance",
        "model_name": "soccer_player_analyzer",
        "model_version": "1.0",
        "processing_status": "completed",
        "processed_at": "2026-08-12T10:01:00",
        "confidence_score": 0.95,
        "overall_score": 75.0,
        "strengths": [["Stamina", 80.0]],
        "weaknesses": [["Shooting", 65.0]],
        "recommendations": ["Practice shooting technique."],
        "raw_output_path": "/analysis/test.json",
        "requires_human_review": False,
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "approved": False,
        "approved_by": None,
        "approved_at": None,
    }

    response = client.post("/analyses", json=analysis_data)

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
    

def test_get_analysis():
    create_test_analysis("AN300")

    response = client.get("/analyses/AN300")

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "AN300"

def test_get_unknown_analysis_returns_404():
    response = client.get("/analyses/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}

def test_get_all_analyses():
    create_test_analysis("AN400")
    create_test_analysis("AN401")

    response = client.get("/analyses")

    assert response.status_code == 200

    analysis_ids = [
        analysis["analysis_id"]
        for analysis in response.json()
    ]

    assert "AN400" in analysis_ids
    assert "AN401" in analysis_ids


def test_update_analysis():
    analysis_data = create_test_analysis("AN500")

    analysis_data["overall_score"] = 82.5
    analysis_data["confidence_score"] = 0.98

    response = client.put(
        "/analyses/AN500",
        json=analysis_data,
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "AN500"
    assert response.json()["overall_score"] == 82.5
    assert response.json()["confidence_score"] == 0.98

def test_update_analysis():
    analysis_data = create_test_analysis("AN500")

    analysis_data["overall_score"] = 82.5
    analysis_data["confidence_score"] = 0.98

    response = client.put(
        "/analyses/AN500",
        json=analysis_data,
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "AN500"
    assert response.json()["overall_score"] == 82.5
    assert response.json()["confidence_score"] == 0.98

def test_update_unknown_analysis_returns_404():
    analysis_data = create_test_analysis("AN501")

    response = client.put(
        "/analyses/DOES_NOT_EXIST",
        json=analysis_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}

def test_delete_analysis():
    create_test_analysis("AN600")

    response = client.delete("/analyses/AN600")

    assert response.status_code == 200
    assert response.json() == {"message": "Analysis deleted"}

    get_response = client.get("/analyses/AN600")
    assert get_response.status_code == 404


def test_delete_unknown_analysis_returns_404():
    response = client.delete("/analyses/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}
    
def test_get_analyses_by_player():
    create_test_player("P700")
    create_test_player("P999")

    create_test_analysis("AN700", player_id="P700")
    create_test_analysis("AN701", player_id="P700")
    create_test_analysis("AN702", player_id="P999")

    response = client.get("/players/P700/analyses")

    assert response.status_code == 200

    analyses = response.json()
    analysis_ids = [analysis["analysis_id"] for analysis in analyses]

    assert "AN700" in analysis_ids
    assert "AN701" in analysis_ids
    assert "AN702" not in analysis_ids

def test_get_development_plan():
    create_test_player("P800")

    response = client.get("/players/P800/development-plan")

    assert response.status_code == 200

    plan = response.json()

    assert plan["player_id"] == "P800"
    assert plan["player_name"] == "Karim Elsayed"
    assert "age" in plan
    assert "top_strengths" in plan
    assert "top_weaknesses" in plan
    assert "training_recommendations" in plan
    assert "overall_score" in plan
    assert "player_level" in plan
    assert "development_goals" in plan

def test_get_development_plan_unknown_player_returns_404():
    response = client.get(
        "/players/DOES_NOT_EXIST/development-plan"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}



def test_create_drill():
    drill_data = {
        "drill_id": "DRILL_API_CREATE",
        "name": "Scanning Before Receiving",
        "category": "Vision",
        "description": "Player scans before receiving the ball.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/scanning_before_receiving.mp4",
        "active": True,
    }

    response = client.post("/drills", json=drill_data)

    assert response.status_code == 201
    assert response.json()["drill_id"] == "DRILL_API_CREATE"
    assert response.json()["category"] == "Vision"


def test_get_drill():
    drill_data = {
        "drill_id": "DRILL_API_GET",
        "name": "Passing Accuracy",
        "category": "Passing",
        "description": "Practice accurate passing.",
        "min_age": 8,
        "max_age": 15,
        "difficulty": "beginner",
        "duration_minutes": 12,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/passing_accuracy.mp4",
        "active": True,
    }

    create_response = client.post("/drills", json=drill_data)
    assert create_response.status_code == 201

    response = client.get("/drills/DRILL_API_GET")

    assert response.status_code == 200
    assert response.json()["drill_id"] == "DRILL_API_GET"
    assert response.json()["category"] == "Passing"


def test_get_unknown_drill_returns_404():
    response = client.get("/drills/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Drill not found"}


def test_get_all_drills():
    drill_data = {
        "drill_id": "DRILL_API_LIST",
        "name": "Finishing Under Pressure",
        "category": "Finishing",
        "description": "Practice finishing under pressure.",
        "min_age": 9,
        "max_age": 16,
        "difficulty": "intermediate",
        "duration_minutes": 15,
        "equipment": ["ball", "goal"],
        "video_url": "/drills/finishing_under_pressure.mp4",
        "active": True,
    }

    create_response = client.post("/drills", json=drill_data)
    assert create_response.status_code == 201

    response = client.get("/drills")

    assert response.status_code == 200
    assert any(
        drill["drill_id"] == "DRILL_API_LIST"
        for drill in response.json()
    )


def test_update_drill():
    drill_data = {
        "drill_id": "DRILL_API_UPDATE",
        "name": "Basic Vision Drill",
        "category": "Vision",
        "description": "Basic scanning practice.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/basic_vision.mp4",
        "active": True,
    }

    create_response = client.post("/drills", json=drill_data)
    assert create_response.status_code == 201

    drill_data["name"] = "Advanced Vision Drill"
    drill_data["category"] = "Decision Making"
    drill_data["description"] = "Updated full training details."
    drill_data["min_age"] = 9
    drill_data["max_age"] = 16
    drill_data["difficulty"] = "advanced"
    drill_data["duration_minutes"] = 15
    drill_data["equipment"] = ["ball", "cones", "vests"]
    drill_data["video_url"] = "https://example.com/updated-drill"
    drill_data["active"] = False

    response = client.put(
        "/drills/DRILL_API_UPDATE",
        json=drill_data,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Advanced Vision Drill"
    assert response.json()["category"] == "Decision Making"
    assert response.json()["description"] == "Updated full training details."
    assert response.json()["min_age"] == 9
    assert response.json()["max_age"] == 16
    assert response.json()["difficulty"] == "advanced"
    assert response.json()["duration_minutes"] == 15
    assert response.json()["equipment"] == ["ball", "cones", "vests"]
    assert response.json()["video_url"] == "https://example.com/updated-drill"
    assert response.json()["active"] is False


def test_update_unknown_drill_returns_404():
    drill_data = {
        "drill_id": "DOES_NOT_EXIST",
        "name": "Unknown Drill",
        "category": "Vision",
        "description": "Unknown drill.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "video_url": "/drills/unknown.mp4",
        "active": True,
    }

    response = client.put(
        "/drills/DOES_NOT_EXIST",
        json=drill_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Drill not found"}


def test_delete_drill():
    drill_data = {
        "drill_id": "DRILL_API_DELETE",
        "name": "Delete Test Drill",
        "category": "Passing",
        "description": "Drill created for deletion.",
        "min_age": 8,
        "max_age": 14,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "video_url": "/drills/delete_test.mp4",
        "active": True,
    }

    create_response = client.post("/drills", json=drill_data)
    assert create_response.status_code == 201

    response = client.delete("/drills/DRILL_API_DELETE")

    assert response.status_code == 200
    assert response.json() == {"message": "Drill deleted"}

    get_response = client.get("/drills/DRILL_API_DELETE")
    assert get_response.status_code == 404


def test_delete_unknown_drill_returns_404():
    response = client.delete("/drills/DOES_NOT_EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Drill not found"}


def test_rank_recommended_drills():
    vision_drill = {
        "drill_id": "DRILL_RANK_VISION",
        "name": "Vision Drill",
        "category": "Vision",
        "description": "Practice scanning and awareness.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/vision.mp4",
        "active": True,
    }

    passing_drill = {
        "drill_id": "DRILL_RANK_PASSING",
        "name": "Passing Drill",
        "category": "Passing",
        "description": "Practice passing accuracy.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/passing.mp4",
        "active": True,
    }

    assert client.post("/drills", json=vision_drill).status_code == 201
    assert client.post("/drills", json=passing_drill).status_code == 201

    response = client.post(
        "/drills/recommendations",
        json={
            "weakness": "Vision",
            "weakness_score": 50,
            "age": 10,
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": ["ball", "cones"],
        },
    )

    assert response.status_code == 200

    ranked_ids = [
        drill["drill_id"]
        for drill in response.json()
    ]

    assert ranked_ids.index("DRILL_RANK_VISION") < ranked_ids.index(
        "DRILL_RANK_PASSING"
    )


def test_recommended_drills_exclude_inactive():
    inactive_drill = {
        "drill_id": "DRILL_RANK_INACTIVE",
        "name": "Inactive Vision Drill",
        "category": "Vision",
        "description": "Inactive drill.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "video_url": "/drills/inactive.mp4",
        "active": False,
    }

    assert client.post("/drills", json=inactive_drill).status_code == 201

    response = client.post(
        "/drills/recommendations",
        json={
            "weakness": "Vision",
            "weakness_score": 50,
            "age": 10,
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": ["ball"],
        },
    )

    assert response.status_code == 200

    ranked_ids = {
        drill["drill_id"]
        for drill in response.json()
    }

    assert "DRILL_RANK_INACTIVE" not in ranked_ids


def test_recommended_drills_exclude_wrong_age():
    older_player_drill = {
        "drill_id": "DRILL_RANK_OLDER",
        "name": "Advanced Older Player Drill",
        "category": "Vision",
        "description": "Drill for older players.",
        "min_age": 16,
        "max_age": 18,
        "difficulty": "advanced",
        "duration_minutes": 20,
        "equipment": ["ball"],
        "video_url": "/drills/older_players.mp4",
        "active": True,
    }

    assert client.post("/drills", json=older_player_drill).status_code == 201

    response = client.post(
        "/drills/recommendations",
        json={
            "weakness": "Vision",
            "weakness_score": 50,
            "age": 10,
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": ["ball"],
        },
    )

    assert response.status_code == 200

    ranked_ids = {
        drill["drill_id"]
        for drill in response.json()
    }

    assert "DRILL_RANK_OLDER" not in ranked_ids


def test_upload_drill_video(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    metadata = {
        "drill_id": "DRILL_UPLOAD_TEST",
        "name": "Uploaded Vision Drill",
        "category": "Vision",
        "description": "Video upload test.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "active": True,
    }

    response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "vision.mp4",
                b"\x00\x00\x00\x18ftypmp42fake-mp4-content",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["drill_id"] == "DRILL_UPLOAD_TEST"
    assert response.json()["video_url"] == (
        "/uploads/drills/DRILL_UPLOAD_TEST.mp4"
    )

    saved_file = tmp_path / "DRILL_UPLOAD_TEST.mp4"
    assert saved_file.read_bytes() == (
        b"\x00\x00\x00\x18ftypmp42fake-mp4-content"
    )


def test_upload_drill_video_accepts_mov_from_phone(monkeypatch, tmp_path):
    # A drill video recorded on a coach's phone is typically a .mov with no
    # (or a blank) Content-Type header when picked from the Photos library.
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    metadata = {
        "drill_id": "DRILL_UPLOAD_MOV",
        "name": "Uploaded Vision Drill (phone)",
        "category": "Vision",
        "description": "Video upload test from a phone.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "active": True,
    }

    response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "IMG_5678.MOV",
                b"\x00\x00\x00\x18ftypqt  fake-drill-mov-content",
                "",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["video_url"] == (
        "/uploads/drills/DRILL_UPLOAD_MOV.mov"
    )

    saved_file = tmp_path / "DRILL_UPLOAD_MOV.mov"
    assert saved_file.read_bytes() == (
        b"\x00\x00\x00\x18ftypqt  fake-drill-mov-content"
    )


def test_upload_drill_rejects_non_mp4(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    metadata = {
        "drill_id": "DRILL_UPLOAD_INVALID_TYPE",
        "name": "Invalid Upload",
        "category": "Vision",
        "description": "Invalid file type test.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "active": True,
    }

    response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "notes.txt",
                b"not-a-video",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Unsupported video format"
    }
    assert list(tmp_path.iterdir()) == []


def test_upload_drill_rejects_oversized_video(
    monkeypatch,
    tmp_path,
):
    import json
    import app.drill_upload as drill_upload

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        drill_upload,
        "MAX_VIDEO_SIZE_BYTES",
        5,
    )

    metadata = {
        "drill_id": "DRILL_UPLOAD_TOO_LARGE",
        "name": "Large Upload",
        "category": "Vision",
        "description": "Oversized file test.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "active": True,
    }

    response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "large.mp4",
                b"\x00\x00\x00\x18ftypmp42",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Video exceeds the 500 MB limit"
    }
    assert list(tmp_path.iterdir()) == []


def test_upload_drill_rejects_unsafe_drill_id(
    monkeypatch,
    tmp_path,
):
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    metadata = {
        "drill_id": "../unsafe",
        "name": "Unsafe Upload",
        "category": "Vision",
        "description": "Unsafe identifier test.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "active": True,
    }

    response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "unsafe.mp4",
                b"fake-video",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid drill_id for upload"
    }
    assert list(tmp_path.iterdir()) == []


def test_uploaded_drill_video_can_be_retrieved(
    monkeypatch,
    tmp_path,
):
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    metadata = {
        "drill_id": "DRILL_STREAM_TEST",
        "name": "Stream Test Drill",
        "category": "Vision",
        "description": "Video retrieval test.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "active": True,
    }

    upload_response = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "stream.mp4",
                b"\x00\x00\x00\x18ftypmp42retrievable-mp4-content",
                "video/mp4",
            )
        },
    )

    assert upload_response.status_code == 201

    video_response = client.get(
        upload_response.json()["video_url"]
    )

    assert video_response.status_code == 200
    assert video_response.headers["content-type"] == "video/mp4"
    assert video_response.content == (
        b"\x00\x00\x00\x18ftypmp42retrievable-mp4-content"
    )


def test_delete_uploaded_drill_removes_video_file(
    monkeypatch,
    tmp_path,
):
    import json

    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))
    metadata = {
        "drill_id": "DRILL_DELETE_UPLOAD",
        "name": "Delete Uploaded Drill",
        "category": "Passing",
        "description": "Deletion storage test.",
        "min_age": 8,
        "max_age": 14,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "active": True,
    }
    upload = client.post(
        "/drills/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "drill.mp4",
                b"\x00\x00\x00\x18ftypmp42deletable-mp4-content",
                "video/mp4",
            )
        },
    )
    assert upload.status_code == 201

    response = client.delete("/drills/DRILL_DELETE_UPLOAD")

    assert response.status_code == 200
    assert not (tmp_path / "DRILL_DELETE_UPLOAD.mp4").exists()
    assert client.get("/drills/DRILL_DELETE_UPLOAD").status_code == 404


def test_get_missing_uploaded_video_returns_404(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    response = client.get(
        "/uploads/drills/DOES_NOT_EXIST.mp4"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Video not found"}


def test_get_uploaded_video_rejects_invalid_filename(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DRILL_UPLOAD_DIR", str(tmp_path))

    response = client.get(
        "/uploads/drills/not-a-video.txt"
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid video filename"
    }


def test_drill_library_page():
    response = client.get("/drill-library")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Drill Library" in response.text
    assert "Kemet FC" in response.text
    assert 'class="drill-library-page"' in response.text
    assert 'href="/dashboard">Home</a>' in response.text
    assert 'id="analysis-id"' in response.text
    assert "Player analysis" in response.text
    assert '<select\n          id="analysis-id"' in response.text
    assert "Loading analyses..." in response.text
    assert "Latest analysis selected automatically" in response.text
    assert "Create player analysis" in response.text
    assert "getRecommendations.disabled = true" in response.text
    assert 'class="library-hero-inner"' in response.text
    assert 'aria-label="Drill Library actions"' in response.text
    assert 'fetch("/analyses")' in response.text
    assert 'fetch("/players")' in response.text
    assert "player.first_name_en" in response.text
    assert "analysis.created_at" in response.text
    assert 'id="recommendation-form"' in response.text
    assert "Get recommendations" in response.text
    assert 'id="plan-id"' not in response.text
    assert 'id="save-training-plan"' in response.text
    assert "Save training plan" in response.text
    assert "Edit details" in response.text
    assert "Delete training video" in response.text
    assert "object-fit: contain" in response.text
    assert "Age comes from the" in response.text


def test_shared_design_system_is_available():
    response = client.get("/design-system.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--gold: #C9A45D" in response.text
    assert "@media (max-width: 640px)" in response.text


def test_analysis_generates_ranked_drill_recommendations():
    analysis_data = create_test_analysis("AN_DRILL_RECOMMENDATIONS")
    analysis_data["weaknesses"] = [
        {
            "attribute": "Leadership",
            "score": 50,
        }
    ]

    update_response = client.put(
        "/analyses/AN_DRILL_RECOMMENDATIONS",
        json=analysis_data,
    )
    assert update_response.status_code == 200

    drill_data = {
        "drill_id": "DRILL_ANALYSIS_LEADERSHIP",
        "name": "Leadership Communication",
        "category": "Leadership",
        "description": "Practice communication and leadership.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/leadership.mp4",
        "active": True,
    }

    create_response = client.post("/drills", json=drill_data)
    assert create_response.status_code == 201

    response = client.post(
        "/analyses/AN_DRILL_RECOMMENDATIONS/drill-recommendations",
        json={
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": ["ball", "cones"],
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["weakness"] == "Leadership"
    assert (
        response.json()[0]["drills"][0]["drill_id"]
        == "DRILL_ANALYSIS_LEADERSHIP"
    )


def test_missing_analysis_drill_recommendations_returns_404():
    response = client.post(
        "/analyses/DOES_NOT_EXIST/drill-recommendations",
        json={
            "age": 10,
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": ["ball"],
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}


def test_create_training_plan_from_analysis():
    analysis_data = create_test_analysis("AN_TRAINING_PLAN")
    analysis_data["weaknesses"] = [
        {
            "attribute": "Communication",
            "score": 45,
        }
    ]

    update_response = client.put(
        "/analyses/AN_TRAINING_PLAN",
        json=analysis_data,
    )
    assert update_response.status_code == 200

    drill_data = {
        "drill_id": "DRILL_PLAN_COMMUNICATION",
        "name": "Communication Drill",
        "category": "Communication",
        "description": "Practice communication during play.",
        "min_age": 7,
        "max_age": 13,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball", "cones"],
        "video_url": "/drills/communication.mp4",
        "active": True,
    }

    create_drill_response = client.post(
        "/drills",
        json=drill_data,
    )
    assert create_drill_response.status_code == 201

    response = client.post(
        "/analyses/AN_TRAINING_PLAN/training-plans",
        json={
            "plan_id": "PLAN_API_CREATE",
            "player_difficulty": "beginner",
            "target_duration": 30,
            "available_equipment": ["ball", "cones"],
        },
    )

    assert response.status_code == 201
    assert response.json()["plan_id"] == "PLAN_API_CREATE"
    assert response.json()["player_id"] == "P001"
    assert response.json()["analysis_id"] == "AN_TRAINING_PLAN"
    assert (
        response.json()["recommendations"][0]["drills"][0]["drill_id"]
        == "DRILL_PLAN_COMMUNICATION"
    )

    service = TrainingPlanService(db=TestingSessionLocal())
    saved = service.get_plan("PLAN_API_CREATE")

    assert saved is not None
    assert saved.analysis_id == "AN_TRAINING_PLAN"
    assert saved.recommendations == response.json()["recommendations"]


def test_create_training_plan_missing_analysis_returns_404():
    response = client.post(
        "/analyses/DOES_NOT_EXIST/training-plans",
        json={
            "plan_id": "PLAN_MISSING_ANALYSIS",
            "player_difficulty": "beginner",
            "target_duration": 30,
            "available_equipment": ["ball"],
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis not found"}


def test_create_training_plan_rejects_duplicate_id():
    create_test_analysis("AN_PLAN_DUPLICATE")

    payload = {
        "plan_id": "PLAN_DUPLICATE",
        "player_difficulty": "beginner",
        "target_duration": 30,
        "available_equipment": ["ball"],
    }

    first_response = client.post(
        "/analyses/AN_PLAN_DUPLICATE/training-plans",
        json=payload,
    )
    assert first_response.status_code == 201

    second_response = client.post(
        "/analyses/AN_PLAN_DUPLICATE/training-plans",
        json=payload,
    )

    assert second_response.status_code == 409
    assert second_response.json() == {
        "detail": "Training plan already exists"
    }


def test_api_uses_training_buddy_name():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Kemet FC"


def create_saved_training_plan(
    plan_id: str,
    analysis_id: str,
):
    create_test_analysis(analysis_id)

    response = client.post(
        f"/analyses/{analysis_id}/training-plans",
        json={
            "plan_id": plan_id,
            "player_difficulty": "beginner",
            "target_duration": 30,
            "available_equipment": ["ball"],
        },
    )

    assert response.status_code == 201
    return response.json()


def test_get_training_plan():
    create_saved_training_plan(
        "PLAN_API_GET",
        "AN_PLAN_API_GET",
    )

    response = client.get("/training-plans/PLAN_API_GET")

    assert response.status_code == 200
    assert response.json()["plan_id"] == "PLAN_API_GET"
    assert response.json()["status"] == "draft"


def test_get_all_training_plans():
    create_saved_training_plan(
        "PLAN_API_LIST",
        "AN_PLAN_API_LIST",
    )

    response = client.get("/training-plans")

    assert response.status_code == 200
    assert any(
        plan["plan_id"] == "PLAN_API_LIST"
        for plan in response.json()
    )


def test_update_training_plan_status():
    create_saved_training_plan(
        "PLAN_API_STATUS",
        "AN_PLAN_API_STATUS",
    )

    response = client.patch(
        "/training-plans/PLAN_API_STATUS/status",
        json={"status": "active"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "active"

    get_response = client.get(
        "/training-plans/PLAN_API_STATUS"
    )
    assert get_response.json()["status"] == "active"


def test_get_missing_training_plan_returns_404():
    response = client.get(
        "/training-plans/DOES_NOT_EXIST"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Training plan not found"
    }


def test_training_plan_rejects_invalid_status():
    create_saved_training_plan(
        "PLAN_INVALID_STATUS",
        "AN_PLAN_INVALID_STATUS",
    )

    response = client.patch(
        "/training-plans/PLAN_INVALID_STATUS/status",
        json={"status": "unknown"},
    )

    assert response.status_code == 422


def test_training_plans_dashboard_page():
    response = client.get("/training-plans-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Training Plans" in response.text
    assert 'id="plans-list"' in response.text
    assert "/training-plan-details?plan_id=" in response.text


def test_training_plan_details_page():
    response = client.get("/training-plan-details")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Training Plan Details" in response.text
    assert 'id="plan-details"' in response.text


def test_training_buddy_dashboard_page():
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert 'id="dashboard-stats"' in response.text
    assert 'id="recent-assessments"' in response.text
    assert 'id="active-training-plans"' in response.text
    assert 'id="teams-count"' in response.text
    assert 'fetch("/teams")' in response.text
    assert 'class="home-feature next-training"' in response.text
    assert 'id="featured-score"' in response.text
    assert 'class="home-actions"' in response.text
    assert 'aria-label="Mobile navigation"' in response.text


def test_root_opens_home_dashboard():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_players_dashboard_page():
    response = client.get("/players-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Players" in response.text
    assert 'id="players-list"' in response.text
    assert 'id="player-search"' in response.text
    assert 'id="team-filter"' in response.text
    assert 'fetch("/teams")' in response.text


def test_player_details_page():
    response = client.get("/player-details?player_id=P001")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Player Profile" in response.text
    assert 'id="player-profile"' in response.text
    assert 'id="player-assessments"' in response.text
    assert 'id="player-training-plans"' in response.text
    assert 'id="development-snapshot-link"' in response.text
    assert 'fetch("/teams")' in response.text


def test_assessments_dashboard_page():
    response = client.get("/assessments-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Assessments" in response.text
    assert 'id="assessments-list"' in response.text
    assert 'id="assessment-search"' in response.text


def test_assessment_details_page():
    response = client.get(
        "/assessment-details?analysis_id=AN001"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Assessment Details" in response.text
    assert 'id="assessment-overview"' in response.text
    assert 'id="assessment-strengths"' in response.text
    assert 'id="assessment-weaknesses"' in response.text
    assert 'id="assessment-recommendations"' in response.text


def test_add_assessment_page():
    response = client.get("/add-assessment")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Add Player Assessment" in response.text
    assert 'id="assessment-form"' in response.text
    assert 'id="player-id"' in response.text
    assert 'id="analysis-id"' not in response.text
    assert 'id="video-id"' in response.text
    assert "No technical ID entry is needed" in response.text
    assert 'fetch("/videos")' in response.text
    assert 'id="technical-skills"' in response.text


def test_add_player_page():
    response = client.get("/add-player")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Add Player" in response.text
    assert 'id="player-form"' in response.text
    assert 'id="basic-information"' in response.text
    assert 'id="physical-profile"' in response.text
    assert 'id="technical-profile"' in response.text
    assert 'id="mental-profile"' in response.text
    assert 'id="team-id"' in response.text
    assert 'fetch("/teams")' in response.text


def make_video_payload(video_id="VID_API_CREATE"):
    return {
        "video_id": video_id,
        "record_id": "REC001",
        "video_type": "training",
        "duration_seconds": 120.0,
        "recorded_at": "2026-08-16T12:00:00",
        "session_id": "SESSION_API",
        "location_id": "FIELD_01",
        "capture_device": "TrainingBuddy Camera",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "file_size_mb": 25.0,
        "file_format": "mp4",
        "file_path": "/videos/training.mp4",
        "checksum": "video-api-checksum",
        "original_preserved": True,
        "ai_processing_status": "pending",
        "ai_processed_at": None,
        "ai_model_version": None,
        "ai_confidence_score": None,
        "requires_human_review": False,
        "review_reason": "",
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "analysis_approved": False,
        "approved_by": None,
        "approved_at": None,
    }


def test_create_video():
    response = client.post(
        "/videos",
        json=make_video_payload(),
    )

    assert response.status_code == 201
    assert response.json()["video_id"] == "VID_API_CREATE"
    assert response.json()["record_id"] == "REC001"


def test_get_video():
    payload = make_video_payload("VID_API_GET")
    assert client.post("/videos", json=payload).status_code == 201

    response = client.get("/videos/VID_API_GET")

    assert response.status_code == 200
    assert response.json()["video_id"] == "VID_API_GET"


def test_get_all_videos():
    payload = make_video_payload("VID_API_LIST")
    assert client.post("/videos", json=payload).status_code == 201

    response = client.get("/videos")

    assert response.status_code == 200
    assert any(
        video["video_id"] == "VID_API_LIST"
        for video in response.json()
    )


def test_update_video():
    payload = make_video_payload("VID_API_UPDATE")
    assert client.post("/videos", json=payload).status_code == 201

    payload["video_type"] = "match"
    payload["duration_seconds"] = 180.0

    response = client.put(
        "/videos/VID_API_UPDATE",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["video_type"] == "match"
    assert response.json()["duration_seconds"] == 180.0


def test_delete_video():
    payload = make_video_payload("VID_API_DELETE")
    assert client.post("/videos", json=payload).status_code == 201

    response = client.delete("/videos/VID_API_DELETE")

    assert response.status_code == 200
    assert response.json() == {"message": "Video deleted"}

    assert client.get("/videos/VID_API_DELETE").status_code == 404


def test_videos_dashboard_page():
    response = client.get("/videos-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Video Library" in response.text
    assert 'id="videos-list"' in response.text
    assert 'id="video-search"' in response.text
    assert 'id="add-video"' in response.text
    assert "/analysis-jobs" in response.text
    assert "/video-analysis-details?job_id=" in response.text


def test_video_analysis_details_page():
    response = client.get(
        "/video-analysis-details?job_id=JOB_API_LIFECYCLE"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Video Analysis Review" in response.text
    assert 'id="analysis-metrics"' in response.text
    assert 'id="repetitions"' in response.text
    assert 'id="coach-review-form"' in response.text
    assert 'id="quality-status"' in response.text
    assert "Automated scoring withheld" in response.text
    assert "/result" in response.text
    assert "/review" in response.text


def test_add_video_page():
    response = client.get("/add-video")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Add Training Video" in response.text
    assert 'id="training-video-form"' in response.text
    assert 'data-field="video-file"' in response.text
    assert "500 MB" in response.text
    assert "loadDrillForEditing" in response.text
    assert 'data-field="active"' in response.text
    assert 'data-field="video-url"' in response.text
    assert "normalizeVideoUrl" in response.text
    assert "complete video link" in response.text
    assert 'contentType.includes("application/json")' in response.text
    assert "/drills/upload" in response.text


def test_upload_player_video_page_is_separate():
    response = client.get("/upload-player-video")

    assert response.status_code == 200
    assert "Add Player Video" in response.text
    assert 'id="video-form"' in response.text
    assert 'id="player-id"' in response.text
    assert "/analysis-jobs" in response.text
    assert "technical details" in response.text
    assert 'id="session-id"' not in response.text
    assert 'id="checksum"' not in response.text


def test_dashboard_explains_the_primary_workflow():
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Start here" in response.text
    assert "Add the player" in response.text
    assert "Upload a player video" in response.text
    assert "Review the analysis" in response.text
    assert "Build the training plan" in response.text
    assert 'href="/upload-player-video"' in response.text


def test_upload_player_video_creates_record_and_video(
    monkeypatch,
    tmp_path,
):
    import json

    monkeypatch.setenv(
        "VIDEO_STORAGE_BACKEND",
        "local",
    )
    monkeypatch.setenv(
        "PLAYER_VIDEO_UPLOAD_DIR",
        str(tmp_path),
    )

    metadata = {
        "video_id": "VID_PLAYER_UPLOAD",
        "record_id": "REC_PLAYER_UPLOAD",
        "player_id": "P001",
        "video_type": "training",
        "duration_seconds": 90.0,
        "session_id": "SESSION_UPLOAD",
        "location_id": "FIELD_UPLOAD",
        "capture_device": "TrainingBuddy Camera",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "coach",
    }

    grant_video_consent("P001", "CONSENT_UPLOAD_MP4")

    response = client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "player-training.mp4",
                b"\x00\x00\x00\x18ftypmp42fake-player-mp4-content",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["video_id"] == "VID_PLAYER_UPLOAD"
    assert response.json()["record_id"] == "REC_PLAYER_UPLOAD"
    assert response.json()["file_path"] == (
        "/uploads/videos/VID_PLAYER_UPLOAD.mp4"
    )

    saved_file = tmp_path / "VID_PLAYER_UPLOAD.mp4"
    assert saved_file.read_bytes() == (
        b"\x00\x00\x00\x18ftypmp42fake-player-mp4-content"
    )

    get_response = client.get("/videos/VID_PLAYER_UPLOAD")
    assert get_response.status_code == 200

    stream_response = client.get(
        "/uploads/videos/VID_PLAYER_UPLOAD.mp4"
    )
    assert stream_response.status_code == 200
    assert stream_response.content == (
        b"\x00\x00\x00\x18ftypmp42fake-player-mp4-content"
    )


def test_minor_video_upload_accepts_hard_copy_guardian_consent(
    monkeypatch,
    tmp_path,
):
    import json

    create_test_player("P_MINOR_NO_CONSENT")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")
    monkeypatch.setenv("PLAYER_VIDEO_UPLOAD_DIR", str(tmp_path))
    metadata = {
        "video_id": "VID_MINOR_NO_CONSENT",
        "record_id": "REC_MINOR_NO_CONSENT",
        "player_id": "P_MINOR_NO_CONSENT",
        "video_type": "training",
        "duration_seconds": 30.0,
        "session_id": "SESSION_PRIVACY",
        "location_id": "FIELD_PRIVACY",
        "capture_device": "camera",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "coach",
    }

    response = client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "minor.mp4",
                b"\x00\x00\x00\x18ftypmp42private-video",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["video_id"] == "VID_MINOR_NO_CONSENT"
    assert (tmp_path / "VID_MINOR_NO_CONSENT.mp4").exists()


def test_guardian_consent_can_be_withdrawn_and_is_audited():
    create_test_player("P_MINOR_WITHDRAW")
    consent = grant_video_consent(
        "P_MINOR_WITHDRAW",
        "CONSENT_WITHDRAW",
    )

    response = client.delete(
        f"/guardian-consents/{consent['consent_id']}"
    )

    assert response.status_code == 200
    assert response.json()["withdrawn_at"] is not None

    db = TestingSessionLocal()
    actions = [
        event.action
        for event in db.query(AuditEventDB)
        .filter(AuditEventDB.resource_id == "P_MINOR_WITHDRAW")
        .order_by(AuditEventDB.occurred_at)
        .all()
    ]
    db.close()
    assert actions == [
        "guardian_consent_granted",
        "guardian_consent_withdrawn",
    ]


def test_expired_guardian_consent_is_not_active():
    create_test_player("P_MINOR_EXPIRED")
    db = TestingSessionLocal()
    now = utcnow()
    db.add(
        GuardianConsentDB(
            consent_id="CONSENT_EXPIRED",
            player_id="P_MINOR_EXPIRED",
            guardian_name="Test Parent",
            guardian_email="parent@example.com",
            verification_method="signed_form",
            purposes=["video_analysis"],
            granted_at=now - timedelta(days=10),
            expires_at=now - timedelta(days=1),
            withdrawn_at=None,
            recorded_by_user_id="TEST_ADMIN",
        )
    )
    db.commit()

    assert PrivacyService(db=db).has_active_consent(
        "P_MINOR_EXPIRED",
        "video_analysis",
    ) is False
    db.close()


def test_guardian_can_only_access_linked_child_snapshot():
    create_test_player("P_GUARDIAN_LINKED")
    create_test_player("P_GUARDIAN_UNRELATED")
    user_response = client.post(
        "/auth/users",
        json={
            "username": "parent.one",
            "password": "GuardianPassword123!",
            "role": "guardian",
        },
    )
    assert user_response.status_code == 201
    guardian_user_id = user_response.json()["user_id"]

    link_response = client.post(
        "/guardian-player-links",
        json={
            "guardian_user_id": guardian_user_id,
            "player_id": "P_GUARDIAN_LINKED",
        },
    )
    assert link_response.status_code == 201

    guardian_client = TestClient(app)
    login = guardian_client.post(
        "/auth/login",
        json={
            "username": "parent.one",
            "password": "GuardianPassword123!",
        },
    )
    assert login.status_code == 200
    guardian_client.headers.update({
        "X-CSRF-Token": guardian_client.cookies.get(CSRF_COOKIE_NAME),
    })

    children = guardian_client.get("/guardian/children")
    assert children.status_code == 200
    assert [item["player_id"] for item in children.json()] == [
        "P_GUARDIAN_LINKED"
    ]

    snapshot = guardian_client.get(
        "/guardian/children/P_GUARDIAN_LINKED/development-snapshot"
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["player"]["player_id"] == "P_GUARDIAN_LINKED"

    unrelated = guardian_client.get(
        "/guardian/children/P_GUARDIAN_UNRELATED/development-snapshot"
    )
    assert unrelated.status_code == 404

    direct_player_access = guardian_client.get("/players/P_GUARDIAN_LINKED")
    assert direct_player_access.status_code == 403

    mutation = guardian_client.post(
        "/drills",
        json={},
    )
    assert mutation.status_code == 403


def test_guardian_with_video_access_uploads_only_for_linked_child(
    monkeypatch,
    tmp_path,
):
    import json

    create_test_player("P_GUARDIAN_VIDEO")
    create_test_player("P_GUARDIAN_VIDEO_OTHER")
    grant_video_consent("P_GUARDIAN_VIDEO", "CONSENT_GUARDIAN_VIDEO")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")
    monkeypatch.setenv("PLAYER_VIDEO_UPLOAD_DIR", str(tmp_path))

    user_response = client.post(
        "/auth/users",
        json={
            "username": "parent.video",
            "password": "GuardianVideoPassword123!",
            "role": "guardian",
            "feature_permissions": ["videos"],
        },
    )
    assert user_response.status_code == 201

    link_response = client.post(
        "/guardian-player-links",
        json={
            "guardian_user_id": user_response.json()["user_id"],
            "player_id": "P_GUARDIAN_VIDEO",
        },
    )
    assert link_response.status_code == 201

    guardian_client = TestClient(app)
    login = guardian_client.post(
        "/auth/login",
        json={
            "username": "parent.video",
            "password": "GuardianVideoPassword123!",
        },
    )
    assert login.status_code == 200
    guardian_client.headers.update({
        "X-CSRF-Token": guardian_client.cookies.get(CSRF_COOKIE_NAME),
    })

    page = guardian_client.get("/upload-player-video")
    assert page.status_code == 200
    assert '"/guardian/children"' in page.text

    metadata = {
        "video_id": "VID_GUARDIAN_UPLOAD",
        "record_id": "REC_GUARDIAN_UPLOAD",
        "player_id": "P_GUARDIAN_VIDEO",
        "video_type": "training",
        "duration_seconds": 30.0,
        "session_id": "SESSION_GUARDIAN",
        "location_id": "unspecified",
        "capture_device": "Phone upload",
        "resolution": "1280x720",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "guardian",
    }
    upload = guardian_client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "child.mp4",
                b"\x00\x00\x00\x18ftypmp42guardian-video",
                "video/mp4",
            )
        },
    )
    assert upload.status_code == 201

    job = guardian_client.post(
        "/videos/VID_GUARDIAN_UPLOAD/analysis-jobs",
        json={"analysis_type": "pose_estimation", "max_attempts": 3},
    )
    assert job.status_code == 201

    metadata["video_id"] = "VID_GUARDIAN_FORBIDDEN"
    metadata["record_id"] = "REC_GUARDIAN_FORBIDDEN"
    metadata["player_id"] = "P_GUARDIAN_VIDEO_OTHER"
    forbidden = guardian_client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={"video": ("other.mp4", b"private-video", "video/mp4")},
    )
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"] == "Linked child not found"


def test_guardian_exports_child_data_and_requests_deletion():
    create_test_player("P_GUARDIAN_PRIVACY")
    create_test_player("P_GUARDIAN_PRIVACY_OTHER")
    user_response = client.post(
        "/auth/users",
        json={
            "username": "parent.privacy",
            "password": "GuardianPrivacy123!",
            "role": "guardian",
        },
    )
    assert user_response.status_code == 201
    guardian_user_id = user_response.json()["user_id"]

    link_response = client.post(
        "/guardian-player-links",
        json={
            "guardian_user_id": guardian_user_id,
            "player_id": "P_GUARDIAN_PRIVACY",
        },
    )
    assert link_response.status_code == 201

    guardian_client = TestClient(app)
    login = guardian_client.post(
        "/auth/login",
        json={
            "username": "parent.privacy",
            "password": "GuardianPrivacy123!",
        },
    )
    assert login.status_code == 200
    guardian_client.headers.update({
        "X-CSRF-Token": guardian_client.cookies.get(CSRF_COOKIE_NAME),
    })

    export = guardian_client.get(
        "/guardian/children/P_GUARDIAN_PRIVACY/data-export"
    )
    assert export.status_code == 200
    assert export.json()["player"]["player_id"] == "P_GUARDIAN_PRIVACY"
    assert set(export.json()) == {
        "generated_at",
        "player",
        "data_records",
        "videos",
        "analyses",
        "training_plans",
        "consents",
    }
    serialized_export = str(export.json())
    assert "original_file_path" not in serialized_export
    assert "file_path" not in serialized_export
    assert "raw_output_path" not in serialized_export

    unrelated = guardian_client.get(
        "/guardian/children/P_GUARDIAN_PRIVACY_OTHER/data-export"
    )
    assert unrelated.status_code == 404

    deletion = guardian_client.post(
        "/guardian/children/P_GUARDIAN_PRIVACY/deletion-requests",
        json={
            "request_id": "PRIVACY_DELETE_TEST",
            "reason": "The family no longer uses the platform.",
        },
    )
    assert deletion.status_code == 201
    assert deletion.json()["status"] == "pending"

    duplicate = guardian_client.post(
        "/guardian/children/P_GUARDIAN_PRIVACY/deletion-requests",
        json={"request_id": "PRIVACY_DELETE_DUPLICATE"},
    )
    assert duplicate.status_code == 409

    requests = client.get("/privacy-requests")
    assert requests.status_code == 200
    assert any(
        item["request_id"] == "PRIVACY_DELETE_TEST"
        for item in requests.json()
    )

    review = client.patch(
        "/privacy-requests/PRIVACY_DELETE_TEST",
        json={
            "status": "in_review",
            "review_notes": "Identity verification started.",
        },
    )
    assert review.status_code == 200
    assert review.json()["status"] == "in_review"

    second_review = client.patch(
        "/privacy-requests/PRIVACY_DELETE_TEST",
        json={"status": "rejected"},
    )
    assert second_review.status_code == 409

    completed = client.patch(
        "/privacy-requests/PRIVACY_DELETE_TEST",
        json={
            "status": "completed",
            "review_notes": "Identity verified and child data erased.",
        },
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    anonymized = client.get("/players/P_GUARDIAN_PRIVACY")
    assert anonymized.status_code == 200
    assert anonymized.json()["first_name_en"] == "Deleted"
    assert anonymized.json()["physical_profile"]["speed"] == 0.0

    guardian_access_after_deletion = guardian_client.get(
        "/guardian/children/P_GUARDIAN_PRIVACY/data-export"
    )
    assert guardian_access_after_deletion.status_code == 404


def test_upload_player_video_accepts_mov(
    monkeypatch,
    tmp_path,
):
    import json

    monkeypatch.setenv(
        "VIDEO_STORAGE_BACKEND",
        "local",
    )
    monkeypatch.setenv(
        "PLAYER_VIDEO_UPLOAD_DIR",
        str(tmp_path),
    )

    metadata = {
        "video_id": "VID_PLAYER_MOV",
        "record_id": "REC_PLAYER_MOV",
        "player_id": "P001",
        "video_type": "training",
        "duration_seconds": 45.0,
        "session_id": "SESSION_MOV",
        "location_id": "FIELD_MOV",
        "capture_device": "iPhone",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "coach",
    }

    grant_video_consent("P001", "CONSENT_UPLOAD_MOV")

    response = client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "player-training.mov",
                b"\x00\x00\x00\x18ftypqt  fake-player-mov-content",
                "video/quicktime",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["file_format"] == "mov"
    assert response.json()["file_path"] == (
        "/uploads/videos/VID_PLAYER_MOV.mov"
    )

    saved_file = tmp_path / "VID_PLAYER_MOV.mov"
    assert saved_file.read_bytes() == (
        b"\x00\x00\x00\x18ftypqt  fake-player-mov-content"
    )


def test_upload_player_video_accepts_missing_content_type(
    monkeypatch,
    tmp_path,
):
    # Mobile Safari sometimes omits (or blanks) the Content-Type header for
    # videos picked from the phone's Photos library instead of the Files
    # app. The upload must still succeed based on the real file signature.
    import json

    monkeypatch.setenv(
        "VIDEO_STORAGE_BACKEND",
        "local",
    )
    monkeypatch.setenv(
        "PLAYER_VIDEO_UPLOAD_DIR",
        str(tmp_path),
    )

    metadata = {
        "video_id": "VID_PLAYER_NOTYPE",
        "record_id": "REC_PLAYER_NOTYPE",
        "player_id": "P001",
        "video_type": "training",
        "duration_seconds": 45.0,
        "session_id": "SESSION_NOTYPE",
        "location_id": "FIELD_NOTYPE",
        "capture_device": "iPhone",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "coach",
    }

    grant_video_consent("P001", "CONSENT_UPLOAD_NOTYPE")

    response = client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "IMG_1234.MOV",
                b"\x00\x00\x00\x18ftypqt  fake-player-mov-content",
                "",
            )
        },
    )

    assert response.status_code == 201
    assert response.json()["file_format"] == "mov"


def test_remote_player_video_uses_short_lived_redirect(monkeypatch):
    import main

    db = TestingSessionLocal()
    db.add(VideoDB(
        video_id="VID_REMOTE",
        record_id="REC001",
        video_type="training",
        duration_seconds=30.0,
        recorded_at=datetime.now(),
        session_id="SESSION_REMOTE",
        location_id="FIELD_REMOTE",
        capture_device="camera",
        resolution="1920x1080",
        frame_rate_fps=30.0,
        file_size_mb=1.0,
        file_format="mp4",
        file_path="/uploads/videos/VID_REMOTE.mp4",
        checksum="remote-checksum",
        original_preserved=True,
        ai_processing_status="pending",
        ai_processed_at=None,
        ai_model_version=None,
        ai_confidence_score=None,
        requires_human_review=False,
        review_reason="",
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        analysis_approved=False,
        approved_by=None,
        approved_at=None,
    ))
    db.commit()
    db.close()

    class RemoteStorage:
        def local_path(self, filename):
            assert filename == "VID_REMOTE.mp4"
            return None

        def create_download_url(self, filename):
            assert filename == "VID_REMOTE.mp4"
            return "https://storage.example/private/video?signature=test"

    monkeypatch.setattr(
        main,
        "get_video_storage",
        lambda: RemoteStorage(),
    )

    response = client.get(
        "/uploads/videos/VID_REMOTE.mp4",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://storage.example/private/video"
    )


def test_uploaded_video_requires_database_record(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")
    monkeypatch.setenv("PLAYER_VIDEO_UPLOAD_DIR", str(tmp_path))
    (tmp_path / "ORPHAN.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42orphan"
    )

    response = client.get("/uploads/videos/ORPHAN.mp4")

    assert response.status_code == 404
    assert response.json() == {"detail": "Video not found"}


def test_matches_dashboard_page():
    response = client.get("/matches-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Matches" in response.text
    assert 'id="matches-list"' in response.text
    assert 'id="match-search"' in response.text
    assert 'id="add-match"' in response.text
    assert 'fetch("/teams")' in response.text


def test_add_match_page():
    response = client.get("/add-match")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Add Match" in response.text
    assert 'id="match-form"' in response.text
    assert 'id="home-team-id"' in response.text
    assert 'id="away-team-id"' in response.text
    assert 'id="match-status"' in response.text
    assert '<select id="home-team-id"' in response.text
    assert '<select id="away-team-id"' in response.text
    assert 'fetch("/teams")' in response.text


def test_reports_dashboard_page():
    response = client.get("/reports-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Reports" in response.text
    assert 'id="report-player"' in response.text
    assert 'id="report-content"' in response.text
    assert 'id="print-report"' in response.text


def test_calendar_dashboard_page():
    response = client.get("/calendar-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Calendar" in response.text
    assert 'id="calendar-grid"' in response.text
    assert 'id="calendar-title"' in response.text
    assert 'id="calendar-events"' in response.text



def test_create_team():
    team_data = {
        "team_id": "TEAM_API_CREATE",
        "name": "TrainingBuddy U10",
        "age_group": "U10",
        "coach_name": "Coach Ahmed",
        "season_id": "2026-2027",
        "active": True,
    }

    response = client.post("/teams", json=team_data)

    assert response.status_code == 201
    assert response.json()["team_id"] == "TEAM_API_CREATE"
    assert response.json()["age_group"] == "U10"
    assert response.json()["coach_name"] == "Coach Ahmed"



def create_test_team(team_id="TEAM_API_TEST"):
    team_data = {
        "team_id": team_id,
        "name": "TrainingBuddy U12",
        "age_group": "U12",
        "coach_name": "Coach Karim",
        "season_id": "2026-2027",
        "active": True,
    }

    response = client.post("/teams", json=team_data)
    assert response.status_code == 201
    return team_data


def test_get_team():
    create_test_team("TEAM_API_GET")

    response = client.get("/teams/TEAM_API_GET")

    assert response.status_code == 200
    assert response.json()["team_id"] == "TEAM_API_GET"


def test_get_all_teams():
    create_test_team("TEAM_API_LIST")

    response = client.get("/teams")

    assert response.status_code == 200
    assert any(
        team["team_id"] == "TEAM_API_LIST"
        for team in response.json()
    )


def test_update_team():
    team_data = create_test_team("TEAM_API_UPDATE")
    team_data["name"] = "TrainingBuddy Academy U12"
    team_data["coach_name"] = "Coach Omar"

    response = client.put(
        "/teams/TEAM_API_UPDATE",
        json=team_data,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "TrainingBuddy Academy U12"
    assert response.json()["coach_name"] == "Coach Omar"


def test_delete_team():
    create_test_team("TEAM_API_DELETE")

    response = client.delete("/teams/TEAM_API_DELETE")

    assert response.status_code == 200
    assert response.json() == {"message": "Team deleted"}

    get_response = client.get("/teams/TEAM_API_DELETE")
    assert get_response.status_code == 404



def test_get_unknown_team_returns_404():
    response = client.get("/teams/UNKNOWN_TEAM")

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}


def test_update_unknown_team_returns_404():
    team_data = {
        "team_id": "UNKNOWN_TEAM",
        "name": "Unknown Team",
        "age_group": "U10",
        "coach_name": "Coach Unknown",
        "season_id": "2026-2027",
        "active": True,
    }

    response = client.put(
        "/teams/UNKNOWN_TEAM",
        json=team_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}


def test_update_team_rejects_id_mismatch():
    team_data = {
        "team_id": "TEAM_BODY_ID",
        "name": "Mismatch Team",
        "age_group": "U10",
        "coach_name": "Coach Ahmed",
        "season_id": "2026-2027",
        "active": True,
    }

    response = client.put(
        "/teams/TEAM_PATH_ID",
        json=team_data,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Team ID mismatch"}


def test_delete_unknown_team_returns_404():
    response = client.delete("/teams/UNKNOWN_TEAM")

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}



def test_teams_dashboard_page():
    response = client.get("/teams-dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Teams" in response.text
    assert 'id="teams-list"' in response.text
    assert 'id="team-search"' in response.text
    assert 'href="/add-team"' in response.text
    assert "/team-details?team_id=" in response.text



def test_add_team_page():
    response = client.get("/add-team")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Add Team" in response.text
    assert 'id="team-form"' in response.text
    assert 'name="team_id"' not in response.text
    assert 'name="age_group"' in response.text



def test_assign_player_to_team():
    create_test_team("TEAM_PLAYER_LINK")
    player_data = create_test_player("PLAYER_TEAM_LINK")
    player_data["team_id"] = "TEAM_PLAYER_LINK"

    update_response = client.put(
        "/players/PLAYER_TEAM_LINK",
        json=player_data,
    )

    assert update_response.status_code == 200
    assert update_response.json()["team_id"] == "TEAM_PLAYER_LINK"

    get_response = client.get("/players/PLAYER_TEAM_LINK")

    assert get_response.status_code == 200
    assert get_response.json()["team_id"] == "TEAM_PLAYER_LINK"



def test_assign_player_to_unknown_team_returns_404():
    player_data = create_test_player("PLAYER_UNKNOWN_TEAM")
    player_data["team_id"] = "TEAM_DOES_NOT_EXIST"

    response = client.put(
        "/players/PLAYER_UNKNOWN_TEAM",
        json=player_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}


def test_team_details_page():
    response = client.get("/team-details?team_id=TEAM_U10")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Kemet FC" in response.text
    assert "Team Details" in response.text
    assert 'id="team-profile"' in response.text
    assert 'id="team-players"' in response.text
    assert 'id="team-matches"' in response.text
    assert 'fetch("/matches")' in response.text
    assert 'fetch("/teams/"' in response.text
    assert 'fetch("/players")' in response.text
    assert 'id="edit-team-form"' in response.text
    assert 'id="delete-team"' in response.text



def test_delete_team_with_players_returns_409():
    create_test_team("TEAM_WITH_PLAYERS")
    player_data = create_test_player("PLAYER_IN_TEAM")
    player_data["team_id"] = "TEAM_WITH_PLAYERS"

    update_response = client.put(
        "/players/PLAYER_IN_TEAM",
        json=player_data,
    )
    assert update_response.status_code == 200

    response = client.delete("/teams/TEAM_WITH_PLAYERS")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Team still has assigned players"
    }



def test_create_match_rejects_unknown_team():
    match_data = {
        "match_id": "MATCH_UNKNOWN_TEAM",
        "competition_id": "COMP001",
        "season_id": "2026-2027",
        "home_team_id": "TEAM_DOES_NOT_EXIST",
        "away_team_id": "TEAM002",
        "match_date": "2026-09-01T18:00:00",
        "venue_id": "VENUE001",
        "status": "scheduled",
        "home_score": None,
        "away_score": None,
    }

    response = client.post("/matches", json=match_data)

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}



def test_update_match_rejects_unknown_team():
    match_data = create_test_match("MATCH_UPDATE_UNKNOWN_TEAM")
    match_data["home_team_id"] = "TEAM_DOES_NOT_EXIST"

    response = client.put(
        "/matches/MATCH_UPDATE_UNKNOWN_TEAM",
        json=match_data,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Team not found"}


def test_create_and_list_video_analysis_job():
    response = client.post(
        "/videos/VID001/analysis-jobs",
        json={
            "job_id": "JOB_API_CREATE",
            "analysis_type": "pose_estimation",
            "max_attempts": 3,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["progress_percent"] == 0.0
    assert response.json()["attempt_count"] == 0

    list_response = client.get("/videos/VID001/analysis-jobs")

    assert list_response.status_code == 200
    assert any(
        job["job_id"] == "JOB_API_CREATE"
        for job in list_response.json()
    )


def test_video_analysis_job_rejects_unknown_analysis_type():
    response = client.post(
        "/videos/VID001/analysis-jobs",
        json={
            "job_id": "JOB_UNKNOWN_ANALYSIS_TYPE",
            "analysis_type": "unknown_drill",
        },
    )

    assert response.status_code == 422


def test_video_analysis_job_accepts_full_match():
    response = client.post(
        "/videos/VID001/analysis-jobs",
        json={
            "job_id": "JOB_FULL_MATCH",
            "analysis_type": "full_match",
            "target_track_id": 17,
        },
    )

    assert response.status_code == 201
    assert response.json()["analysis_type"] == "full_match"
    assert response.json()["target_track_id"] == 17


def test_video_analysis_job_lifecycle():
    create_response = client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_API_LIFECYCLE"},
    )
    assert create_response.status_code == 201

    processing_response = client.patch(
        "/analysis-jobs/JOB_API_LIFECYCLE",
        json={
            "status": "processing",
            "progress_percent": 25.0,
            "model_name": "pose-baseline",
            "model_version": "0.1.0",
        },
    )

    assert processing_response.status_code == 200
    assert processing_response.json()["status"] == "processing"
    assert processing_response.json()["attempt_count"] == 1

    completed_response = client.patch(
        "/analysis-jobs/JOB_API_LIFECYCLE",
        json={
            "status": "completed",
            "result_path": "/analysis/JOB_API_LIFECYCLE.json",
        },
    )

    assert completed_response.status_code == 200
    assert completed_response.json()["status"] == "completed"
    assert completed_response.json()["progress_percent"] == 100.0
    assert completed_response.json()["completed_at"] is not None


def test_get_and_review_video_analysis_result(monkeypatch, tmp_path):
    job_id = "JOB_API_RESULT_REVIEW"
    result_path = tmp_path / f"{job_id}.json"
    result_path.write_text(
        '{"summary":{"detection_rate":0.9},"features":{}}'
    )
    monkeypatch.setenv("VIDEO_ANALYSIS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")

    assert client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": job_id, "analysis_type": "squat_jump"},
    ).status_code == 201
    assert client.patch(
        f"/analysis-jobs/{job_id}",
        json={"status": "processing"},
    ).status_code == 200
    assert client.patch(
        f"/analysis-jobs/{job_id}",
        json={"status": "completed", "result_path": str(result_path)},
    ).status_code == 200

    result_response = client.get(f"/analysis-jobs/{job_id}/result")

    assert result_response.status_code == 200
    assert result_response.json()["summary"]["detection_rate"] == 0.9

    review_response = client.put(
        f"/analysis-jobs/{job_id}/review",
        json={
            "review_status": "approved",
            "reviewed_by": "Spoofed reviewer",
            "review_notes": "Movement phases match the video.",
        },
    )

    assert review_response.status_code == 200
    assert review_response.json()["review_status"] == "approved"
    assert review_response.json()["reviewed_by"] == "TEST_ADMIN"
    assert review_response.json()["reviewed_at"] is not None


def test_analysis_result_rejects_untrusted_path(monkeypatch, tmp_path):
    job_id = "JOB_API_UNTRUSTED_RESULT"
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    untrusted = tmp_path / f"{job_id}.json"
    untrusted.write_text("{}")
    monkeypatch.setenv("VIDEO_ANALYSIS_OUTPUT_DIR", str(allowed))
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")

    client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": job_id},
    )
    client.patch(
        f"/analysis-jobs/{job_id}",
        json={"status": "processing"},
    )
    client.patch(
        f"/analysis-jobs/{job_id}",
        json={"status": "completed", "result_path": str(untrusted)},
    )

    response = client.get(f"/analysis-jobs/{job_id}/result")

    assert response.status_code == 404


def test_pending_analysis_job_cannot_be_reviewed():
    job_id = "JOB_API_PENDING_REVIEW"
    client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": job_id},
    )

    response = client.put(
        f"/analysis-jobs/{job_id}/review",
        json={
            "review_status": "rejected",
            "reviewed_by": "Coach Ahmed",
        },
    )

    assert response.status_code == 409


def test_video_analysis_job_rejects_invalid_transition():
    create_response = client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_API_INVALID_TRANSITION"},
    )
    assert create_response.status_code == 201

    response = client.patch(
        "/analysis-jobs/JOB_API_INVALID_TRANSITION",
        json={
            "status": "completed",
            "result_path": "/analysis/result.json",
        },
    )

    assert response.status_code == 409


def test_video_analysis_worker_completes_job(tmp_path):
    from app.video_analysis_worker import VideoAnalysisWorker

    class FakePoseAnalyzer:
        model_name = "fake-pose"
        model_version = "test-1"

        def analyze(self, video_path, progress_callback):
            progress_callback(45.0)
            return {
                "frames_processed": 12,
                "frames_with_pose": 10,
                "detection_rate": 10 / 12,
            }

    create_response = client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_WORKER_SUCCESS"},
    )
    assert create_response.status_code == 201

    db = TestingSessionLocal()
    worker = VideoAnalysisWorker(
        db=db,
        analyzer=FakePoseAnalyzer(),
        output_dir=tmp_path,
        video_path_resolver=lambda video: tmp_path / "input.mp4",
    )

    result = worker.process_job("JOB_WORKER_SUCCESS")
    db.close()

    assert result.status == "completed"
    assert result.progress_percent == 100.0
    assert result.model_name == "fake-pose"
    assert (tmp_path / "JOB_WORKER_SUCCESS.json").is_file()

    analysis_response = client.get("/analyses/AN_JOB_WORKER_SUCCESS")
    assert analysis_response.status_code == 200
    assert analysis_response.json()["video_id"] == "VID001"
    assert analysis_response.json()["approved"] is False

    review_response = client.put(
        "/analysis-jobs/JOB_WORKER_SUCCESS/review",
        json={
            "review_status": "approved",
            "review_notes": "Pose result verified against the video.",
        },
    )
    assert review_response.status_code == 200

    approved_analysis = client.get("/analyses/AN_JOB_WORKER_SUCCESS")
    assert approved_analysis.json()["approved"] is True
    assert approved_analysis.json()["approved_by"] == "TEST_ADMIN"

    video_response = client.get("/videos/VID001")
    assert video_response.status_code == 200
    assert video_response.json()["ai_processing_status"] == "completed"
    assert video_response.json()["analysis_approved"] is True


def test_video_analysis_worker_records_failure(tmp_path):
    from app.video_analysis_worker import VideoAnalysisWorker

    class FailingPoseAnalyzer:
        model_name = "failing-pose"
        model_version = "test-1"

        def analyze(self, video_path, progress_callback):
            raise RuntimeError("Pose model unavailable")

    create_response = client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_WORKER_FAILURE"},
    )
    assert create_response.status_code == 201

    db = TestingSessionLocal()
    worker = VideoAnalysisWorker(
        db=db,
        analyzer=FailingPoseAnalyzer(),
        output_dir=tmp_path,
        video_path_resolver=lambda video: tmp_path / "input.mp4",
    )

    result = worker.process_job("JOB_WORKER_FAILURE")
    db.close()

    assert result.status == "failed"
    assert result.error_message == "Pose model unavailable"

    video_response = client.get("/videos/VID001")
    assert video_response.status_code == 200
    assert video_response.json()["ai_processing_status"] == "failed"


def test_player_development_snapshot_combines_profile_and_analysis():
    create_test_player("P_SNAPSHOT")
    create_test_analysis("AN_SNAPSHOT", player_id="P_SNAPSHOT")

    drill_response = client.post(
        "/drills",
        json={
            "drill_id": "DRILL_SNAPSHOT_SHOOTING",
            "name": "Finishing Technique",
            "category": "Shooting",
            "description": "Improve shooting mechanics.",
            "min_age": 7,
            "max_age": 18,
            "difficulty": "beginner",
            "duration_minutes": 10,
            "equipment": ["ball", "goal"],
            "video_url": "/drills/snapshot-shooting.mp4",
            "active": True,
        },
    )
    assert drill_response.status_code == 201

    response = client.get(
        "/players/P_SNAPSHOT/development-snapshot",
        params={
            "player_difficulty": "beginner",
            "target_duration": 10,
            "available_equipment": "ball,goal",
        },
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["player"]["player_id"] == "P_SNAPSHOT"
    assert snapshot["player"]["name"] == "Karim Elsayed"
    assert snapshot["latest_analysis"]["analysis_id"] == "AN_SNAPSHOT"
    assert snapshot["development_focus"] == ["Shooting"]
    assert len(snapshot["ability_profile"]) == 6
    assert snapshot["drill_recommendations"][0]["drills"][0][
        "drill_id"
    ] == "DRILL_SNAPSHOT_SHOOTING"


def test_player_development_snapshot_unknown_player_returns_404():
    response = client.get(
        "/players/DOES_NOT_EXIST/development-snapshot"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}


def test_development_snapshot_page():
    response = client.get("/development-snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Player Development Snapshot" in response.text
    assert 'href="/dashboard"' in response.text
    assert "← Home" in response.text
    assert 'id="snapshot-form"' in response.text
    assert '<label for="player-id">Player</label>' in response.text
    assert '<select id="player-id" required>' in response.text
    assert 'fetch("/players")' in response.text
    assert "Choose a player to begin." in response.text
    assert 'id="ability-chart"' in response.text
    assert 'id="recommended-drills"' in response.text


def test_analysis_development_forecast_uses_saved_weaknesses():
    analysis_data = create_test_analysis("AN_MONTE_CARLO")
    analysis_data["weaknesses"] = [
        {"attribute": "Passing", "score": 55}
    ]
    analysis_data.update({
        "requires_human_review": True,
        "human_review_status": "completed",
        "reviewed_by": "TEST_COACH",
        "reviewed_at": "2026-08-12T10:02:00",
        "approved": True,
        "approved_by": "TEST_ADMIN",
        "approved_at": "2026-08-12T10:03:00",
    })
    update = client.put(
        "/analyses/AN_MONTE_CARLO",
        json=analysis_data,
    )
    assert update.status_code == 200

    response = client.post(
        "/analyses/AN_MONTE_CARLO/development-forecast",
        json={
            "weeks": 6,
            "sessions_per_week": 3,
            "simulations": 500,
            "seed": 12,
        },
    )

    assert response.status_code == 200
    forecast = response.json()
    assert forecast["method"] == "monte_carlo"
    assert forecast["forecasts"][0]["attribute"] == "Passing"
    assert forecast["assumptions"]["total_planned_sessions"] == 18


def test_analysis_development_forecast_unknown_analysis_returns_404():
    response = client.post(
        "/analyses/DOES_NOT_EXIST/development-forecast",
        json={"weeks": 4, "sessions_per_week": 2},
    )

    assert response.status_code == 404


def test_analysis_development_forecast_requires_ai_review():
    analysis_data = create_test_analysis("AN_FORECAST_UNREVIEWED")
    analysis_data.update({
        "weaknesses": [{"attribute": "Vision", "score": 50}],
        "requires_human_review": True,
        "human_review_status": "pending",
    })
    update = client.put(
        "/analyses/AN_FORECAST_UNREVIEWED",
        json=analysis_data,
    )
    assert update.status_code == 200

    response = client.post(
        "/analyses/AN_FORECAST_UNREVIEWED/development-forecast",
        json={"weeks": 4, "sessions_per_week": 2},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Analysis must be approved before forecasting"
    )


def authenticated_role_client(username: str, role: str) -> TestClient:
    create_response = client.post(
        "/auth/users",
        json={
            "username": username,
            "password": "SecureRolePassword123!",
            "role": role,
        },
    )
    assert create_response.status_code == 201

    role_client = TestClient(app)
    login = role_client.post(
        "/auth/login",
        json={
            "username": username,
            "password": "SecureRolePassword123!",
        },
    )
    assert login.status_code == 200
    role_client.headers.update({
        "X-CSRF-Token": role_client.cookies.get(CSRF_COOKIE_NAME),
    })
    return role_client


def test_coach_cannot_self_approve_analysis():
    analysis_data = create_test_analysis("AN_COACH_APPROVAL_BLOCKED")
    coach = authenticated_role_client("approvalcoach", "coach")
    analysis_data.update({
        "requires_human_review": True,
        "human_review_status": "completed",
        "reviewed_by": "SPOOFED_REVIEWER",
        "reviewed_at": "2026-08-12T10:02:00",
        "approved": True,
        "approved_by": "SPOOFED_APPROVER",
        "approved_at": "2026-08-12T10:03:00",
    })

    response = coach.put(
        "/analyses/AN_COACH_APPROVAL_BLOCKED",
        json=analysis_data,
    )

    assert response.status_code == 200
    assert response.json()["approved"] is False
    assert response.json()["reviewed_by"] is None
    assert response.json()["approved_by"] is None

    delete_response = coach.delete("/analyses/AN_COACH_APPROVAL_BLOCKED")
    assert delete_response.status_code == 403
    assert delete_response.json() == {"detail": "Admin access required"}


def test_coach_cannot_transition_or_review_analysis_job():
    assert client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_COACH_SECURITY"},
    ).status_code == 201
    coach = authenticated_role_client("jobcoach", "coach")

    transition = coach.patch(
        "/analysis-jobs/JOB_COACH_SECURITY",
        json={"status": "processing"},
    )
    review = coach.put(
        "/analysis-jobs/JOB_COACH_SECURITY/review",
        json={"review_status": "approved"},
    )

    assert transition.status_code == 403
    assert transition.json() == {"detail": "Admin access required"}
    assert review.status_code == 403
    assert review.json() == {"detail": "Reviewer access required"}


def test_analysis_job_can_only_be_claimed_once():
    from app.services.video_analysis_job_service import (
        VideoAnalysisJobService,
    )

    assert client.post(
        "/videos/VID001/analysis-jobs",
        json={"job_id": "JOB_ATOMIC_CLAIM"},
    ).status_code == 201
    first_db = TestingSessionLocal()
    second_db = TestingSessionLocal()

    try:
        first = VideoAnalysisJobService(db=first_db).claim_job(
            "JOB_ATOMIC_CLAIM"
        )
        second = VideoAnalysisJobService(db=second_db).claim_job(
            "JOB_ATOMIC_CLAIM"
        )
    finally:
        first_db.close()
        second_db.close()

    assert first is not None
    assert first.status == "processing"
    assert first.attempt_count == 1
    assert second is None


def test_unapproved_analysis_cannot_generate_training_guidance():
    analysis_data = create_test_analysis("AN_GUIDANCE_REVIEW_REQUIRED")
    analysis_data.update({
        "requires_human_review": True,
        "human_review_status": "pending",
    })
    assert client.put(
        "/analyses/AN_GUIDANCE_REVIEW_REQUIRED",
        json=analysis_data,
    ).status_code == 200

    recommendation = client.post(
        "/analyses/AN_GUIDANCE_REVIEW_REQUIRED/drill-recommendations",
        json={"age": 10},
    )
    plan = client.post(
        "/analyses/AN_GUIDANCE_REVIEW_REQUIRED/training-plans",
        json={
            "plan_id": "PLAN_UNAPPROVED_BLOCKED",
            "player_difficulty": "beginner",
            "target_duration": 15,
            "available_equipment": [],
        },
    )

    expected = {
        "detail": "Analysis must be approved before generating training guidance"
    }
    assert recommendation.status_code == 409
    assert recommendation.json() == expected
    assert plan.status_code == 409
    assert plan.json() == expected


def test_create_drill_generates_id_when_omitted():
    response = client.post("/drills", json={
        "name": "Automatic ID Drill",
        "category": "Passing",
        "description": "Tests automatic IDs.",
        "min_age": 8,
        "max_age": 12,
        "difficulty": "beginner",
        "duration_minutes": 10,
        "equipment": ["ball"],
        "video_url": "https://example.com/drill.mp4",
        "active": True,
    })

    assert response.status_code == 201
    assert response.json()["drill_id"].startswith("DRILL")


def test_create_team_generates_id_when_omitted():
    response = client.post("/teams", json={
        "name": "Automatic ID Team",
        "age_group": "U10",
        "coach_name": "Coach Auto",
        "season_id": "2026-2027",
        "active": True,
    })

    assert response.status_code == 201
    assert response.json()["team_id"].startswith("TEAM")


def test_admin_user_management_page_is_available():
    response = client.get("/admin/users")

    assert response.status_code == 200
    assert "Account management" in response.text
    assert 'id="create-user-form"' in response.text
    assert "Enabled features" in response.text
    assert "Reset password" in response.text
    assert "Delete account" in response.text


def test_admin_creates_account_with_limited_features():
    response = client.post(
        "/auth/users",
        json={
            "username": "limited.account",
            "password": "LimitedAccountPassword123!",
            "role": "coach",
            "feature_permissions": ["dashboard", "players"],
        },
    )

    assert response.status_code == 201
    assert response.json()["feature_permissions"] == [
        "dashboard",
        "players",
    ]

    limited = TestClient(app)
    login = limited.post(
        "/auth/login",
        json={
            "username": "limited.account",
            "password": "LimitedAccountPassword123!",
        },
    )
    assert login.status_code == 200
    limited.headers.update({
        "X-CSRF-Token": limited.cookies.get(CSRF_COOKIE_NAME),
    })

    assert limited.get("/players").status_code == 200
    denied = limited.get("/teams")
    assert denied.status_code == 403
    assert denied.json() == {
        "detail": "Teams access is not enabled for this account"
    }
    assert limited.get("/teams-dashboard").status_code == 403
    assert limited.get("/admin/users").status_code == 403


def test_admin_can_replace_account_feature_access():
    created = client.post(
        "/auth/users",
        json={
            "username": "editable.features",
            "password": "EditableFeaturesPassword123!",
            "role": "coach",
            "feature_permissions": ["players"],
        },
    )
    assert created.status_code == 201

    response = client.patch(
        f"/auth/users/{created.json()['user_id']}",
        json={"feature_permissions": ["training", "videos"]},
    )

    assert response.status_code == 200
    assert response.json()["feature_permissions"] == ["training", "videos"]


def test_admin_can_delete_unused_account():
    created = client.post(
        "/auth/users",
        json={
            "username": "delete.me",
            "password": "DeleteAccountPassword123!",
            "role": "coach",
            "feature_permissions": ["dashboard"],
        },
    )
    assert created.status_code == 201

    response = client.delete(f"/auth/users/{created.json()['user_id']}")

    assert response.status_code == 200
    assert response.json() == {"message": "Account deleted"}
    assert all(
        user["user_id"] != created.json()["user_id"]
        for user in client.get("/auth/users").json()
    )


def test_admin_cannot_delete_current_account():
    response = client.delete("/auth/users/TEST_ADMIN")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "You cannot delete the account you are currently using"
    }


def test_admin_can_create_and_activate_a_season():
    created = client.post(
        "/seasons",
        json={"name": "2026/2027 Season", "make_active": True},
    )
    assert created.status_code == 201
    season_id = created.json()["season_id"]
    assert created.json()["is_active"] is True

    listed = client.get("/seasons")
    assert listed.status_code == 200
    assert any(season["season_id"] == season_id for season in listed.json())

    other = client.post("/seasons", json={"name": "2027/2028 Season"})
    assert other.status_code == 201

    activated = client.post(f"/seasons/{other.json()['season_id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    refreshed = {
        season["season_id"]: season["is_active"]
        for season in client.get("/seasons").json()
    }
    assert refreshed[season_id] is False
    assert refreshed[other.json()["season_id"]] is True


def test_non_admin_cannot_create_a_season():
    coach = client.post(
        "/auth/users",
        json={
            "username": "season.coach",
            "password": "SeasonCoachPassword123!",
            "role": "coach",
            "feature_permissions": ["dashboard"],
        },
    )
    assert coach.status_code == 201

    coach_client = TestClient(app)
    login = coach_client.post(
        "/auth/login",
        json={
            "username": "season.coach",
            "password": "SeasonCoachPassword123!",
        },
    )
    assert login.status_code == 200
    coach_client.headers.update({
        "X-CSRF-Token": coach_client.cookies.get(CSRF_COOKIE_NAME),
    })

    response = coach_client.post("/seasons", json={"name": "Denied Season"})
    assert response.status_code == 403


def test_creating_an_assessment_notifies_other_coaching_staff():
    other_coach = client.post(
        "/auth/users",
        json={
            "username": "notify.coach",
            "password": "NotifyCoachPassword123!",
            "role": "coach",
            "feature_permissions": ["dashboard", "assessments", "messaging"],
        },
    )
    assert other_coach.status_code == 201
    other_coach_id = other_coach.json()["user_id"]

    other_coach_client = TestClient(app)
    login = other_coach_client.post(
        "/auth/login",
        json={
            "username": "notify.coach",
            "password": "NotifyCoachPassword123!",
        },
    )
    assert login.status_code == 200
    other_coach_client.headers.update({
        "X-CSRF-Token": other_coach_client.cookies.get(CSRF_COOKIE_NAME),
    })

    before = other_coach_client.get("/notifications/unread-count")
    assert before.status_code == 200
    starting_count = before.json()["unread_count"]

    player_id = create_test_player(player_id="P200")["player_id"]
    create_test_analysis(analysis_id="AN_NOTIFY_TEST", player_id=player_id)

    after = other_coach_client.get("/notifications/unread-count")
    assert after.status_code == 200
    assert after.json()["unread_count"] == starting_count + 1

    notifications = other_coach_client.get("/notifications").json()
    assert any(n["type"] == "assessment" for n in notifications)
    assert other_coach_id  # sanity: the recipient account exists


def test_send_and_read_a_message():
    recipient = client.post(
        "/auth/users",
        json={
            "username": "inbox.owner",
            "password": "InboxOwnerPassword123!",
            "role": "coach",
            "feature_permissions": ["dashboard", "messaging"],
        },
    )
    assert recipient.status_code == 201
    recipient_id = recipient.json()["user_id"]

    sent = client.post(
        "/messages",
        json={
            "recipient_id": recipient_id,
            "subject": "Welcome",
            "body": "Glad to have you on the team.",
        },
    )
    assert sent.status_code == 201
    message_id = sent.json()["message_id"]

    recipient_client = TestClient(app)
    login = recipient_client.post(
        "/auth/login",
        json={
            "username": "inbox.owner",
            "password": "InboxOwnerPassword123!",
        },
    )
    assert login.status_code == 200
    recipient_client.headers.update({
        "X-CSRF-Token": recipient_client.cookies.get(CSRF_COOKIE_NAME),
    })

    unread = recipient_client.get("/messages/unread-count")
    assert unread.status_code == 200
    assert unread.json()["unread_count"] == 1

    inbox = recipient_client.get("/messages")
    assert inbox.status_code == 200
    assert any(m["message_id"] == message_id for m in inbox.json())

    marked = recipient_client.post(f"/messages/{message_id}/read")
    assert marked.status_code == 200

    unread_after = recipient_client.get("/messages/unread-count")
    assert unread_after.json()["unread_count"] == 0

    # sending a message also raises a bell notification for the recipient
    notifications = recipient_client.get("/notifications").json()
    assert any(n["type"] == "message" for n in notifications)


def test_search_returns_matching_players_and_teams():
    create_test_player(player_id="P300")

    response = client.get("/search", params={"q": "Karim"})
    assert response.status_code == 200
    body = response.json()
    assert any(p["player_id"] == "P300" for p in body["players"])

    team_response = client.get("/search", params={"q": "TrainingBuddy"})
    assert team_response.status_code == 200
    assert len(team_response.json()["teams"]) > 0

    short_query = client.get("/search", params={"q": "a"})
    assert short_query.status_code == 200
    assert short_query.json() == {"players": [], "teams": [], "videos": []}


def test_upload_and_remove_own_avatar():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    upload = client.post(
        "/auth/me/avatar",
        files={"avatar": ("photo.png", png_bytes, "image/png")},
    )
    assert upload.status_code == 200
    avatar_url = upload.json()["avatar_url"]
    assert avatar_url.startswith("/uploads/avatars/")

    identity = client.get("/auth/me").json()
    assert identity["user"]["avatar_url"] == avatar_url

    served = client.get(avatar_url)
    assert served.status_code == 200
    assert served.content == png_bytes

    removed = client.delete("/auth/me/avatar")
    assert removed.status_code == 200

    identity_after = client.get("/auth/me").json()
    assert identity_after["user"]["avatar_url"] is None

    served_after = client.get(avatar_url)
    assert served_after.status_code == 404


def test_avatar_upload_rejects_non_image_content():
    fake_bytes = b"not-an-image" + b"\x00" * 20

    response = client.post(
        "/auth/me/avatar",
        files={"avatar": ("fake.png", fake_bytes, "image/png")},
    )
    assert response.status_code == 400


def test_upload_and_remove_player_photo():
    player = create_test_player(player_id="P500")
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    upload = client.post(
        f"/players/{player['player_id']}/photo",
        files={"photo": ("player.png", png_bytes, "image/png")},
    )
    assert upload.status_code == 200
    photo_url = upload.json()["photo_url"]

    fetched = client.get(f"/players/{player['player_id']}")
    assert fetched.json()["photo_filename"] == photo_url.rsplit("/", 1)[-1]

    served = client.get(photo_url)
    assert served.status_code == 200
    assert served.content == png_bytes

    # Editing the player afterwards must not wipe the uploaded photo.
    edited = dict(player)
    edited["first_name_en"] = "Karim2"
    update_response = client.put(f"/players/{player['player_id']}", json=edited)
    assert update_response.status_code == 200
    assert update_response.json()["photo_filename"] == photo_url.rsplit("/", 1)[-1]

    removed = client.delete(f"/players/{player['player_id']}/photo")
    assert removed.status_code == 200

    fetched_after = client.get(f"/players/{player['player_id']}")
    assert fetched_after.json()["photo_filename"] is None

    served_after = client.get(photo_url)
    assert served_after.status_code == 404


def test_player_created_at_survives_updates():
    player = create_test_player(player_id="P501")
    first_created_at = client.get(f"/players/{player['player_id']}").json()["created_at"]
    assert first_created_at is not None

    edited = dict(player)
    edited["first_name_en"] = "Renamed"
    client.put(f"/players/{player['player_id']}", json=edited)

    second_created_at = client.get(f"/players/{player['player_id']}").json()["created_at"]
    assert second_created_at == first_created_at


def test_team_created_at_is_set_and_survives_updates():
    created = client.post(
        "/teams",
        json={
            "team_id": None,
            "name": "Persistence Test FC",
            "age_group": "U12",
            "coach_name": "Coach Test",
            "season_id": "2026-2027",
            "active": True,
        },
    )
    assert created.status_code == 201
    team_id = created.json()["team_id"]
    first_created_at = created.json()["created_at"]
    assert first_created_at is not None

    updated = client.put(
        f"/teams/{team_id}",
        json={
            "team_id": team_id,
            "name": "Persistence Test FC Renamed",
            "age_group": "U12",
            "coach_name": "Coach Test",
            "season_id": "2026-2027",
            "active": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["created_at"] == first_created_at
