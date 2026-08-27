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
