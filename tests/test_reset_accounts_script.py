from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import AuthSessionDB
from app.services.auth_service import AuthService
from scripts.reset_accounts import reset_accounts


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine)


def test_reset_accounts_preserves_one_fresh_admin_and_revokes_sessions():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    service = AuthService(db=db)
    old_admin = service.create_user(
        username="karim",
        password="OldAdminPassword123!",
        role="admin",
    )
    coach = service.create_user(
        username="coach.one",
        password="CoachPassword123!",
        role="coach",
    )
    service.create_session(old_admin)
    service.create_session(coach)

    admin, previous_count = reset_accounts(
        db=db,
        admin_username="karim",
        admin_password="FreshAdminPassword123!",
    )

    users = service.list_users()
    assert previous_count == 2
    assert admin.user_id == old_admin.user_id
    assert admin.role == "admin"
    assert admin.active is True
    assert next(user for user in users if user.username == "coach.one").active is False
    assert db.query(AuthSessionDB).count() == 0
    assert service.authenticate("karim", "OldAdminPassword123!") is None
    assert service.authenticate("karim", "FreshAdminPassword123!") == admin
    db.close()
