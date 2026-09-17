from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import GuardianPlayerLinkDB, PlayerAssessmentDB, UserDB
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


TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id="TRK_COACH",
        username="trk.coach",
        password_hash=hash_password("TrkCoachPassword123!"),
        role="coach",
        active=True,
        created_at=now,
        updated_at=now,
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
        json={"username": "trk.coach", "password": "TrkCoachPassword123!"},
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


def _create_role_client(username, password, role):
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id=f"TRK_{username.upper()}",
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

    role_client = TestClient(app)
    login_response = role_client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login_response.status_code == 200
    role_client.headers.update({
        "X-CSRF-Token": role_client.cookies.get(CSRF_COOKIE_NAME),
    })
    return role_client


def create_test_player(client, player_id):
    payload = {
        "player_id": player_id,
        "first_name_ar": "لاعب",
        "last_name_ar": "تجربة",
        "first_name_en": "Tracking",
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


def create_session(client, player_id, tracking_mode="smart_soccer"):
    response = client.post(
        "/tracking/sessions",
        json={"player_id": player_id, "tracking_mode": tracking_mode, "gimbal_model": "Insta360 Flow 2 Pro"},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def make_sample(t, player=True, ball=True, status="locked"):
    entry = {
        "t_seconds": t,
        "gimbal_state": "tracking_active",
        "tracking_mode": "smart_soccer",
        "tracking_status": status,
    }
    if player:
        entry["player_bbox"] = [0.1 + 0.01 * t, 0.4, 0.15, 0.4]
        entry["player_center"] = [0.1 + 0.01 * t, 0.5]
        entry["player_confidence"] = 0.9
        entry["player_track_id"] = 1
    if ball:
        entry["ball_bbox"] = [0.12 + 0.01 * t, 0.55, 0.03, 0.03]
        entry["ball_center"] = [0.12 + 0.01 * t, 0.55]
        entry["ball_confidence"] = 0.85
        entry["ball_track_id"] = 1
    return entry


def ingest_rich_session(client, session_id, count=40):
    samples = [make_sample(float(i)) for i in range(count)]
    response = client.post(f"/tracking/sessions/{session_id}/samples", json={"samples": samples})
    assert response.status_code == 201
    assert response.json()["ingested"] == count


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def test_coach_can_create_and_complete_a_tracking_session(client):
    create_test_player(client, "TRK_P_LIFECYCLE")
    session_id = create_session(client, "TRK_P_LIFECYCLE")

    ingest_rich_session(client, session_id)

    event_response = client.post(
        f"/tracking/sessions/{session_id}/events",
        json={"event_type": "player_locked", "details": {"target_track_id": 1}},
    )
    assert event_response.status_code == 201

    complete_response = client.post(f"/tracking/sessions/{session_id}/complete", json={})
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    events = client.get(f"/tracking/sessions/{session_id}/events").json()["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "player_locked"


def test_creating_session_for_unknown_player_returns_404(client):
    response = client.post(
        "/tracking/sessions",
        json={"player_id": "DOES_NOT_EXIST", "tracking_mode": "smart_soccer"},
    )
    assert response.status_code == 404


def test_invalid_tracking_mode_rejected(client):
    create_test_player(client, "TRK_P_BADMODE")
    response = client.post(
        "/tracking/sessions",
        json={"player_id": "TRK_P_BADMODE", "tracking_mode": "not_a_real_mode"},
    )
    assert response.status_code == 422  # pydantic Literal validation


def test_guardian_cannot_create_a_tracking_session(client):
    create_test_player(client, "TRK_P_GUARDDENY")
    guardian = _create_role_client("trk.guardian.deny", "GuardianPassword123!", "guardian")
    response = guardian.post(
        "/tracking/sessions",
        json={"player_id": "TRK_P_GUARDDENY", "tracking_mode": "smart_soccer"},
    )
    assert response.status_code == 403


def test_coach_cannot_ingest_samples_into_another_coachs_session(client):
    create_test_player(client, "TRK_P_OTHERCOACH")
    session_id = create_session(client, "TRK_P_OTHERCOACH")

    other_coach = _create_role_client("trk.coach2", "TrkCoach2Password123!", "coach")
    response = other_coach.post(
        f"/tracking/sessions/{session_id}/samples",
        json={"samples": [make_sample(0.0)]},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Quality, features, heatmap
# ---------------------------------------------------------------------------

def test_quality_features_and_heatmap_endpoints(client):
    create_test_player(client, "TRK_P_ANALYSIS")
    session_id = create_session(client, "TRK_P_ANALYSIS")
    ingest_rich_session(client, session_id)
    client.post(f"/tracking/sessions/{session_id}/complete", json={})

    quality = client.get(f"/tracking/sessions/{session_id}/quality").json()
    assert quality["outcome"] in {"VALID", "VALID_WITH_LIMITED_CONFIDENCE", "REVIEW_REQUIRED", "INSUFFICIENT_DATA"}

    features = client.get(f"/tracking/sessions/{session_id}/features").json()
    assert features["movement"]["status"] == "ok"

    heatmap = client.get(f"/tracking/sessions/{session_id}/heatmap").json()
    assert heatmap["sample_count"] == 40
    assert heatmap["player_heat_grid"] is not None


def test_guardian_can_view_own_childs_session_not_anothers(client):
    create_test_player(client, "TRK_P_FAM_A")
    create_test_player(client, "TRK_P_FAM_B")
    session_a = create_session(client, "TRK_P_FAM_A")
    session_b = create_session(client, "TRK_P_FAM_B")

    guardian = _create_role_client("trk.guardian.fam", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id="TRK_TRK.GUARDIAN.FAM",
        player_id="TRK_P_FAM_A",
        created_at=utcnow(),
        created_by_user_id="TRK_COACH",
    ))
    db.commit()
    db.close()

    own = guardian.get(f"/tracking/sessions/{session_a}")
    assert own.status_code == 200

    other = guardian.get(f"/tracking/sessions/{session_b}")
    assert other.status_code == 404


# ---------------------------------------------------------------------------
# Publishing to Player Profile
# ---------------------------------------------------------------------------

def test_publish_refuses_when_insufficient_data(client):
    create_test_player(client, "TRK_P_SPARSE")
    session_id = create_session(client, "TRK_P_SPARSE")
    # Only 3 samples — below MIN_SAMPLE_COUNT in tracking_quality.py.
    client.post(
        f"/tracking/sessions/{session_id}/samples",
        json={"samples": [make_sample(float(i)) for i in range(3)]},
    )
    client.post(f"/tracking/sessions/{session_id}/complete", json={})

    response = client.post(f"/tracking/sessions/{session_id}/publish", json={})
    assert response.status_code == 400
    assert "Insufficient" in response.json()["detail"]


def test_publish_creates_player_assessment_linked_to_session_and_correct_player(client):
    create_test_player(client, "TRK_P_PUBLISH")
    create_test_player(client, "TRK_P_OTHER_PUBLISH")
    session_id = create_session(client, "TRK_P_PUBLISH")
    ingest_rich_session(client, session_id)
    client.post(f"/tracking/sessions/{session_id}/complete", json={})

    response = client.post(f"/tracking/sessions/{session_id}/publish", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "TRK_P_PUBLISH"
    assert body["tracking_session_id"] == session_id

    db = TestingSessionLocal()
    assessment = db.get(PlayerAssessmentDB, body["assessment_id"])
    assert assessment is not None
    assert assessment.player_id == "TRK_P_PUBLISH"
    assert assessment.ai_assisted is True
    assert assessment.tracking_session_id == session_id
    assert "skills" in assessment.raw_data
    db.close()

    # Never leaks into the OTHER player's assessment history — correct
    # Player ID assignment, never guessed.
    other_assessments = client.get("/players/TRK_P_OTHER_PUBLISH/assessments").json()
    assert not any(a.get("tracking_session_id") == session_id for a in other_assessments.get("assessments", []))


def test_no_database_wide_facial_recognition_player_always_explicit(client):
    """Every tracking endpoint requires an explicit player_id supplied by
    the caller (the coach's own selection workflow) — there is no
    endpoint anywhere that accepts a video/image and returns a guessed
    player identity."""
    import inspect

    from app.routers import tracking as tracking_router

    source = inspect.getsource(tracking_router)
    assert "face" not in source.lower()
    assert "facial" not in source.lower()


# ---------------------------------------------------------------------------
# Coach validation labels
# ---------------------------------------------------------------------------

def test_coach_can_record_and_list_validation_labels(client):
    create_test_player(client, "TRK_P_LABELS")
    session_id = create_session(client, "TRK_P_LABELS")

    response = client.post(
        f"/tracking/sessions/{session_id}/coach-labels",
        json={"label_type": "ball_control_rating", "value": {"rating": 4}, "notes": "Good close control"},
    )
    assert response.status_code == 201

    labels = client.get(f"/tracking/sessions/{session_id}/coach-labels").json()["labels"]
    assert len(labels) == 1
    assert labels[0]["label_type"] == "ball_control_rating"


def test_guardian_cannot_record_coach_validation_label(client):
    create_test_player(client, "TRK_P_LABELDENY")
    session_id = create_session(client, "TRK_P_LABELDENY")
    guardian = _create_role_client("trk.guardian.label", "GuardianPassword123!", "guardian")
    response = guardian.post(
        f"/tracking/sessions/{session_id}/coach-labels",
        json={"label_type": "tracking_quality", "value": {"flag": True}},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

def test_model_registry_requires_admin(client):
    response = client.get("/tracking/admin/models")
    # trk.coach is role "coach", not "admin" -> require_admin denies.
    assert response.status_code == 403


def test_admin_can_register_list_and_update_model_status():
    admin = _create_role_client("trk.admin.registry", "TrkAdminPassword123!", "admin")

    create_response = admin.post(
        "/tracking/admin/models",
        json={
            "model_name": "ball-detector",
            "model_version": "test-v9.9",
            "model_type": "on_device_classical_cv",
            "status": "experimental",
            "notes": "Test registration",
        },
    )
    assert create_response.status_code == 201
    model_id = create_response.json()["model_id"]

    duplicate_response = admin.post(
        "/tracking/admin/models",
        json={
            "model_name": "ball-detector",
            "model_version": "test-v9.9",
            "model_type": "on_device_classical_cv",
        },
    )
    assert duplicate_response.status_code == 400

    list_response = admin.get("/tracking/admin/models", params={"model_name": "ball-detector"})
    assert list_response.status_code == 200
    assert any(m["model_id"] == model_id for m in list_response.json()["models"])

    update_response = admin.patch(f"/tracking/admin/models/{model_id}", json={"status": "active"})
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "active"
    assert update_response.json()["deployment_date"] is not None


# ---------------------------------------------------------------------------
# Phase 2: model-version traceability + coach player-confirmation
# ---------------------------------------------------------------------------

def test_session_defaults_ball_model_status_to_missing_when_not_reported(client):
    create_test_player(client, "TRK_P_NOVERSION")
    session_id = create_session(client, "TRK_P_NOVERSION")
    detail = client.get(f"/tracking/sessions/{session_id}").json()
    assert detail["ball_model_status"] == "missing"
    assert detail["ball_detector_version"] is None


def test_session_persists_reported_model_versions(client):
    create_test_player(client, "TRK_P_VERSIONS")
    response = client.post(
        "/tracking/sessions",
        json={
            "player_id": "TRK_P_VERSIONS",
            "tracking_mode": "smart_soccer",
            "player_detector_version": "vision-v1",
            "ball_detector_version": "classical-cv-v0.1",
            "ball_model_status": "fallback_classical",
            "pose_model_version": "vision-v1",
            "tracker_algorithm_version": "player-tracker-v1",
            "framing_algorithm_version": "framing-calculator-v1",
            "ios_app_version": "1.0.0",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["ball_model_status"] == "fallback_classical"
    assert body["player_detector_version"] == "vision-v1"
    assert body["ios_app_version"] == "1.0.0"


def test_coach_can_confirm_correct_player_tracked(client):
    create_test_player(client, "TRK_P_CONFIRM")
    session_id = create_session(client, "TRK_P_CONFIRM")

    response = client.post(
        f"/tracking/sessions/{session_id}/confirm-player",
        json={"answer": "yes"},
    )
    assert response.status_code == 201
    assert response.json()["label_type"] == "correct_player_tracked"
    assert response.json()["value"] == {"answer": "yes"}

    labels = client.get(f"/tracking/sessions/{session_id}/coach-labels").json()["labels"]
    assert any(label["label_type"] == "correct_player_tracked" for label in labels)


def test_guardian_cannot_confirm_player_tracked(client):
    create_test_player(client, "TRK_P_CONFIRMDENY")
    session_id = create_session(client, "TRK_P_CONFIRMDENY")
    guardian = _create_role_client("trk.guardian.confirm", "GuardianPassword123!", "guardian")
    response = guardian.post(
        f"/tracking/sessions/{session_id}/confirm-player",
        json={"answer": "no"},
    )
    assert response.status_code == 403


def test_quality_gate_forces_review_required_when_id_switch_detected(client):
    create_test_player(client, "TRK_P_SWITCH")
    session_id = create_session(client, "TRK_P_SWITCH")

    # 10Hz spacing (0.1s apart) — realistic telemetry density, and
    # crucially well under tracking_quality.ID_SWITCH_MAX_DT_SECONDS
    # (0.5s), so the injected jump below is actually evaluated rather
    # than skipped as "a gap wide enough to be a legitimate
    # re-acquisition." An earlier version of this test used 1-per-second
    # samples (dt=1.0s) and the assertion failed on real execution
    # because every pair was being skipped for exactly that reason —
    # caught by actually running the test, not just reading the code.
    samples = [make_sample(i * 0.1) for i in range(40)]
    samples[20]["player_center"] = [0.95, 0.95]
    client.post(f"/tracking/sessions/{session_id}/samples", json={"samples": samples})
    client.post(f"/tracking/sessions/{session_id}/complete", json={})

    quality = client.get(f"/tracking/sessions/{session_id}/quality").json()
    assert quality["id_switch_risk_count"] >= 1
    assert quality["outcome"] == "REVIEW_REQUIRED"

    # publish_assessment still succeeds (REVIEW_REQUIRED is not
    # INSUFFICIENT_DATA) but the published record honestly carries the
    # review-required outcome for a coach to see, never silently upgraded.
    publish_response = client.post(f"/tracking/sessions/{session_id}/publish", json={})
    assert publish_response.status_code == 200
