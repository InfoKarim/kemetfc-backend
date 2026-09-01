from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.notification_service import NotificationService

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return NotificationService(db=TestingSessionLocal())


def test_create_and_list_notifications():
    service = make_service()

    service.create_notification(
        user_id="U1", type="message", title="New message", body="Hello"
    )
    service.create_notification(
        user_id="U1", type="assessment", title="New assessment", body="Ready",
        link="/assessments-dashboard",
    )
    service.create_notification(
        user_id="U2", type="message", title="Not yours", body="Ignore"
    )

    notifications = service.list_for_user("U1")

    assert len(notifications) == 2
    assert {n.title for n in notifications} == {"New message", "New assessment"}


def test_unread_count_only_counts_own_unread_notifications():
    service = make_service()

    service.create_notification(user_id="U1", type="message", title="A", body="a")
    service.create_notification(user_id="U1", type="message", title="B", body="b")
    service.create_notification(user_id="U2", type="message", title="C", body="c")

    assert service.unread_count("U1") == 2
    assert service.unread_count("U2") == 1


def test_mark_read_updates_own_notification():
    service = make_service()

    notification = service.create_notification(
        user_id="U1", type="message", title="A", body="a"
    )

    updated = service.mark_read(notification.notification_id, "U1")

    assert updated is True
    assert service.unread_count("U1") == 0


def test_mark_read_returns_false_for_missing_notification():
    service = make_service()

    assert service.mark_read("does-not-exist", "U1") is False


def test_mark_read_returns_false_for_someone_elses_notification():
    service = make_service()

    notification = service.create_notification(
        user_id="U1", type="message", title="A", body="a"
    )

    updated = service.mark_read(notification.notification_id, "U2")

    assert updated is False
    assert service.unread_count("U1") == 1


def test_mark_all_read_only_affects_own_notifications():
    service = make_service()

    service.create_notification(user_id="U1", type="message", title="A", body="a")
    service.create_notification(user_id="U1", type="message", title="B", body="b")
    service.create_notification(user_id="U2", type="message", title="C", body="c")

    service.mark_all_read("U1")

    assert service.unread_count("U1") == 0
    assert service.unread_count("U2") == 1
