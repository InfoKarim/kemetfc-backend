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


def contact_message_payload(**overrides):
    payload = {
        "name": "Nadia Farouk",
        "email": "nadia@example.com",
        "topic": "General Information",
        "message": "What ages do you coach, and is the assessment really free?",
    }
    payload.update(overrides)
    return payload


def test_public_contact_message_succeeds_without_auth(anonymous_client):
    response = anonymous_client.post(
        "/public/contact-messages",
        json=contact_message_payload(),
    )

    assert response.status_code == 201
    assert "message_id" in response.json()


def test_public_contact_message_requires_message_text(anonymous_client):
    payload = contact_message_payload()
    payload["message"] = ""

    response = anonymous_client.post("/public/contact-messages", json=payload)

    assert response.status_code == 422


def test_contact_messages_list_requires_authentication(anonymous_client):
    response = anonymous_client.get("/contact-messages")

    assert response.status_code == 401


def test_admin_can_list_contact_messages(client, anonymous_client):
    created = anonymous_client.post(
        "/public/contact-messages",
        json=contact_message_payload(email="list-test@example.com"),
    )
    assert created.status_code == 201
    message_id = created.json()["message_id"]

    response = client.get("/contact-messages")

    assert response.status_code == 200
    messages = response.json()
    match = next(
        (item for item in messages if item["message_id"] == message_id),
        None,
    )
    assert match is not None
    assert match["email"] == "list-test@example.com"


def test_delete_contact_message_requires_authentication(anonymous_client):
    response = anonymous_client.delete("/contact-messages/MSG000001")

    assert response.status_code == 401


def test_admin_can_delete_contact_message(client, anonymous_client):
    created = anonymous_client.post(
        "/public/contact-messages",
        json=contact_message_payload(email="delete-test@example.com"),
    )
    assert created.status_code == 201
    message_id = created.json()["message_id"]

    response = client.delete(f"/contact-messages/{message_id}")
    assert response.status_code == 200

    remaining = client.get("/contact-messages").json()
    assert all(item["message_id"] != message_id for item in remaining)


def test_delete_unknown_contact_message_returns_404(client):
    response = client.delete("/contact-messages/MSG999999")

    assert response.status_code == 404
