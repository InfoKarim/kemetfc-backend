from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import NotificationDB
from app.services.auth_service import utcnow


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def create_notification(
        self,
        user_id: str,
        type: str,
        title: str,
        body: str,
        link: str | None = None,
    ) -> NotificationDB:
        notification = NotificationDB(
            notification_id=str(uuid4()),
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            link=link,
            read=False,
            created_at=utcnow(),
        )
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        return notification

    def list_for_user(self, user_id: str, limit: int = 20) -> list[NotificationDB]:
        return (
            self.db.query(NotificationDB)
            .filter(NotificationDB.user_id == user_id)
            .order_by(NotificationDB.created_at.desc())
            .limit(limit)
            .all()
        )

    def unread_count(self, user_id: str) -> int:
        return (
            self.db.query(NotificationDB)
            .filter(
                NotificationDB.user_id == user_id,
                NotificationDB.read.is_(False),
            )
            .count()
        )

    def mark_read(self, notification_id: str, user_id: str) -> bool:
        notification = self.db.get(NotificationDB, notification_id)

        if notification is None or notification.user_id != user_id:
            return False

        notification.read = True
        self.db.commit()
        return True

    def mark_all_read(self, user_id: str) -> None:
        self.db.query(NotificationDB).filter(
            NotificationDB.user_id == user_id,
            NotificationDB.read.is_(False),
        ).update({"read": True})
        self.db.commit()
