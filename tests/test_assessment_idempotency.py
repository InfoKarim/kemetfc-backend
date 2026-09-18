"""Backend idempotency for the iOS app's local-first recording queue
(PendingAssessmentStore) — a retried POST after a dropped response must
never create a duplicate tracking session or video record. See
app/services/tracking_service.py (client_recording_id) and
app/routers/videos.py (client-supplied video_id/record_id, existing
409-on-duplicate behavior)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import TrackingSessionDB, UserDB, VideoDB
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
        user_id="IDEMP_COACH",
        username="idemp.coach",
        password_hash=hash_password("IdempCoachPassword123!"),
        role="coach",
        active=True,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()

    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = getattr(app.state, "auth_session_factory", None)
    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = TestingSessionLocal

    test_client = TestClient(app)
    login_response = test_client.post(
        "/auth/login",
        json={"username": "idemp.coach", "password": "IdempCoachPassword123!"},
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


def create_test_player(client, player_id):
    payload = {
        "player_id": player_id,
        "first_name_ar": "لاعب",
        "last_name_ar": "تجربة",
        "first_name_en": "Idemp",
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


def test_retrying_session_create_with_same_client_recording_id_returns_same_session(client):
    create_test_player(client, "IDEMP_P001")
    body = {
        "player_id": "IDEMP_P001",
        "tracking_mode": "smart_soccer",
        "client_recording_id": "CLIENTREC-001",
    }

    first = client.post("/tracking/sessions", json=body)
    assert first.status_code == 201
    second = client.post("/tracking/sessions", json=body)
    assert second.status_code == 201

    assert first.json()["session_id"] == second.json()["session_id"]

    db = TestingSessionLocal()
    count = (
        db.query(TrackingSessionDB)
        .filter(TrackingSessionDB.client_recording_id == "CLIENTREC-001")
        .count()
    )
    db.close()
    assert count == 1


def test_reusing_client_recording_id_with_different_player_is_rejected(client):
    create_test_player(client, "IDEMP_P001B")
    create_test_player(client, "IDEMP_P001C")
    first = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P001B", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-MISMATCH",
    })
    assert first.status_code == 201

    conflict = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P001C", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-MISMATCH",
    })
    assert conflict.status_code == 409

    db = TestingSessionLocal()
    count = (
        db.query(TrackingSessionDB)
        .filter(TrackingSessionDB.client_recording_id == "CLIENTREC-MISMATCH")
        .count()
    )
    db.close()
    assert count == 1


def test_client_recording_id_unique_constraint_is_actually_enforced(client):
    # The service layer's concurrent-race handling (create_session's
    # `except IntegrityError` block) only ever fires if the database
    # itself rejects a second row with the same client_recording_id —
    # this proves that safety net is real, deterministically, rather
    # than relying on true OS-thread timing against SQLite's single
    # shared StaticPool connection (which doesn't reliably reproduce a
    # genuine concurrent-write race in-process). The service layer's
    # try/except IntegrityError -> rollback -> re-query -> return-winner
    # logic is a straightforward, direct consequence of this constraint
    # existing, verified here, plus the sequential-retry test above,
    # which already exercises the SAME re-query-and-return-existing path
    # this except block also takes.
    create_test_player(client, "IDEMP_CONSTRAINT")
    first = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_CONSTRAINT", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-CONSTRAINT",
    })
    assert first.status_code == 201

    db = TestingSessionLocal()
    try:
        db.add(TrackingSessionDB(
            session_id="TRK_RAW_DUPLICATE",
            player_id="IDEMP_CONSTRAINT",
            coach_user_id="IDEMP_COACH",
            tracking_mode="smart_soccer",
            status="recording",
            started_at=utcnow(),
            client_recording_id="CLIENTREC-CONSTRAINT",
            created_at=utcnow(),
        ))
        with pytest.raises(Exception) as exc_info:
            db.commit()
        assert "UNIQUE" in str(exc_info.value) or "unique" in str(exc_info.value)
    finally:
        db.rollback()
        db.close()

    count = TestingSessionLocal().query(TrackingSessionDB).filter(
        TrackingSessionDB.client_recording_id == "CLIENTREC-CONSTRAINT"
    ).count()
    assert count == 1


def test_different_client_recording_ids_create_different_sessions(client):
    create_test_player(client, "IDEMP_P002")
    first = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P002", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-002A",
    })
    second = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P002", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-002B",
    })
    assert first.json()["session_id"] != second.json()["session_id"]


def _upload_video(client, video_id, record_id, player_id, session_id):
    metadata = {
        "video_id": video_id,
        "record_id": record_id,
        "player_id": player_id,
        "video_type": "assessment_smart_tracking",
        "duration_seconds": 32.5,
        "session_id": session_id,
        "location_id": "kemetfc_ios_field_capture",
        "capture_device": "iPhone (KemetFCTracker)",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "schema_version": "1.0",
        "created_by": "ios-app-unidentified-coach",
    }
    return client.post(
        "/videos/upload",
        data={"metadata": json.dumps(metadata)},
        files={
            "video": (
                "assessment.mov",
                b"\x00\x00\x00\x18ftypqt  fake-assessment-video-content",
                "video/quicktime",
            )
        },
    )


def test_retrying_video_upload_with_same_ids_returns_409_never_duplicates(client):
    create_test_player(client, "IDEMP_P003")
    session_response = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P003", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-003",
    })
    session_id = session_response.json()["session_id"]

    first = _upload_video(client, "VID-CLIENTREC-003", "REC-CLIENTREC-003", "IDEMP_P003", session_id)
    assert first.status_code == 201

    # A client retry after e.g. a dropped response — the iOS app treats
    # this 409 as "already uploaded", never as a failure to keep retrying.
    retry = _upload_video(client, "VID-CLIENTREC-003", "REC-CLIENTREC-003", "IDEMP_P003", session_id)
    assert retry.status_code == 409

    db = TestingSessionLocal()
    video_count = db.query(VideoDB).filter(VideoDB.video_id == "VID-CLIENTREC-003").count()
    db.close()
    assert video_count == 1


def test_get_video_returns_fields_needed_for_client_side_409_verification(client):
    create_test_player(client, "IDEMP_P005")
    session_response = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P005", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-005",
    })
    session_id = session_response.json()["session_id"]

    upload_response = _upload_video(client, "VID-CLIENTREC-005", "REC-CLIENTREC-005", "IDEMP_P005", session_id)
    assert upload_response.status_code == 201

    # The iOS app's uploadVideoIdempotent() fetches exactly this endpoint
    # on a 409 and compares record_id/session_id before ever treating the
    # 409 as success — confirming the fields it needs are genuinely
    # present and correct, not merely assumed.
    get_response = client.get("/videos/VID-CLIENTREC-005")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["video_id"] == "VID-CLIENTREC-005"
    assert body["record_id"] == "REC-CLIENTREC-005"
    assert body["session_id"] == session_id


def test_completing_session_after_upload_links_correct_player_and_video(client):
    create_test_player(client, "IDEMP_P004")
    session_response = client.post("/tracking/sessions", json={
        "player_id": "IDEMP_P004", "tracking_mode": "smart_soccer", "client_recording_id": "CLIENTREC-004",
    })
    session_id = session_response.json()["session_id"]

    upload_response = _upload_video(client, "VID-CLIENTREC-004", "REC-CLIENTREC-004", "IDEMP_P004", session_id)
    assert upload_response.status_code == 201

    complete_response = client.post(
        f"/tracking/sessions/{session_id}/complete",
        json={"video_id": "VID-CLIENTREC-004"},
    )
    assert complete_response.status_code == 200

    session_detail = client.get(f"/tracking/sessions/{session_id}").json()
    assert session_detail["player_id"] == "IDEMP_P004"
    assert session_detail["video_id"] == "VID-CLIENTREC-004"
    assert session_detail["status"] == "completed"
