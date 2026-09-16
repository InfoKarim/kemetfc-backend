"""Guardian-role authorization audit.

A guardian must only ever be able to reach data for the specific
player(s) explicitly linked to their account via GuardianPlayerLinkDB —
never any other KEMET FC player, regardless of which endpoint, ID shape,
or URL is used to ask for it. These tests exercise the real HTTP/API
surface (not just service-layer functions) so a passing suite is actual
evidence the backend enforces this, independent of any UI.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import AnalysisDB, PlayerAssessmentDB, PlayerDB, UserDB
from app.services.auth_service import hash_password, utcnow
from app.services.id_service import next_entity_id
from main import CSRF_COOKIE_NAME, app


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Foreign keys deliberately left unenforced here (unlike most other test
# modules): this suite is about authorization logic, not referential
# integrity, and a couple of seeded rows (e.g. an analysis's video_id)
# reference placeholder IDs with no real row behind them.

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


def make_player(db, player_id: str) -> PlayerDB:
    player = PlayerDB(
        player_id=player_id,
        first_name_ar="لاعب",
        last_name_ar=player_id,
        first_name_en="Player",
        last_name_en=player_id,
        date_of_birth=date(2016, 1, 1),
        sex="male",
        **PLAYER_PROFILE_KWARGS,
    )
    db.add(player)
    return player


def make_user(db, user_id: str, username: str, role: str) -> UserDB:
    now = utcnow()
    user = UserDB(
        user_id=user_id,
        username=username,
        password_hash=hash_password(f"{username}Password123!"),
        role=role,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    return user


@pytest.fixture(scope="module")
def seeded():
    """Seed a fixed authorization fixture used by every test in this
    module:

    - Player A, Player B, Player C — three independent players.
    - Admin and coach staff accounts (unrestricted by role).
    - guardian_a -> linked to Player A only.
    - guardian_b -> linked to Player B only.
    - guardian_multi -> linked to Player A AND Player C (two children).
    - guardian_none -> linked to no one.
    - One AnalysisDB row and one PlayerAssessmentDB row for Player A, to
      exercise IDOR checks on IDs that aren't the player_id itself.
    """
    db = TestingSessionLocal()

    make_player(db, "P_GUARD_A")
    make_player(db, "P_GUARD_B")
    make_player(db, "P_GUARD_C")
    make_user(db, "STAFF_ADMIN", "guardaudit.admin", "admin")
    make_user(db, "STAFF_COACH", "guardaudit.coach", "coach")
    make_user(db, "GUARDIAN_A", "guardaudit.parent.a", "guardian")
    make_user(db, "GUARDIAN_B", "guardaudit.parent.b", "guardian")
    make_user(db, "GUARDIAN_MULTI", "guardaudit.parent.multi", "guardian")
    make_user(db, "GUARDIAN_NONE", "guardaudit.parent.none", "guardian")
    db.commit()

    db.add(AnalysisDB(
        analysis_id="AN_GUARD_A",
        video_id="VID_PLACEHOLDER",
        player_id="P_GUARD_A",
        created_at=utcnow(),
        analysis_type="player_performance",
        model_name="test_model",
        model_version="1.0",
        processing_status="completed",
        processed_at=utcnow(),
        confidence_score=0.9,
        overall_score=75.0,
        strengths=[["Stamina", 80.0]],
        weaknesses=[["Shooting", 60.0]],
        recommendations=[],
        raw_output_path="/analysis/test.json",
        requires_human_review=False,
        human_review_status="not_required",
        approved=False,
    ))
    db.commit()

    assessment = PlayerAssessmentDB(
        assessment_id=next_entity_id(db, "player_assessment"),
        player_id="P_GUARD_A",
        pillar="physical",
        test_category="endurance",
        test_type="yoyo_kids",
        methodology_version="yoyo_kids_raw_distance_v1",
        test_date=date(2026, 9, 1),
        age_at_assessment_years=10,
        raw_data={"level": 10, "shuttle": 2, "total_distance_m": 400.0},
        calculated_metrics={},
        ai_assisted=False,
        notes=None,
        recorded_by_user_id="STAFF_COACH",
        created_at=utcnow(),
    )
    db.add(assessment)
    db.commit()
    assessment_id = assessment.assessment_id
    db.close()

    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = app.state.auth_session_factory
    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = TestingSessionLocal

    def login(username: str) -> TestClient:
        test_client = TestClient(app)
        response = test_client.post(
            "/auth/login",
            json={"username": username, "password": f"{username}Password123!"},
        )
        assert response.status_code == 200
        test_client.headers.update({
            "X-CSRF-Token": test_client.cookies.get(CSRF_COOKIE_NAME),
        })
        return test_client

    context = {
        "admin": login("guardaudit.admin"),
        "coach": login("guardaudit.coach"),
        "guardian_a": login("guardaudit.parent.a"),
        "guardian_b": login("guardaudit.parent.b"),
        "guardian_multi": login("guardaudit.parent.multi"),
        "guardian_none": login("guardaudit.parent.none"),
        "analysis_id": "AN_GUARD_A",
        "assessment_id": assessment_id,
    }

    # Link guardians to their players using the admin session (the only
    # role permitted to create GuardianPlayerLinkDB rows).
    for guardian_user_id, player_id in [
        ("GUARDIAN_A", "P_GUARD_A"),
        ("GUARDIAN_B", "P_GUARD_B"),
        ("GUARDIAN_MULTI", "P_GUARD_A"),
        ("GUARDIAN_MULTI", "P_GUARD_C"),
    ]:
        response = context["admin"].post(
            "/guardian-player-links",
            json={"guardian_user_id": guardian_user_id, "player_id": player_id},
        )
        assert response.status_code == 201

    yield context

    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override
    app.state.auth_session_factory = previous_session_factory


# 1. Guardian can access linked player.
def test_guardian_can_access_linked_player(seeded):
    response = seeded["guardian_a"].get("/players/P_GUARD_A")
    assert response.status_code == 200
    assert response.json()["player_id"] == "P_GUARD_A"


# 2 & 3. Guardian cannot access an unrelated player, including by simply
# swapping the ID in the URL for one the app itself just showed them.
def test_guardian_cannot_access_unrelated_player_by_url(seeded):
    response = seeded["guardian_a"].get("/players/P_GUARD_B")
    assert response.status_code == 404


# Cross-check the full 2x2 the task asks for explicitly: each guardian
# reaches their own player and no one else's.
def test_guardian_b_can_access_own_player_and_no_other(seeded):
    own = seeded["guardian_b"].get("/players/P_GUARD_B")
    other = seeded["guardian_b"].get("/players/P_GUARD_A")
    assert own.status_code == 200
    assert own.json()["player_id"] == "P_GUARD_B"
    assert other.status_code == 404


# 4. Guardian cannot access unrelated player data through the API
# generally — sweep every player-scoped sub-resource this session added
# or hardened.
@pytest.mark.parametrize("path", [
    "/players/P_GUARD_B",
    "/players/P_GUARD_B/analyses",
    "/players/P_GUARD_B/development-plan",
    "/players/P_GUARD_B/development-snapshot",
    "/players/P_GUARD_B/tactical-assessment",
    "/players/P_GUARD_B/technical-assessment",
    "/players/P_GUARD_B/mental-assessment",
    "/players/P_GUARD_B/match-performance-assessment",
    "/players/P_GUARD_B/weak-foot-assessment",
    "/players/P_GUARD_B/smart-recommendations",
    "/players/P_GUARD_B/sports-medicine-notes",
    "/players/P_GUARD_B/coaching-insights",
    "/players/P_GUARD_B/recommended-drill",
    "/players/P_GUARD_B/assessments",
    "/players/P_GUARD_B/development-report",
])
def test_guardian_cannot_reach_unrelated_player_subresources(seeded, path):
    response = seeded["guardian_a"].get(path)
    assert response.status_code in (403, 404)
    assert response.status_code != 200


# 5. Guardian cannot access an unrelated assessment (by analysis ID).
def test_guardian_cannot_access_unrelated_analysis_by_id(seeded):
    response = seeded["guardian_b"].get("/analyses/AN_GUARD_A")
    assert response.status_code == 404


def test_guardian_can_access_own_linked_analysis(seeded):
    response = seeded["guardian_a"].get("/analyses/AN_GUARD_A")
    assert response.status_code == 200


# 6 & 7. Guardian cannot access an unrelated player's Yo-Yo /
# Physical Performance results.
def test_guardian_cannot_access_unrelated_physical_assessments(seeded):
    response = seeded["guardian_b"].get(
        "/players/P_GUARD_A/assessments?pillar=physical"
    )
    assert response.status_code == 404


def test_guardian_can_access_own_linked_physical_assessments(seeded):
    response = seeded["guardian_a"].get(
        "/players/P_GUARD_A/assessments?pillar=physical"
    )
    assert response.status_code == 200
    assert response.json()["assessments"][0]["player_id"] == "P_GUARD_A"


# 8. Guardian cannot access an unrelated player's Technical Assessment.
def test_guardian_cannot_access_unrelated_technical_assessment(seeded, monkeypatch):
    import main
    monkeypatch.setattr(main, "is_provider_configured", lambda provider: True)
    response = seeded["guardian_b"].get("/players/P_GUARD_A/technical-assessment")
    assert response.status_code == 404


# 9. Guardian cannot access another player's report (development
# snapshot, via both the direct and the guardian-namespaced route).
def test_guardian_cannot_access_unrelated_player_report(seeded):
    direct = seeded["guardian_a"].get("/players/P_GUARD_B/development-snapshot")
    assert direct.status_code == 404

    namespaced = seeded["guardian_a"].get(
        "/guardian/children/P_GUARD_B/development-snapshot"
    )
    assert namespaced.status_code == 404


# 10. Guardian cannot download/export another player's report.
def test_guardian_cannot_export_unrelated_player_data(seeded):
    response = seeded["guardian_a"].get(
        "/guardian/children/P_GUARD_B/data-export"
    )
    assert response.status_code == 404


def test_guardian_can_export_own_linked_player_data(seeded):
    response = seeded["guardian_a"].get(
        "/guardian/children/P_GUARD_A/data-export"
    )
    assert response.status_code == 200


# 11. Guardian cannot list all players.
def test_guardian_cannot_list_all_players(seeded):
    response = seeded["guardian_a"].get("/players")
    assert response.status_code == 403


# 12. Guardian cannot list all reports/analyses.
def test_guardian_cannot_list_all_analyses(seeded):
    response = seeded["guardian_a"].get("/analyses")
    assert response.status_code == 403


# 13 & 14. Guardian with two linked children can access both, and still
# cannot access a third, unrelated player.
def test_guardian_with_two_children_can_access_both(seeded):
    first = seeded["guardian_multi"].get("/players/P_GUARD_A")
    second = seeded["guardian_multi"].get("/players/P_GUARD_C")
    assert first.status_code == 200
    assert second.status_code == 200


def test_guardian_with_two_children_still_denied_a_third(seeded):
    response = seeded["guardian_multi"].get("/players/P_GUARD_B")
    assert response.status_code == 404


# 15. Guardian with no linked player gets a safe empty state, not an
# error and not someone else's data.
def test_guardian_with_no_linked_player_gets_empty_state(seeded):
    children = seeded["guardian_none"].get("/guardian/children")
    assert children.status_code == 200
    assert children.json() == []

    denied = seeded["guardian_none"].get("/players/P_GUARD_A")
    assert denied.status_code == 404


# 16. Coach/Admin behavior remains correct and is not accidentally
# broken by any of the guardian-scoping added above.
def test_admin_and_coach_are_unaffected_by_guardian_scoping(seeded):
    for role_client in (seeded["admin"], seeded["coach"]):
        assert role_client.get("/players").status_code == 200
        assert role_client.get("/players/P_GUARD_A").status_code == 200
        assert role_client.get("/players/P_GUARD_B").status_code == 200
        assert role_client.get("/analyses").status_code == 200
        assert role_client.get("/analyses/AN_GUARD_A").status_code == 200
        assert (
            role_client.get("/players/P_GUARD_A/assessments").status_code == 200
        )


# 17. IDOR/BOLA sweep: deleting or mutating another player's assessment
# by ID must be denied even though the ID itself doesn't look like it
# belongs to anyone in particular.
def test_guardian_cannot_delete_unrelated_players_assessment(seeded):
    response = seeded["guardian_b"].delete(
        f"/player-assessments/{seeded['assessment_id']}"
    )
    assert response.status_code == 403


def test_guardian_cannot_record_assessment_for_any_player(seeded):
    # Guardians are read-only observers — recording results is a
    # coach/admin action regardless of whose child it is, including
    # their own.
    response = seeded["guardian_a"].post(
        "/players/P_GUARD_A/physical-assessments/yoyo-kids",
        json={
            "test_date": "2026-09-01",
            "level": 5,
            "shuttle": 1,
            "total_distance_m": 200.0,
        },
    )
    assert response.status_code == 403


def test_guardian_cannot_access_unrelated_player_via_photo_endpoint(seeded):
    # A different ID-bearing path shape than the ones above — the photo
    # routes have their own explicit ownership check, verified here too.
    response = seeded["guardian_a"].delete("/players/P_GUARD_B/photo")
    assert response.status_code == 404


# Global search must never surface players outside a guardian's own
# linked children, even though guardians can't currently reach this path
# at all (it's not in the middleware allowlist) — the handler itself
# must not depend on that allowlist being the only safeguard.
def test_guardian_search_is_scoped_to_own_children_at_the_handler_level(seeded):
    from app.routers.messaging import search

    db_gen = override_get_db()
    db = next(db_gen)

    class FakeState:
        current_user = {"role": "guardian", "user_id": "GUARDIAN_A"}

    class FakeRequest:
        state = FakeState()

    result = search(request=FakeRequest(), q="Player", db=db)
    player_ids = {item["player_id"] for item in result["players"]}

    assert player_ids == {"P_GUARD_A"}
    assert "P_GUARD_B" not in player_ids
    assert "P_GUARD_C" not in player_ids

    db.close()


# Development report: guardian can read their own child's, never
# another's, and can never write the coach message regardless of
# ownership (it's a staff-authored field, guardian is read-only).
def test_guardian_can_view_own_linked_development_report(seeded):
    response = seeded["guardian_a"].get("/players/P_GUARD_A/development-report")
    assert response.status_code == 200
    assert response.json()["player_id"] == "P_GUARD_A"
    assert "score" not in response.json()


def test_guardian_cannot_set_coach_message_for_own_or_other_child(seeded):
    own = seeded["guardian_a"].put(
        "/players/P_GUARD_A/coach-message",
        json={"message": "I am not staff", "next_focus": []},
    )
    other = seeded["guardian_a"].put(
        "/players/P_GUARD_B/coach-message",
        json={"message": "IDOR attempt", "next_focus": []},
    )
    assert own.status_code == 403
    assert other.status_code == 403


def test_admin_can_set_coach_message_and_guardian_sees_it(seeded):
    update = seeded["admin"].put(
        "/players/P_GUARD_A/coach-message",
        json={"message": "Great week!", "next_focus": ["Scanning"]},
    )
    assert update.status_code == 200

    report = seeded["guardian_a"].get(
        "/players/P_GUARD_A/development-report"
    ).json()
    assert report["coach_message"]["message"] == "Great week!"
