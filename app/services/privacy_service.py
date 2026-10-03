from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db_models import (
    AnalysisDB,
    AuditEventDB,
    CoachMessageDB,
    CoachValidationLabelDB,
    DataRecordDB,
    GuardianConsentDB,
    GuardianPlayerLinkDB,
    MLDatasetEntryDB,
    MLPredictionDB,
    MonteCarloSimulationDB,
    PlayerAssessmentDB,
    PlayerCheckInTokenDB,
    PlayerDB,
    PlayerMetricDB,
    PredictionOutcomeDB,
    PrivacyRequestDB,
    TrackingEventDB,
    TrackingFeatureSetDB,
    TrackingSampleDB,
    TrackingSessionDB,
    TrainingPlanDB,
    UserDB,
    VideoDB,
)
from app.services.auth_service import utcnow
from app.v1.models import (
    V1AssessmentDB,
    V1ManualMeasurementCorrectionDB,
    V1ManualMeasurementDB,
    V1StationRunDB,
    V1TrialDB,
)
from app.v1.timing import format_seconds


VIDEO_ANALYSIS_PURPOSE = "video_analysis"

# The state complete_deletion_request leaves a player in. There is no separate
# "deleted" flag: this is the existing anonymised-player marker.
ANONYMISED_FIRST_NAME = "Deleted"
ANONYMISED_LAST_NAME = "Player"
ANONYMISED_DATE_OF_BIRTH = date(1900, 1, 1)

# What the guardian export says about itself. It must never imply "all data":
# each category states whether it is in the export, and where it is not, why.
# SEPARATE_DOWNLOAD names the intended contract; "download_available" says
# whether that download exists yet.
EXPORT_COMPLETENESS_STATEMENT = (
    "This export lists the categories below. It is not a complete copy of every "
    "piece of data held about the child: read the state of each category."
)
_NOT_PACKAGED = "NOT_INCLUDED"


def export_completeness() -> dict:
    included = {"state": "INCLUDED"}

    def not_included(note: str) -> dict:
        return {"state": _NOT_PACKAGED, "note": note}

    return {
        "statement": EXPORT_COMPLETENESS_STATEMENT,
        "categories": {
            "player": included,
            "data_records": {"state": "INCLUDED", "note": "File paths are not exported."},
            "videos": {"state": "INCLUDED", "note": "Metadata only; file paths are not exported."},
            "video_files": not_included(
                "Video files are not packaged in this export. Copies on devices, "
                "in backups or in other storage are not covered by it."
            ),
            "analyses": included,
            "training_plans": included,
            "player_assessments": included,
            "player_metrics": included,
            "ml_predictions": included,
            "monte_carlo_simulations": included,
            "prediction_outcomes": included,
            "coach_message": included,
            "tracking_sessions": {
                "state": "INCLUDED",
                "note": "Sessions with events, derived features and coach labels.",
            },
            "v1_assessments": {
                "state": "INCLUDED",
                "note": "V1 assessments with station runs, attempts, manual times and the "
                        "full correction history (original values are kept).",
            },
            "tracking_samples": {
                "state": "SEPARATE_DOWNLOAD",
                "download_available": False,
                "note": (
                    "Raw per-frame tracking samples are not part of this export. "
                    "They will be offered as a separate download; that download "
                    "does not exist yet. Only a sample count per session is listed."
                ),
            },
            "consents": {"state": "INCLUDED", "note": "Without guardian name or email."},
            "avatar_file": not_included("The image file is not packaged in this export."),
            "assessment_registrations": not_included("Policy decision pending."),
            "billing_records": not_included("Policy decision pending."),
            "dataset_entries": not_included("Policy decision pending."),
            "external_dataset_copies": not_included(
                "Copies made outside the platform are not tracked or exported by it."
            ),
            "check_in_tokens": not_included("Opaque access codes, not child data."),
            "audit_records": not_included("Internal operational records."),
        },
    }


