from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import MessageDB, UserDB
from app.services.auth_service import utcnow
from app.services.notification_service import NotificationService


class MessageService:
    def __init__(self, db: Session):
        self.db = db

    def send_message(
        self,
        sender_id: str | None,
        recipient_id: str,
        subject: str,
        body: str,
    ) -> MessageDB:
        if self.db.get(UserDB, recipient_id) is None:
            raise ValueError("Recipient not found")

        message = MessageDB(
            message_id=str(uuid4()),
            sender_id=sender_id,
            recipient_id=recipient_id,
            subject=subject,
            body=body,
            read=False,
            created_at=utcnow(),
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)

        sender = self.db.get(UserDB, sender_id) if sender_id else None
        NotificationService(db=self.db).create_notification(
            user_id=recipient_id,
            type="message",
            title=f"New message from {sender.username if sender else 'Kemet FC'}",
            body=subject,
            link="/messages-page",
        )

        return message

    def inbox_for_user(self, user_id: str, limit: int = 50) -> list[MessageDB]:
        return (
            self.db.query(MessageDB)
            .filter(MessageDB.recipient_id == user_id)
            .order_by(MessageDB.created_at.desc())
            .limit(limit)
            .all()
        )

    def unread_count(self, user_id: str) -> int:
        return (
            self.db.query(MessageDB)
            .filter(
                MessageDB.recipient_id == user_id,
                MessageDB.read.is_(False),
            )
            .count()
        )

    def mark_read(self, message_id: str, user_id: str) -> bool:
        message = self.db.get(MessageDB, message_id)

        if message is None or message.recipient_id != user_id:
            return False

        message.read = True
        self.db.commit()
        return True
