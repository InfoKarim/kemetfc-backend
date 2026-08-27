from sqlalchemy.orm import Session

from app.api_schemas import PublicRegistrationSchema
from app.db_models import AssessmentRegistrationDB
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id


class RegistrationService:
    def __init__(self, db: Session):
        self.db = db

    def create_registration(
        self,
        payload: PublicRegistrationSchema,
    ) -> AssessmentRegistrationDB:
        registration = AssessmentRegistrationDB(
            registration_id=next_entity_id(self.db, "registration"),
            parent_name=payload.parent_name,
            parent_email=payload.parent_email,
            parent_phone=payload.parent_phone,
            emergency_contact=payload.emergency_contact,
            player_name=payload.player_name,
            player_date_of_birth=payload.player_date_of_birth,
            player_age=payload.player_age,
            preferred_position=payload.preferred_position,
            experience_level=payload.experience_level,
            current_team=payload.current_team,
            consents=payload.consents.model_dump(),
            submitted_at=utcnow(),
        )
        self.db.add(registration)
        self.db.commit()
        self.db.refresh(registration)
        return registration

    def list_registrations(self) -> list[AssessmentRegistrationDB]:
        return (
            self.db.query(AssessmentRegistrationDB)
            .order_by(AssessmentRegistrationDB.submitted_at.desc())
            .all()
        )

    def delete_registration(self, registration_id: str) -> bool:
        registration = self.db.get(AssessmentRegistrationDB, registration_id)

        if registration is None:
            return False

        self.db.delete(registration)
        self.db.commit()
        return True
