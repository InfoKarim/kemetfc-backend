from sqlalchemy.orm import Session

from app.api_schemas import PublicContactMessageSchema
from app.db_models import ContactMessageDB
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id


class ContactMessageService:
    def __init__(self, db: Session):
        self.db = db

    def create_message(
        self,
        payload: PublicContactMessageSchema,
    ) -> ContactMessageDB:
        message = ContactMessageDB(
            message_id=next_entity_id(self.db, "contact_message"),
            name=payload.name,
            email=payload.email,
            topic=payload.topic,
            message=payload.message,
            submitted_at=utcnow(),
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def list_messages(self) -> list[ContactMessageDB]:
        return (
            self.db.query(ContactMessageDB)
            .order_by(ContactMessageDB.submitted_at.desc())
            .all()
        )

    def delete_message(self, message_id: str) -> bool:
        message = self.db.get(ContactMessageDB, message_id)

        if message is None:
            return False

        self.db.delete(message)
        self.db.commit()
        return True
