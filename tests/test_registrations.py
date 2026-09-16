import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import UserDB
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


@pytest.fixture(scope="module")
def client():
    """Authenticated TestClient wired to this module's own in-memory DB.

    The FastAPI ``app`` object is a process-wide singleton, so mutating
    ``app.dependency_overrides``/``app.state.auth_session_factory`` at
    import time (as tests/test_api.py does) leaks across test modules when
    the full suite runs together. Scoping the swap to this fixture's
    setup/teardown keeps it local to this module's tests.
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
    # `client` fixture already put the DB override in place for this
    # module's tests; this is a second TestClient with no session cookies.
    return TestClient(app)


def registration_payload(**overrides):
    payload = {
        "parent_name": "Sara Youssef",
        "parent_email": "sara@example.com",
        "parent_phone": "555-0100",
        "emergency_contact": "Ahmed Youssef, 555-0101",
        "player_name": "Layla Youssef",
        "player_date_of_birth": "2017-03-12",
        "player_age": 8,
        "preferred_position": "Midfielder",
        "experience_level": "Recreational (1-2 years)",
        "current_team": None,
        "consents": {
            "parent_consent": True,
            "liability_waiver": True,
            "emergency_medical": True,
            "photo_video": True,
            "privacy_policy": True,
            "terms": True,
            "technology_ai_consent": True,
        },
    }
    payload.update(overrides)
    return payload


def test_public_registration_succeeds_without_auth(anonymous_client):
    response = anonymous_client.post(
        "/public/registrations",
        json=registration_payload(),
    )

    assert response.status_code == 201
    assert "registration_id" in response.json()


def test_public_registration_requires_all_consents(anonymous_client):
    payload = registration_payload()
    payload["consents"]["technology_ai_consent"] = False

    response = anonymous_client.post("/public/registrations", json=payload)

    assert response.status_code == 422


def test_registrations_list_requires_authentication(anonymous_client):
    response = anonymous_client.get("/registrations")

    assert response.status_code == 401


def test_admin_can_list_registrations(client, anonymous_client):
    created = anonymous_client.post(
        "/public/registrations",
        json=registration_payload(parent_email="list-test@example.com"),
    )
    assert created.status_code == 201
    registration_id = created.json()["registration_id"]

    response = client.get("/registrations")

    assert response.status_code == 200
    registrations = response.json()
    match = next(
        (item for item in registrations if item["registration_id"] == registration_id),
        None,
    )
    assert match is not None
    assert match["parent_email"] == "list-test@example.com"
    assert match["consents"]["parent_consent"] is True


def test_delete_registration_requires_authentication(anonymous_client):
    response = anonymous_client.delete("/registrations/REG000001")

    assert response.status_code == 401


def test_admin_can_delete_registration(client, anonymous_client):
    created = anonymous_client.post(
        "/public/registrations",
        json=registration_payload(parent_email="delete-test@example.com"),
    )
    assert created.status_code == 201
    registration_id = created.json()["registration_id"]

    response = client.delete(f"/registrations/{registration_id}")
    assert response.status_code == 200

    remaining = client.get("/registrations").json()
    assert all(item["registration_id"] != registration_id for item in remaining)


def test_delete_unknown_registration_returns_404(client):
    response = client.delete("/registrations/REG999999")

    assert response.status_code == 404


def test_admin_can_waitlist_and_restore_a_registration(client, anonymous_client):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="Waitlist Parent",
        parent_email="waitlist-parent@example.com",
        player_name="Waitlist Kid",
    )

    waitlist_response = client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "waitlisted"},
    )
    assert waitlist_response.status_code == 200
    assert waitlist_response.json()["status"] == "waitlisted"

    restore_response = client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "submitted"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["status"] == "submitted"


def test_admin_can_archive_a_registration(client, anonymous_client):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="Archive Parent",
        parent_email="archive-parent@example.com",
        player_name="Archive Kid",
    )

    response = client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "archived"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"


def test_cannot_change_status_of_a_registration_already_linked_to_a_player(
    client, anonymous_client
):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="Locked Parent",
        parent_email="locked-parent@example.com",
        player_name="Locked Kid",
    )
    create_response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )
    assert create_response.status_code == 201

    response = client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "archived"},
    )
    assert response.status_code == 409


def test_non_admin_cannot_change_registration_status(client, anonymous_client):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="No Access Parent",
        parent_email="no-access-status@example.com",
        player_name="No Access Kid",
    )

    coach_client = TestClient(app)
    client.post(
        "/auth/users",
        json={
            "username": "registrations.status.coach",
            "password": "CoachPassword123!",
            "role": "coach",
        },
    )
    login_response = coach_client.post(
        "/auth/login",
        json={"username": "registrations.status.coach", "password": "CoachPassword123!"},
    )
    assert login_response.status_code == 200
    coach_client.headers.update({
        "X-CSRF-Token": coach_client.cookies.get(CSRF_COOKIE_NAME),
    })

    response = coach_client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "archived"},
    )
    assert response.status_code == 403

    anon_response = anonymous_client.patch(
        f"/registrations/{registration_id}/status",
        json={"status": "archived"},
    )
    assert anon_response.status_code == 401


def additional_player_payload(**overrides):
    payload = {
        "first_name_ar": "ليلى",
        "last_name_ar": "يوسف",
        "sex": "female",
        "team_id": None,
        "physical_profile": {
            "height_cm": 130.0,
            "weight_kg": 28.0,
            "dominant_foot": "right",
            "speed": 60.0,
            "acceleration": 62.0,
            "agility": 58.0,
            "stamina": 65.0,
            "strength": 50.0,
        },
        "technical_profile": {
            "ball_control": 50.0,
            "dribbling": 52.0,
            "passing": 48.0,
            "shooting": 45.0,
            "finishing": 47.0,
        },
        "mental_profile": {
            "decision_making": 50.0,
            "concentration": 52.0,
            "composure": 48.0,
            "positioning": 51.0,
            "vision": 54.0,
            "awareness": 50.0,
            "game_reading": 50.0,
            "coachability": 50.0,
        },
        "match_performance": {
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
        "tactical_profile": {
            "positioning_spatial_intelligence": 50.0,
            "attacking_contribution_in_possession": 48.0,
            "attacking_contribution_off_ball": 52.0,
            "defensive_tactical_contribution": 49.0,
            "transitions": 51.0,
            "decision_quality": 50.0,
            "collective_coordination": 48.0,
            "set_piece_contribution": 45.0,
        },
        "weak_foot_profile": {
            "weak_foot_usage_pct": 15.0,
            "weak_foot_passing": 40.0,
            "weak_foot_receiving": 42.0,
            "weak_foot_dribbling": 38.0,
            "weak_foot_finishing": 35.0,
        },
    }
    payload.update(overrides)
    return payload


def submit_registration(anonymous_client, **overrides):
    response = anonymous_client.post(
        "/public/registrations",
        json=registration_payload(**overrides),
    )
    assert response.status_code == 201
    return response.json()["registration_id"]


def test_create_player_from_registration_populates_fields_and_links_guardian(
    client, anonymous_client
):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="Sara Youssef",
        parent_email="sara.fields@example.com",
        player_name="Layla Youssef",
    )

    response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    player_id = body["player_id"]
    assert body["guardian_account_created"] is True
    assert body["registration"]["status"] == "player_created"
    assert body["registration"]["player_id"] == player_id

    player = client.get(f"/players/{player_id}").json()
    assert player["first_name_en"] == "Layla"
    assert player["last_name_en"] == "Youssef"
    assert player["date_of_birth"] == "2017-03-12"
    assert player["source"] == "registration"
    assert player["created_by_user_id"] == "TEST_ADMIN"

    guardian_user_id = body["guardian_user_id"]

    # Prove the guardian<->player link actually works end-to-end by
    # logging in AS the newly created guardian (admin resets their unknown
    # random password first) and confirming they see the linked child.
    reset_response = client.patch(
        f"/auth/users/{guardian_user_id}",
        json={"password": "GuardianResetPassword123!"},
    )
    assert reset_response.status_code == 200

    guardian_client = TestClient(app)
    guardian_login = guardian_client.post(
        "/auth/login",
        json={
            "username": reset_response.json()["username"],
            "password": "GuardianResetPassword123!",
        },
    )
    assert guardian_login.status_code == 200
    guardian_client.headers.update({
        "X-CSRF-Token": guardian_client.cookies.get(CSRF_COOKIE_NAME),
    })

    children = guardian_client.get("/guardian/children").json()
    assert any(child["player_id"] == player_id for child in children)


def test_create_player_from_registration_reuses_existing_guardian_by_email(
    client, anonymous_client
):
    create_user_response = client.post(
        "/auth/users",
        json={
            "username": "existing.guardian.reuse",
            "password": "GuardianPassword123!",
            "role": "guardian",
            "email": "MoHamed.Reuse@Example.com",
        },
    )
    assert create_user_response.status_code == 201
    guardian_user_id = create_user_response.json()["user_id"]

    registration_id = submit_registration(
        anonymous_client,
        parent_name="Mohamed Reuse",
        parent_email="mohamed.reuse@example.com",
        player_name="Adam Reuse",
    )

    response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["guardian_account_created"] is False
    assert body["guardian_user_id"] == guardian_user_id


def test_create_player_from_registration_detects_possible_duplicate(
    client, anonymous_client
):
    create_test_player_response = client.post(
        "/players",
        json={
            **_MANUAL_PLAYER_TEMPLATE,
            "player_id": "P_DUP_CHECK",
            "first_name_en": "Duplicate",
            "last_name_en": "Check",
            "date_of_birth": "2016-01-01",
        },
    )
    assert create_test_player_response.status_code == 201

    registration_id = submit_registration(
        anonymous_client,
        parent_name="Some Parent",
        parent_email="dup-check-parent@example.com",
        player_name="Duplicate Check",
        player_date_of_birth="2016-01-01",
    )

    response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["possible_duplicate_players"][0]["player_id"] == "P_DUP_CHECK"

    confirmed_response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(confirm_duplicate=True),
    )
    assert confirmed_response.status_code == 201


def test_create_player_from_registration_rejects_already_linked_registration(
    client, anonymous_client
):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="Once Only",
        parent_email="once-only@example.com",
        player_name="Once Only Kid",
    )

    first_response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )
    assert first_response.status_code == 201

    second_response = client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(confirm_duplicate=True),
    )
    assert second_response.status_code == 409
    assert "player_id" in second_response.json()["detail"]


def test_create_player_from_registration_requires_admin(client, anonymous_client):
    registration_id = submit_registration(
        anonymous_client,
        parent_name="No Access",
        parent_email="no-access@example.com",
        player_name="No Access Kid",
    )

    coach_client = TestClient(app)
    coach_created = client.post(
        "/auth/users",
        json={
            "username": "registrations.coach",
            "password": "CoachPassword123!",
            "role": "coach",
        },
    )
    assert coach_created.status_code == 201
    login_response = coach_client.post(
        "/auth/login",
        json={"username": "registrations.coach", "password": "CoachPassword123!"},
    )
    assert login_response.status_code == 200
    coach_client.headers.update({
        "X-CSRF-Token": coach_client.cookies.get(CSRF_COOKIE_NAME),
    })

    response = coach_client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )
    assert response.status_code == 403

    anon_response = anonymous_client.post(
        f"/registrations/{registration_id}/create-player",
        json=additional_player_payload(),
    )
    assert anon_response.status_code == 401


def test_manual_player_creation_still_works_independent_of_registrations(client):
    response = client.post(
        "/players",
        json={
            **_MANUAL_PLAYER_TEMPLATE,
            "player_id": "P_MANUAL_STILL_WORKS",
        },
    )
    assert response.status_code == 201

    player = client.get("/players/P_MANUAL_STILL_WORKS").json()
    assert player["source"] == "manual"
    assert player["created_by_user_id"] == "TEST_ADMIN"


_MANUAL_PLAYER_TEMPLATE = {
    "first_name_ar": "لاعب",
    "last_name_ar": "يدوي",
    "first_name_en": "Manual",
    "last_name_en": "Entry",
    "date_of_birth": "2015-06-01",
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
        "awareness": 70.0,
        "game_reading": 70.0,
        "coachability": 70.0,
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
        "positioning_spatial_intelligence": 70.0,
        "attacking_contribution_in_possession": 68.0,
        "attacking_contribution_off_ball": 72.0,
        "defensive_tactical_contribution": 69.0,
        "transitions": 71.0,
        "decision_quality": 70.0,
        "collective_coordination": 68.0,
        "set_piece_contribution": 65.0,
    },
    "weak_foot_profile": {
        "weak_foot_usage_pct": 20.0,
        "weak_foot_passing": 60.0,
        "weak_foot_receiving": 62.0,
        "weak_foot_dribbling": 58.0,
        "weak_foot_finishing": 55.0,
    },
}
