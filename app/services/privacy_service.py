from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import (
    AnalysisDB,
    AuditEventDB,
    DataRecordDB,
    GuardianConsentDB,
    GuardianPlayerLinkDB,
    PlayerDB,
    PrivacyRequestDB,
    TrainingPlanDB,
    UserDB,
    VideoDB,
)
from app.services.auth_service import utcnow


VIDEO_ANALYSIS_PURPOSE = "video_analysis"


class PrivacyService:
    def __init__(self, db: Session):
        self.db = db

    def grant_consent(
        self,
        consent_id: str,
        player_id: str,
        guardian_name: str,
        guardian_email: str,
        verification_method: str,
        purposes: list[str],
        recorded_by_user_id: str,
        expires_at: datetime | None = None,
    ) -> GuardianConsentDB:
        if self.db.get(GuardianConsentDB, consent_id) is not None:
            raise ValueError("Consent already exists")

        normalized_purposes = sorted(
            {purpose.strip() for purpose in purposes if purpose.strip()}
        )

        if not normalized_purposes:
            raise ValueError("At least one consent purpose is required")

        now = utcnow()

        if expires_at is not None and expires_at <= now:
            raise ValueError("Consent expiry must be in the future")

        consent = GuardianConsentDB(
            consent_id=consent_id.strip(),
            player_id=player_id,
            guardian_name=guardian_name.strip(),
            guardian_email=guardian_email.strip().lower(),
            verification_method=verification_method.strip(),
            purposes=normalized_purposes,
            granted_at=now,
            expires_at=expires_at,
            withdrawn_at=None,
            recorded_by_user_id=recorded_by_user_id,
        )
        self.db.add(consent)
        self._audit(
            actor_user_id=recorded_by_user_id,
            action="guardian_consent_granted",
            resource_type="player",
            resource_id=player_id,
            details={
                "consent_id": consent_id,
                "purposes": normalized_purposes,
            },
        )
        self.db.commit()
        self.db.refresh(consent)
        return consent

    def link_guardian_to_player(
        self,
        guardian_user_id: str,
        player_id: str,
        actor_user_id: str,
    ) -> GuardianPlayerLinkDB:
        guardian = self.db.get(UserDB, guardian_user_id)

        if guardian is None or guardian.role != "guardian" or not guardian.active:
            raise ValueError("Active guardian user not found")

        if self.db.get(PlayerDB, player_id) is None:
            raise ValueError("Player not found")

        key = (guardian_user_id, player_id)
        existing = self.db.get(GuardianPlayerLinkDB, key)

        if existing is not None:
            return existing

        link = GuardianPlayerLinkDB(
            guardian_user_id=guardian_user_id,
            player_id=player_id,
            created_at=utcnow(),
            created_by_user_id=actor_user_id,
        )
        self.db.add(link)
        self._audit(
            actor_user_id=actor_user_id,
            action="guardian_link_created",
            resource_type="player",
            resource_id=player_id,
            details={"guardian_user_id": guardian_user_id},
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    def guardian_can_access_player(
        self,
        guardian_user_id: str,
        player_id: str,
    ) -> bool:
        return self.db.get(
            GuardianPlayerLinkDB,
            (guardian_user_id, player_id),
        ) is not None

    def list_guardian_players(self, guardian_user_id: str) -> list[PlayerDB]:
        return (
            self.db.query(PlayerDB)
            .join(
                GuardianPlayerLinkDB,
                GuardianPlayerLinkDB.player_id == PlayerDB.player_id,
            )
            .filter(
                GuardianPlayerLinkDB.guardian_user_id == guardian_user_id
            )
            .order_by(PlayerDB.first_name_en, PlayerDB.last_name_en)
            .all()
        )

    def build_child_export(
        self,
        guardian_user_id: str,
        player_id: str,
    ) -> dict:
        if not self.guardian_can_access_player(guardian_user_id, player_id):
            raise ValueError("Linked child not found")

        player = self.db.get(PlayerDB, player_id)
        records = (
            self.db.query(DataRecordDB)
            .filter(DataRecordDB.player_id == player_id)
            .all()
        )
        record_ids = [record.record_id for record in records]
        videos = (
            self.db.query(VideoDB)
            .filter(VideoDB.record_id.in_(record_ids))
            .all()
            if record_ids
            else []
        )
        analyses = (
            self.db.query(AnalysisDB)
            .filter(AnalysisDB.player_id == player_id)
            .all()
        )
        plans = (
            self.db.query(TrainingPlanDB)
            .filter(TrainingPlanDB.player_id == player_id)
            .all()
        )
        consents = self.list_player_consents(player_id)

        def values(row, excluded: set[str] | None = None) -> dict:
            blocked = excluded or set()
            return {
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
                if column.name not in blocked
            }

        export = {
            "generated_at": utcnow(),
            "player": values(player),
            "data_records": [
                values(record, {"original_file_path"})
                for record in records
            ],
            "videos": [values(video, {"file_path"}) for video in videos],
            "analyses": [
                values(analysis, {"raw_output_path"})
                for analysis in analyses
            ],
            "training_plans": [values(plan) for plan in plans],
            "consents": [
                {
                    "consent_id": consent.consent_id,
                    "purposes": consent.purposes,
                    "granted_at": consent.granted_at,
                    "expires_at": consent.expires_at,
                    "withdrawn_at": consent.withdrawn_at,
                }
                for consent in consents
            ],
        }
        self.record_event(
            actor_user_id=guardian_user_id,
            action="child_data_exported",
            resource_type="player",
            resource_id=player_id,
        )
        return export

    def create_deletion_request(
        self,
        request_id: str,
        guardian_user_id: str,
        player_id: str,
        reason: str | None = None,
    ) -> PrivacyRequestDB:
        if not self.guardian_can_access_player(guardian_user_id, player_id):
            raise ValueError("Linked child not found")

        if self.db.get(PrivacyRequestDB, request_id) is not None:
            raise ValueError("Privacy request already exists")

        existing = (
            self.db.query(PrivacyRequestDB)
            .filter(
                PrivacyRequestDB.guardian_user_id == guardian_user_id,
                PrivacyRequestDB.player_id == player_id,
                PrivacyRequestDB.request_type == "delete",
                PrivacyRequestDB.status.in_(["pending", "in_review"]),
            )
            .first()
        )

        if existing is not None:
            raise ValueError("An active deletion request already exists")

        privacy_request = PrivacyRequestDB(
            request_id=request_id,
            guardian_user_id=guardian_user_id,
            player_id=player_id,
            request_type="delete",
            status="pending",
            reason=reason.strip() if reason else None,
            created_at=utcnow(),
            reviewed_at=None,
            reviewed_by_user_id=None,
            review_notes=None,
        )
        self.db.add(privacy_request)
        self._audit(
            actor_user_id=guardian_user_id,
            action="child_deletion_requested",
            resource_type="player",
            resource_id=player_id,
            details={"request_id": request_id},
        )
        self.db.commit()
        self.db.refresh(privacy_request)
        return privacy_request

    def list_privacy_requests(self) -> list[PrivacyRequestDB]:
        return (
            self.db.query(PrivacyRequestDB)
            .order_by(PrivacyRequestDB.created_at.desc())
            .all()
        )

    def review_privacy_request(
        self,
        request_id: str,
        status: str,
        reviewer_user_id: str,
        review_notes: str | None = None,
    ) -> PrivacyRequestDB | None:
        privacy_request = self.db.get(PrivacyRequestDB, request_id)

        if privacy_request is None:
            return None

        if privacy_request.status != "pending":
            raise ValueError("Only pending privacy requests can be reviewed")

        if status not in {"in_review", "rejected"}:
            raise ValueError("Invalid privacy request review status")

        privacy_request.status = status
        privacy_request.reviewed_at = utcnow()
        privacy_request.reviewed_by_user_id = reviewer_user_id
        privacy_request.review_notes = (
            review_notes.strip() if review_notes else None
        )
        self._audit(
            actor_user_id=reviewer_user_id,
            action=f"privacy_request_{status}",
            resource_type="privacy_request",
            resource_id=request_id,
            details={"player_id": privacy_request.player_id},
        )
        self.db.commit()
        self.db.refresh(privacy_request)
        return privacy_request

    def complete_deletion_request(
        self,
        request_id: str,
        reviewer_user_id: str,
        review_notes: str | None = None,
    ) -> PrivacyRequestDB | None:
        privacy_request = self.db.get(PrivacyRequestDB, request_id)

        if privacy_request is None:
            return None
        if privacy_request.status != "in_review":
            raise ValueError("Only in-review deletion requests can be completed")

        player_id = privacy_request.player_id
        records = self.db.query(DataRecordDB).filter(
            DataRecordDB.player_id == player_id
        ).all()
        record_ids = [record.record_id for record in records]
        videos = (
            self.db.query(VideoDB)
            .filter(VideoDB.record_id.in_(record_ids))
            .all()
            if record_ids
            else []
        )
        video_ids = [video.video_id for video in videos]

        if video_ids:
            from app.db_models import VideoAnalysisJobDB
            from app.analysis_result_storage import (
                get_analysis_result_storage,
            )
            from app.video_storage import get_video_storage

            jobs = self.db.query(VideoAnalysisJobDB).filter(
                VideoAnalysisJobDB.video_id.in_(video_ids)
            ).all()

            for job in jobs:
                if job.result_path:
                    get_analysis_result_storage().delete(
                        job.job_id,
                        job.result_path,
                    )

            storage = get_video_storage()

            for video in videos:
                if video.file_path.startswith("/uploads/videos/"):
                    storage.delete(video.file_path.rsplit("/", 1)[-1])

            self.db.query(VideoAnalysisJobDB).filter(
                VideoAnalysisJobDB.video_id.in_(video_ids)
            ).delete(synchronize_session=False)

        self.db.query(TrainingPlanDB).filter(
            TrainingPlanDB.player_id == player_id
        ).delete(synchronize_session=False)
        self.db.query(AnalysisDB).filter(
            AnalysisDB.player_id == player_id
        ).delete(synchronize_session=False)

        for video in videos:
            self.db.delete(video)
        for record in records:
            self.db.delete(record)

        self.db.query(GuardianConsentDB).filter(
            GuardianConsentDB.player_id == player_id
        ).delete(synchronize_session=False)
        self.db.query(GuardianPlayerLinkDB).filter(
            GuardianPlayerLinkDB.player_id == player_id
        ).delete(synchronize_session=False)

        player = self.db.get(PlayerDB, player_id)

        if player is not None:
            player.first_name_ar = "Deleted"
            player.last_name_ar = "Player"
            player.first_name_en = "Deleted"
            player.last_name_en = "Player"
            player.date_of_birth = datetime(1900, 1, 1).date()
            player.sex = "undisclosed"
            player.team_id = None
            player.physical_profile = {
                "height_cm": 0.0,
                "weight_kg": 0.0,
                "dominant_foot": "undisclosed",
                "speed": 0.0,
                "acceleration": 0.0,
                "agility": 0.0,
                "stamina": 0.0,
                "strength": 0.0,
            }
            player.technical_profile = {
                "ball_control": 0.0,
                "dribbling": 0.0,
                "passing": 0.0,
                "shooting": 0.0,
                "finishing": 0.0,
            }
            player.mental_profile = {
                "decision_making": 0.0,
                "concentration": 0.0,
                "composure": 0.0,
                "positioning": 0.0,
                "vision": 0.0,
            }
            player.match_performance = {
                "minutes_played": 0,
                "goals": 0,
                "assists": 0,
                "shots": 0,
                "shots_on_target": 0,
                "passes_attempted": 0,
                "passes_completed": 0,
                "tackles": 0,
                "interceptions": 0,
                "rating": 0.0,
            }

        privacy_request.status = "completed"
        privacy_request.reviewed_at = utcnow()
        privacy_request.reviewed_by_user_id = reviewer_user_id
        privacy_request.review_notes = (
            review_notes.strip() if review_notes else None
        )
        self._audit(
            actor_user_id=reviewer_user_id,
            action="child_deletion_completed",
            resource_type="privacy_request",
            resource_id=request_id,
            details={"player_id": player_id},
        )
        self.db.commit()
        self.db.refresh(privacy_request)
        return privacy_request

    def withdraw_consent(
        self,
        consent_id: str,
        actor_user_id: str,
    ) -> GuardianConsentDB | None:
        consent = self.db.get(GuardianConsentDB, consent_id)

        if consent is None:
            return None

        if consent.withdrawn_at is None:
            consent.withdrawn_at = utcnow()
            self._audit(
                actor_user_id=actor_user_id,
                action="guardian_consent_withdrawn",
                resource_type="player",
                resource_id=consent.player_id,
                details={"consent_id": consent_id},
            )
            self.db.commit()
            self.db.refresh(consent)

        return consent

    def list_player_consents(self, player_id: str) -> list[GuardianConsentDB]:
        return (
            self.db.query(GuardianConsentDB)
            .filter(GuardianConsentDB.player_id == player_id)
            .order_by(GuardianConsentDB.granted_at.desc())
            .all()
        )

    def has_active_consent(
        self,
        player_id: str,
        purpose: str,
        now: datetime | None = None,
    ) -> bool:
        current = now or utcnow()
        consents = self.list_player_consents(player_id)
        return any(
            consent.withdrawn_at is None
            and (consent.expires_at is None or consent.expires_at > current)
            and purpose in consent.purposes
            for consent in consents
        )

    def record_event(
        self,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict | None = None,
    ) -> None:
        self._audit(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self.db.commit()

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
