from sqlalchemy.orm import Session

from app.db_models import DataRecordDB, MLDatasetEntryDB, VideoDB
from app.services.auth_service import utcnow
from app.services.privacy_service import PrivacyService


ML_TRAINING_PURPOSE = "ml_training"


class MLDatasetEntryError(ValueError):
    pass


class MLDatasetService:
    def __init__(self, db: Session):
        self.db = db

    def _video_player_id(self, video: VideoDB) -> str | None:
        record = self.db.get(DataRecordDB, video.record_id)
        return record.player_id if record else None

    def flag_video(
        self,
        entry_id: str,
        video_id: str,
        team_id: str | None,
        age_band: str,
        sex_cohort: str,
        camera_id: str,
        lighting: str,
        flagged_by_user_id: str,
        notes: str | None = None,
    ) -> MLDatasetEntryDB:
        video = self.db.get(VideoDB, video_id)
        if video is None:
            raise MLDatasetEntryError("Video not found")

        if self.db.query(MLDatasetEntryDB).filter(
            MLDatasetEntryDB.video_id == video_id
        ).first() is not None:
            raise MLDatasetEntryError(
                "This video is already flagged for the ML dataset"
            )

        player_id = self._video_player_id(video)
        if player_id is None:
            raise MLDatasetEntryError(
                "This video has no associated player record to check consent against"
            )

        privacy_service = PrivacyService(db=self.db)
        matching_consent = next(
            (
                consent
                for consent in privacy_service.list_player_consents(player_id)
                if consent.withdrawn_at is None
                and (consent.expires_at is None or consent.expires_at > utcnow())
                and ML_TRAINING_PURPOSE in consent.purposes
            ),
            None,
        )
        if matching_consent is None:
            raise MLDatasetEntryError(
                "No active guardian consent for ML training exists for this "
                "video's associated player"
            )

        entry = MLDatasetEntryDB(
            entry_id=entry_id,
            video_id=video_id,
            team_id=team_id,
            age_band=age_band,
            sex_cohort=sex_cohort,
            camera_id=camera_id,
            lighting=lighting,
            consent_id=matching_consent.consent_id,
            status="pending_review",
            notes=notes,
            flagged_by_user_id=flagged_by_user_id,
            flagged_at=utcnow(),
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def list_entries(self) -> list[MLDatasetEntryDB]:
        return (
            self.db.query(MLDatasetEntryDB)
            .order_by(MLDatasetEntryDB.flagged_at.desc())
            .all()
        )

    def review_entry(
        self,
        entry_id: str,
        status: str,
        reviewed_by_user_id: str,
    ) -> MLDatasetEntryDB | None:
        entry = self.db.get(MLDatasetEntryDB, entry_id)
        if entry is None:
            return None

        entry.status = status
        entry.reviewed_by_user_id = reviewed_by_user_id
        entry.reviewed_at = utcnow()
        self.db.commit()
        self.db.refresh(entry)
        return entry
