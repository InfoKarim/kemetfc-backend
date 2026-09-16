import re
import secrets
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api_schemas import CreatePlayerFromRegistrationSchema, PublicRegistrationSchema
from app.db_models import AssessmentRegistrationDB, AuditEventDB, UserDB
from app.mental_profile import MentalProfile
from app.physical_profile import PhysicalProfile
from app.player import Player
from app.services.auth_service import AuthService, utcnow
from app.services.id_service import next_entity_id
from app.services.player_service import PlayerService
from app.services.privacy_service import PrivacyService
from app.tactical_profile import TacticalProfile
from app.technical_profile import TechnicalProfile
from app.weak_foot_profile import WeakFootProfile
from app.match_performance import MatchPerformance


class RegistrationNotFoundError(ValueError):
    pass


class RegistrationAlreadyLinkedError(ValueError):
    """The registration already has a player created from it."""

    def __init__(self, player_id: str):
        self.player_id = player_id
        super().__init__(f"Player already created from this registration: {player_id}")


class PossibleDuplicatePlayerError(ValueError):
    """A player with the same name and date of birth already exists —
    surfaced to staff for a decision (view existing / create anyway), never
    silently resolved either way."""

    def __init__(self, candidates: list[Player]):
        self.candidates = candidates
        super().__init__("A possible duplicate player already exists")


def split_player_name(full_name: str) -> tuple[str, str]:
    """First word is the first name, the rest is the last name — matches
    the same convention already used client-side in registrations.html's
    (now superseded) quick pre-fill link, so a name splits the same way
    everywhere in the system."""
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:]) or parts[0]


def _derive_guardian_username(db: Session, email: str) -> str:
    local_part = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9._-]", "", local_part) or "guardian"
    base = base[:60]

    candidate = base
    suffix = 1
    while db.query(UserDB).filter(UserDB.username == candidate).first() is not None:
        suffix += 1
        candidate = f"{base}{suffix}"[:64]

    return candidate


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
            status="submitted",
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

    def update_status(
        self,
        registration_id: str,
        status: str,
        actor_user_id: str,
    ) -> AssessmentRegistrationDB:
        registration = self.db.get(AssessmentRegistrationDB, registration_id)
        if registration is None:
            raise RegistrationNotFoundError("Registration not found")

        if registration.status == "player_created":
            raise RegistrationAlreadyLinkedError(registration.player_id)

        registration.status = status
        self._audit(
            actor_user_id=actor_user_id,
            action="registration_status_changed",
            resource_type="registration",
            resource_id=registration_id,
            details={"new_status": status},
        )
        self.db.commit()
        self.db.refresh(registration)
        return registration

    def find_duplicate_players(
        self, registration: AssessmentRegistrationDB
    ) -> list[Player]:
        first_name, last_name = split_player_name(registration.player_name)
        return PlayerService(db=self.db).find_by_name_and_dob(
            first_name, last_name, registration.player_date_of_birth
        )

    def find_or_create_guardian(
        self,
        registration: AssessmentRegistrationDB,
        actor_user_id: str,
    ) -> tuple[UserDB, bool]:
        """Exact, case-insensitive email match only — per policy, never
        merge/reuse a guardian account based on fuzzy name matching."""
        email = registration.parent_email.strip().lower()

        existing = (
            self.db.query(UserDB)
            .filter(
                UserDB.role == "guardian",
                func.lower(UserDB.email) == email,
            )
            .first()
        )
        if existing is not None:
            return existing, False

        first_name, last_name = split_player_name(registration.parent_name)
        username = _derive_guardian_username(self.db, email)

        guardian = AuthService(db=self.db).create_user(
            username=username,
            # Random and never surfaced anywhere — the guardian sets their
            # own password via the existing "Forgot your password?" email
            # flow (POST /auth/password-reset/request), the same one every
            # other account recovery already uses.
            password=secrets.token_urlsafe(24),
            role="guardian",
            email=registration.parent_email,
            first_name=first_name,
            last_name=last_name,
            phone=registration.parent_phone,
        )
        self._audit(
            actor_user_id=actor_user_id,
            action="guardian_account_created_from_registration",
            resource_type="user",
            resource_id=guardian.user_id,
            details={"registration_id": registration.registration_id},
        )
        return guardian, True

    def create_player_from_registration(
        self,
        registration_id: str,
        payload: CreatePlayerFromRegistrationSchema,
        actor_user_id: str,
    ) -> dict:
        registration = self.db.get(AssessmentRegistrationDB, registration_id)
        if registration is None:
            raise RegistrationNotFoundError("Registration not found")

        if registration.player_id is not None:
            raise RegistrationAlreadyLinkedError(registration.player_id)

        if not payload.confirm_duplicate:
            duplicates = self.find_duplicate_players(registration)
            if duplicates:
                raise PossibleDuplicatePlayerError(duplicates)

        guardian, guardian_created = self.find_or_create_guardian(
            registration, actor_user_id
        )

        first_name_en, last_name_en = split_player_name(registration.player_name)
        player_id = next_entity_id(self.db, "player")

        player = Player(
            player_id=player_id,
            first_name_ar=payload.first_name_ar,
            last_name_ar=payload.last_name_ar,
            first_name_en=first_name_en,
            last_name_en=last_name_en,
            date_of_birth=registration.player_date_of_birth,
            sex=payload.sex,
            team_id=payload.team_id,
            physical_profile=PhysicalProfile(**payload.physical_profile.model_dump()),
            technical_profile=TechnicalProfile(
                **payload.technical_profile.model_dump()
            ),
            mental_profile=MentalProfile(**payload.mental_profile.model_dump()),
            match_performance=MatchPerformance(
                **payload.match_performance.model_dump()
            ),
            tactical_profile=TacticalProfile(**payload.tactical_profile.model_dump()),
            weak_foot_profile=WeakFootProfile(
                **payload.weak_foot_profile.model_dump()
            ),
            created_at=utcnow(),
            source="registration",
            created_by_user_id=actor_user_id,
        )
        PlayerService(db=self.db).add_player(player)

        PrivacyService(db=self.db).link_guardian_to_player(
            guardian_user_id=guardian.user_id,
            player_id=player_id,
            actor_user_id=actor_user_id,
        )

        registration.status = "player_created"
        registration.player_id = player_id
        registration.linked_by_user_id = actor_user_id
        registration.linked_at = utcnow()

        self._audit(
            actor_user_id=actor_user_id,
            action="player_created_from_registration",
            resource_type="player",
            resource_id=player_id,
            details={
                "registration_id": registration.registration_id,
                "guardian_user_id": guardian.user_id,
                "guardian_account_created": guardian_created,
            },
        )

        self.db.commit()
        self.db.refresh(registration)

        return {
            "player": player,
            "guardian": guardian,
            "guardian_created": guardian_created,
            "registration": registration,
        }

    def _audit(
        self,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict,
    ) -> None:
        self.db.add(
            AuditEventDB(
                event_id=str(uuid4()),
                occurred_at=utcnow(),
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
            )
        )
