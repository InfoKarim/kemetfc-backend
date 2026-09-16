from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.player_assessments as player_assessments_router
from app.database import Base, get_db
from app.db_models import PlayerDB, UserDB
from app.services.auth_service import hash_password, utcnow
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