def is_anonymised_player(player: PlayerDB) -> bool:
    return (
        player.first_name_en == ANONYMISED_FIRST_NAME
        and player.last_name_en == ANONYMISED_LAST_NAME
        and player.date_of_birth == ANONYMISED_DATE_OF_BIRTH
    )


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

        # Assessment, derived-progress and tracking data are already
        # readable by the linked guardian through the guardian-allowed
        # routes, so the export includes them. Raw per-frame telemetry
        # (TrackingSampleDB) is staff-only everywhere else in the app, so it
        # is reported only as a count per session, never as rows.
        assessments = (
            self.db.query(PlayerAssessmentDB)
            .filter(PlayerAssessmentDB.player_id == player_id)
            .order_by(PlayerAssessmentDB.created_at, PlayerAssessmentDB.assessment_id)
            .all()
        )
        metrics = (
            self.db.query(PlayerMetricDB)
            .filter(PlayerMetricDB.player_id == player_id)
            .order_by(PlayerMetricDB.metric_id)
            .all()
        )
        predictions = (
            self.db.query(MLPredictionDB)
            .filter(MLPredictionDB.player_id == player_id)
            .order_by(MLPredictionDB.created_at, MLPredictionDB.prediction_id)
            .all()
        )
        simulations = (
            self.db.query(MonteCarloSimulationDB)
            .filter(MonteCarloSimulationDB.player_id == player_id)
            .order_by(MonteCarloSimulationDB.created_at, MonteCarloSimulationDB.simulation_id)
            .all()
        )
        outcomes = (
            self.db.query(PredictionOutcomeDB)
            .filter(PredictionOutcomeDB.player_id == player_id)
            .order_by(PredictionOutcomeDB.created_at, PredictionOutcomeDB.outcome_id)
            .all()
        )
        coach_message = self.db.get(CoachMessageDB, player_id)
        tracking_sessions = (
            self.db.query(TrackingSessionDB)
            .filter(TrackingSessionDB.player_id == player_id)
            .order_by(TrackingSessionDB.started_at, TrackingSessionDB.session_id)
            .all()
        )
        v1_export = self._export_v1_assessments(player_id)
        tracking_export = []

        for session in tracking_sessions:
            feature_set = self.db.get(TrackingFeatureSetDB, session.session_id)
            events = (
                self.db.query(TrackingEventDB)
                .filter(TrackingEventDB.session_id == session.session_id)
                .order_by(TrackingEventDB.occurred_at, TrackingEventDB.event_id)
                .all()
            )
            labels = (
                self.db.query(CoachValidationLabelDB)
                .filter(CoachValidationLabelDB.session_id == session.session_id)
                .order_by(CoachValidationLabelDB.created_at, CoachValidationLabelDB.label_id)
                .all()
            )
            sample_count = (
                self.db.query(func.count(TrackingSampleDB.sample_id))
                .filter(TrackingSampleDB.session_id == session.session_id)
                .scalar()
            )
            tracking_export.append({
                **values(session),
                "sample_count": sample_count or 0,
                "feature_set": values(feature_set) if feature_set else None,
                "events": [values(event) for event in events],
                "coach_labels": [values(label) for label in labels],
            })

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
            "player_assessments": [values(item) for item in assessments],
            "player_metrics": [values(item) for item in metrics],
            "ml_predictions": [values(item) for item in predictions],
            "monte_carlo_simulations": [values(item) for item in simulations],
            "prediction_outcomes": [values(item) for item in outcomes],
            "coach_message": values(coach_message) if coach_message else None,
            "tracking_sessions": tracking_export,
            "v1_assessments": v1_export,
            "completeness": export_completeness(),
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
            details={
                "result": "exported",
                "counts": {
                    "data_records": len(records),
                    "videos": len(videos),
                    "analyses": len(analyses),
                    "training_plans": len(plans),
                    "player_assessments": len(assessments),
                    "player_metrics": len(metrics),
                    "tracking_sessions": len(tracking_sessions),
                    "v1_assessments": len(v1_export),
                },
            },
        )
        return export

    def _export_v1_assessments(self, player_id: str) -> list[dict]:
        """V1 assessments as a nested tree. Times are exact decimal strings; every
        measurement carries its full correction history."""
        db = self.db

        def values(row) -> dict:
            return {c.name: getattr(row, c.name) for c in row.__table__.columns}

        result = []
        assessments = (
            db.query(V1AssessmentDB).filter(V1AssessmentDB.player_id == player_id)
            .order_by(V1AssessmentDB.created_at, V1AssessmentDB.assessment_id).all()
        )
        for assessment in assessments:
            runs = (
                db.query(V1StationRunDB)
                .filter(V1StationRunDB.assessment_id == assessment.assessment_id)
                .order_by(V1StationRunDB.station_id).all()
            )
            run_export = []
            for run in runs:
                trials = (
                    db.query(V1TrialDB).filter(V1TrialDB.station_run_id == run.station_run_id)
                    .order_by(V1TrialDB.series_key, V1TrialDB.attempt_number).all()
                )
                trial_export = []
                for trial in trials:
                    measurements = (
                        db.query(V1ManualMeasurementDB)
                        .filter(V1ManualMeasurementDB.trial_id == trial.trial_id)
                        .order_by(V1ManualMeasurementDB.manual_measurement_id).all()
                    )
                    measurement_export = []
                    for measurement in measurements:
                        corrections = (
                            db.query(V1ManualMeasurementCorrectionDB)
                            .filter(V1ManualMeasurementCorrectionDB.manual_measurement_id
                                    == measurement.manual_measurement_id)
                            .order_by(V1ManualMeasurementCorrectionDB.sequence_number).all()
                        )
                        effective = (
                            corrections[-1].corrected_centiseconds if corrections
                            else measurement.value_centiseconds
                        )
                        measurement_export.append({
                            **values(measurement),
                            "value_seconds": format_seconds(measurement.value_centiseconds),
                            "effective_value_seconds": format_seconds(effective),
                            "corrections": [
                                {
                                    **values(c),
                                    "original_seconds": format_seconds(c.original_centiseconds),
                                    "corrected_seconds": format_seconds(c.corrected_centiseconds),
                                }
                                for c in corrections
                            ],
                        })
                    trial_export.append({**values(trial), "manual_measurements": measurement_export})
                run_export.append({**values(run), "trials": trial_export})
            result.append({**values(assessment), "station_runs": run_export})
        return result

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
        removed = self._erase_assessment_and_tracking_data(player_id, video_ids)
        revoked_tokens = self.db.query(PlayerCheckInTokenDB).filter(
            PlayerCheckInTokenDB.player_id == player_id,
            PlayerCheckInTokenDB.revoked_at.is_(None),
        ).update({"revoked_at": utcnow()}, synchronize_session=False)

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

        # No ORM relationship links these tables, so the unit of work does not
        # order the two deletes itself: flush the videos first, otherwise the
        # data_records DELETE runs while videos still reference it and fails
        # on any database that enforces foreign keys (PostgreSQL).
        for video in videos:
            self.db.delete(video)
        self.db.flush()
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
            player.first_name_ar = ANONYMISED_FIRST_NAME
            player.last_name_ar = ANONYMISED_LAST_NAME
            player.first_name_en = ANONYMISED_FIRST_NAME
            player.last_name_en = ANONYMISED_LAST_NAME
            player.date_of_birth = ANONYMISED_DATE_OF_BIRTH
            # Database reference only. The image file itself is not touched
            # here: a safe file-deletion strategy has not been decided.
            player.photo_filename = None
            player.sex = "undisclosed"
            player.team_id = None
            player.tactical_profile = None
            player.weak_foot_profile = None
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
            details={
                "player_id": player_id,
                "result": "completed",
                "removed_counts": removed,
                "revoked_counts": {"player_checkin_tokens": revoked_tokens},
                "cleared_references": ["photo_filename"],
            },
        )
        self.db.commit()
        self.db.refresh(privacy_request)
        return privacy_request

    def _erase_assessment_and_tracking_data(
        self,
        player_id: str,
        video_ids: list[str],
    ) -> dict[str, int]:
        """Hard-deletes the child's assessment, derived-progress and tracking
        rows, the same way complete_deletion_request already hard-deletes the
        child's videos, analyses and plans. Runs inside the caller's
        transaction (no commit here) and touches only database rows - no
        files. Rows that belong to another player but point at something
        being removed are un-linked, never deleted.

        Returns row counts per table for the audit event (no row content).
        """
        db = self.db
        counts: dict[str, int] = {}

        def delete(name: str, query) -> None:
            counts[name] = query.delete(synchronize_session=False)

        session_ids = [
            row[0]
            for row in db.query(TrackingSessionDB.session_id)
            .filter(TrackingSessionDB.player_id == player_id)
            .all()
        ]
        assessment_ids = [
            row[0]
            for row in db.query(PlayerAssessmentDB.assessment_id)
            .filter(PlayerAssessmentDB.player_id == player_id)
            .all()
        ]
        prediction_ids = [
            row[0]
            for row in db.query(MLPredictionDB.prediction_id)
            .filter(MLPredictionDB.player_id == player_id)
            .all()
        ]
        consent_ids = [
            row[0]
            for row in db.query(GuardianConsentDB.consent_id)
            .filter(GuardianConsentDB.player_id == player_id)
            .all()
        ]

        # V1 assessment data, child to parent. Bulk deletes on purpose: the V1 models
        # refuse ORM deletes, so only this approved-privacy-request path can remove them.
        v1_assessment_ids = [
            row[0] for row in db.query(V1AssessmentDB.assessment_id)
            .filter(V1AssessmentDB.player_id == player_id).all()
        ]
        v1_run_ids = [
            row[0] for row in db.query(V1StationRunDB.station_run_id)
            .filter(V1StationRunDB.assessment_id.in_(v1_assessment_ids)).all()
        ]
        v1_trial_ids = [
            row[0] for row in db.query(V1TrialDB.trial_id)
            .filter(V1TrialDB.station_run_id.in_(v1_run_ids)).all()
        ]
        v1_measurement_ids = [
            row[0] for row in db.query(V1ManualMeasurementDB.manual_measurement_id)
            .filter(V1ManualMeasurementDB.trial_id.in_(v1_trial_ids)).all()
        ]
        delete("v1_manual_measurement_corrections", db.query(V1ManualMeasurementCorrectionDB).filter(
            V1ManualMeasurementCorrectionDB.manual_measurement_id.in_(v1_measurement_ids)))
        delete("v1_manual_measurements", db.query(V1ManualMeasurementDB).filter(
            V1ManualMeasurementDB.trial_id.in_(v1_trial_ids)))
        delete("v1_trials", db.query(V1TrialDB).filter(V1TrialDB.station_run_id.in_(v1_run_ids)))
        delete("v1_station_runs", db.query(V1StationRunDB).filter(
            V1StationRunDB.assessment_id.in_(v1_assessment_ids)))
        delete("v1_assessments", db.query(V1AssessmentDB).filter(V1AssessmentDB.player_id == player_id))

        # Forecast chain: outcome / simulation -> prediction.
        delete("prediction_outcomes", db.query(PredictionOutcomeDB).filter(or_(
            PredictionOutcomeDB.player_id == player_id,
            PredictionOutcomeDB.prediction_id.in_(prediction_ids),
        )))
        delete("monte_carlo_simulations", db.query(MonteCarloSimulationDB).filter(or_(
            MonteCarloSimulationDB.player_id == player_id,
            MonteCarloSimulationDB.prediction_id.in_(prediction_ids),
        )))
        delete("ml_predictions", db.query(MLPredictionDB).filter(
            MLPredictionDB.player_id == player_id
        ))

        # Another player's assessment must never be deleted just because it
        # points at one of this child's tracking sessions: un-link it.
        if session_ids:
            db.query(PlayerAssessmentDB).filter(
                PlayerAssessmentDB.player_id != player_id,
                PlayerAssessmentDB.tracking_session_id.in_(session_ids),
            ).update(
                {PlayerAssessmentDB.tracking_session_id: None},
                synchronize_session=False,
            )

        delete("player_metrics", db.query(PlayerMetricDB).filter(or_(
            PlayerMetricDB.player_id == player_id,
            PlayerMetricDB.assessment_id.in_(assessment_ids),
        )))
        delete("player_assessments", db.query(PlayerAssessmentDB).filter(
            PlayerAssessmentDB.player_id == player_id
        ))
        delete("coach_messages", db.query(CoachMessageDB).filter(
            CoachMessageDB.player_id == player_id
        ))

        delete("coach_validation_labels", db.query(CoachValidationLabelDB).filter(
            CoachValidationLabelDB.session_id.in_(session_ids)
        ))
        delete("tracking_events", db.query(TrackingEventDB).filter(
            TrackingEventDB.session_id.in_(session_ids)
        ))
        delete("tracking_feature_sets", db.query(TrackingFeatureSetDB).filter(
            TrackingFeatureSetDB.session_id.in_(session_ids)
        ))
        delete("tracking_samples", db.query(TrackingSampleDB).filter(
            TrackingSampleDB.session_id.in_(session_ids)
        ))
        delete("tracking_sessions", db.query(TrackingSessionDB).filter(
            TrackingSessionDB.player_id == player_id
        ))

        # A video being erased may be referenced by another player's tracking
        # session: keep that session, drop only the dead video reference.
        if video_ids:
            db.query(TrackingSessionDB).filter(
                TrackingSessionDB.video_id.in_(video_ids)
            ).update({TrackingSessionDB.video_id: None}, synchronize_session=False)

        # Dataset candidates reference the child's video and consent, both of
        # which this request removes.
        delete("ml_dataset_entries", db.query(MLDatasetEntryDB).filter(or_(
            MLDatasetEntryDB.video_id.in_(video_ids),
            MLDatasetEntryDB.consent_id.in_(consent_ids),
        )))

        return counts

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
