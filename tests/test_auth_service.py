from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services.auth_service import (
    AuthService,
    effective_feature_permissions,
    hash_password,
    utcnow,
    verify_password,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return AuthService(db=TestingSessionLocal())


def test_password_hash_round_trip():
    encoded = hash_password("StrongPassword123!")

    assert "StrongPassword123!" not in encoded
    assert verify_password("StrongPassword123!", encoded) is True
    assert verify_password("WrongPassword123!", encoded) is False


def test_create_authenticate_and_create_session():
    service = make_service()
    user = service.create_user(
        username="Coach.One",
        password="StrongPassword123!",
        role="coach",
    )

    assert user.username == "coach.one"
    assert service.authenticate("COACH.ONE", "StrongPassword123!") == user
    assert service.authenticate("coach.one", "wrong") is None

    created = service.create_session(user)
    authenticated = service.get_session_user(created.token)

    assert authenticated is not None
    assert authenticated[0].user_id == user.user_id
    assert authenticated[1].csrf_token == created.csrf_token


def test_expired_session_is_rejected():
    service = make_service()
    user = service.create_user(
        username="reviewer",
        password="StrongPassword123!",
        role="reviewer",
    )
    created = service.create_session(user)
    authenticated = service.get_session_user(created.token)
    assert authenticated is not None
    authenticated[1].expires_at = utcnow() - timedelta(seconds=1)
    service.db.commit()

    assert service.get_session_user(created.token) is None


def test_password_change_revokes_sessions():
    service = make_service()
    user = service.create_user(
        username="admin",
        password="StrongPassword123!",
        role="admin",
    )
    created = service.create_session(user)

    service.update_user(
        user_id=user.user_id,
        password="NewStrongPassword123!",
    )

    assert service.get_session_user(created.token) is None
    assert service.authenticate("admin", "NewStrongPassword123!") is not None


def test_last_active_administrator_cannot_be_removed():
    service = make_service()
    user = service.create_user(
        username="admin",
        password="StrongPassword123!",
        role="admin",
    )

    try:
        service.update_user(user_id=user.user_id, active=False)
    except ValueError as error:
        assert str(error) == "Cannot remove the last active administrator"
    else:
        raise AssertionError("Expected the last-admin safeguard")


def test_user_feature_permissions_can_be_customized():
    service = make_service()
    user = service.create_user(
        username="limited.coach",
        password="StrongPassword123!",
        role="coach",
        feature_permissions=["players", "dashboard", "players"],
    )

    assert effective_feature_permissions(user) == ["dashboard", "players"]

    updated = service.update_user(
        user_id=user.user_id,
        feature_permissions=["training"],
    )

    assert updated is not None
    assert effective_feature_permissions(updated) == ["training"]


def test_admin_always_has_every_feature():
    service = make_service()
    user = service.create_user(
        username="feature.admin",
        password="StrongPassword123!",
        role="admin",
        feature_permissions=[],
    )

    assert set(effective_feature_permissions(user)) == {
        "dashboard",
        "players",
        "teams",
        "assessments",
        "training",
        "videos",
        "matches",
        "reports",
        "calendar",
        "messaging",
    }


def test_delete_user_removes_account_and_sessions():
    service = make_service()
    service.create_user(
        username="admin",
        password="StrongPassword123!",
        role="admin",
    )
    user = service.create_user(
        username="temporary.coach",
        password="StrongPassword123!",
        role="coach",
    )
    session = service.create_session(user)

    assert service.delete_user(user.user_id) is True
    assert service.delete_user(user.user_id) is False
    assert service.get_session_user(session.token) is None
    assert service.authenticate("temporary.coach", "StrongPassword123!") is None


def test_last_active_administrator_cannot_be_deleted():
    service = make_service()
    admin = service.create_user(
        username="admin",
        password="StrongPassword123!",
        role="admin",
    )

    try:
        service.delete_user(admin.user_id)
    except ValueError as error:
        assert str(error) == "Cannot delete the last active administrator"
    else:
        raise AssertionError("Expected the last-admin deletion safeguard")
