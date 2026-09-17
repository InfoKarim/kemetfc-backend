"""Smart Soccer Camera tracking: session lifecycle, sample/event ingest,
derived-feature caching, and publishing results to the existing
PlayerAssessmentDB — the single integration point between the iOS
tracking app and the rest of KEMET's assessment pipeline. Never invents
a player: every session requires an explicit, already-selected
player_id from the caller (see app/routers/tracking.py), exactly
matching the "coach selects player before recording" requirement."""

from datetime import date, datetime, UTC

from sqlalchemy.orm import Session

from app.db_models import (
    PlayerAssessmentDB,
    TrackingEventDB,
    TrackingFeatureSetDB,
    TrackingSampleDB,
    TrackingSessionDB,
)
from app.development_snapshot import calculate_player_age
from app.player import Player
from app.services import skill_inference
from app.services.id_service import next_entity_id
from app.services.tracking_features import FEATURE_SCHEMA_VERSION, build_feature_set
from app.services.tracking_quality import INSUFFICIENT_DATA, evaluate_session_quality

TRACKING_MODES = {"player_lock", "ball_track", "smart_soccer"}
TRACKING_ANALYSIS_METHODOLOGY_VERSION = "smart_soccer_tracking_v1"


class TrackingError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TrackingService:
    def __init__(self, db: Session):
        self.db = db

    def create_session(
        self,
        player_id: str,
        coach_user_id: str,
        tracking_mode: str,
        gimbal_model: str | None = None,
        calibration_scale_m_per_unit: float | None = None,
        player_detector_version: str | None = None,
        ball_detector_version: str | None = None,
        ball_model_status: str = "missing",
        pose_model_version: str | None = None,
        tracker_algorithm_version: str | None = None,
        framing_algorithm_version: str | None = None,
        ios_app_version: str | None = None,
    ) -> TrackingSessionDB:
        if tracking_mode not in TRACKING_MODES:
            raise TrackingError(f"Unknown tracking_mode: {tracking_mode}")

        now = _now()
        session = TrackingSessionDB(
            session_id=next_entity_id(self.db, "tracking_session"),
            player_id=player_id,
            coach_user_id=coach_user_id,
            tracking_mode=tracking_mode,
            gimbal_model=gimbal_model,
            calibration_status="calibrated" if calibration_scale_m_per_unit else "uncalibrated",
            calibration_scale_m_per_unit=calibration_scale_m_per_unit,
            status="recording",
            started_at=now,
            player_detector_version=player_detector_version,
            ball_detector_version=ball_detector_version,
            ball_model_status=ball_model_status,
            pose_model_version=pose_model_version,
            tracker_algorithm_version=tracker_algorithm_version,
            framing_algorithm_version=framing_algorithm_version,
            ios_app_version=ios_app_version,
            created_at=now,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session(self, session_id: str) -> TrackingSessionDB | None:
        return self.db.get(TrackingSessionDB, session_id)

    def list_sessions_for_player(self, player_id: str) -> list[TrackingSessionDB]:
        return (
            self.db.query(TrackingSessionDB)
            .filter(TrackingSessionDB.player_id == player_id)
            .order_by(TrackingSessionDB.started_at.desc())
            .all()
        )

    def ingest_samples(self, session_id: str, samples: list[dict]) -> int:
        """Batched ingest — the iOS client sends samples in bounded
        batches (see TRACKING_SAMPLE_HZ client-side), never one HTTP call
        per camera frame. Silently ignores an ingest call for a session
        that isn't actively recording, rather than erroring mid-upload
        for what's likely a late-arriving batch after Stop was pressed."""
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        rows = [
            TrackingSampleDB(
                session_id=session_id,
                t_seconds=sample["t_seconds"],
                player_bbox=sample.get("player_bbox"),
                player_center=sample.get("player_center"),
                player_confidence=sample.get("player_confidence"),
                player_track_id=sample.get("player_track_id"),
                ball_bbox=sample.get("ball_bbox"),
                ball_center=sample.get("ball_center"),
                ball_confidence=sample.get("ball_confidence"),
                ball_track_id=sample.get("ball_track_id"),
                pose_keypoints=sample.get("pose_keypoints"),
                gimbal_state=sample.get("gimbal_state", "unknown"),
                tracking_mode=sample.get("tracking_mode", session.tracking_mode),
                tracking_status=sample.get("tracking_status", "unknown"),
            )
            for sample in samples
        ]
        self.db.add_all(rows)
        self.db.commit()
        return len(rows)

    def ingest_event(self, session_id: str, event_type: str, details: dict | None = None) -> TrackingEventDB:
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        event = TrackingEventDB(
            event_id=next_entity_id(self.db, "tracking_event"),
            session_id=session_id,
            occurred_at=_now(),
            event_type=event_type,
            details=details or {},
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def list_events(self, session_id: str) -> list[TrackingEventDB]:
        return (
            self.db.query(TrackingEventDB)
            .filter(TrackingEventDB.session_id == session_id)
            .order_by(TrackingEventDB.occurred_at.asc())
            .all()
        )

    def list_samples(self, session_id: str) -> list[TrackingSampleDB]:
        return (
            self.db.query(TrackingSampleDB)
            .filter(TrackingSampleDB.session_id == session_id)
            .order_by(TrackingSampleDB.t_seconds.asc())
            .all()
        )

    def get_samples_as_dicts(self, session_id: str) -> list[dict]:
        return [
            {
                "t_seconds": s.t_seconds,
                "player_bbox": s.player_bbox,
                "player_center": s.player_center,
                "player_confidence": s.player_confidence,
                "ball_bbox": s.ball_bbox,
                "ball_center": s.ball_center,
                "ball_confidence": s.ball_confidence,
                "pose_keypoints": s.pose_keypoints,
                "tracking_status": s.tracking_status,
            }
            for s in self.list_samples(session_id)
        ]

    def complete_session(self, session_id: str, video_id: str | None = None) -> TrackingSessionDB:
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        session.status = "completed"
        session.ended_at = _now()
        if video_id is not None:
            session.video_id = video_id
        self.db.commit()
        self.db.refresh(session)

        # Compute and cache the feature set once, now, rather than making
        # every later report/heatmap request re-decode raw samples.
        self.get_or_compute_feature_set(session_id)
        return session

    def get_or_compute_feature_set(self, session_id: str) -> dict:
        cached = self.db.get(TrackingFeatureSetDB, session_id)
        if cached is not None and cached.feature_schema_version == FEATURE_SCHEMA_VERSION:
            return cached.features

        samples = self.get_samples_as_dicts(session_id)
        features = build_feature_set(samples)

        if cached is not None:
            cached.features = features
            cached.feature_schema_version = FEATURE_SCHEMA_VERSION
            cached.computed_at = _now()
        else:
            self.db.add(TrackingFeatureSetDB(
                session_id=session_id,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                features=features,
                computed_at=_now(),
            ))
        self.db.commit()
        return features

    def get_quality(self, session_id: str) -> dict:
        samples = self.get_samples_as_dicts(session_id)
        return evaluate_session_quality(samples)

    def publish_assessment(
        self,
        session_id: str,
        actor_user_id: str,
        assessment_date: date | None = None,
    ) -> PlayerAssessmentDB:
        """Publish this session's objective metrics + experimental skill
        inference to the player's existing assessment history. Refuses
        (raises TrackingError) when the quality gate can't support any
        result at all — never fabricates a full assessment from
        insufficient data. A VALID_WITH_LIMITED_CONFIDENCE or
        REVIEW_REQUIRED session still publishes, but the outcome and
        per-metric confidence travel with the result so it's never
        presented as more certain than it is."""
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        from app.services.player_service import PlayerService

        player = PlayerService(db=self.db).get_player(session.player_id)
        if player is None:
            raise TrackingError("Player not found")

        quality = self.get_quality(session_id)
        if quality["outcome"] == INSUFFICIENT_DATA:
            raise TrackingError(
                "Insufficient tracking data to publish an assessment: "
                + "; ".join(quality["reasons"])
            )

        feature_set = self.get_or_compute_feature_set(session_id)
        skills = skill_inference.infer_skills(feature_set, quality)

        test_date = assessment_date or date.today()
        raw_data = {
            "session_id": session_id,
            "tracking_mode": session.tracking_mode,
            "calibration_status": session.calibration_status,
            "quality_gate": quality,
            "features": feature_set,
            "skills": skills,
        }
        movement = feature_set.get("movement", {})
        ball = feature_set.get("ball_relationship", {})
        calculated_metrics = {
            "distance_traveled_units": movement.get("distance_traveled_units"),
            "average_speed_units_per_s": movement.get("average_speed_units_per_s"),
            "peak_speed_units_per_s": movement.get("peak_speed_units_per_s"),
            "direction_change_count": movement.get("direction_change_count"),
            "touch_count": ball.get("touch_count"),
            "possession_seconds": ball.get("possession_seconds"),
        }

        assessment = PlayerAssessmentDB(
            assessment_id=next_entity_id(self.db, "player_assessment"),
            player_id=player.player_id,
            pillar="physical",
            test_category="Smart Soccer Camera Tracking",
            test_type="smart_soccer_session",
            methodology_version=TRACKING_ANALYSIS_METHODOLOGY_VERSION,
            test_date=test_date,
            age_at_assessment_years=calculate_player_age(player.date_of_birth, test_date),
            raw_data=raw_data,
            calculated_metrics=calculated_metrics,
            ai_assisted=True,
            notes=(
                f"Auto-generated from tracking session {session_id}. "
                f"Quality gate: {quality['outcome']}. "
                "Skill scores (if any) are from an EXPERIMENTAL rule-based model, "
                "not a validated neural network — review alongside coach observation."
            ),
            recorded_by_user_id=actor_user_id,
            tracking_session_id=session_id,
            created_at=_now(),
        )
        self.db.add(assessment)
        self.db.commit()
        self.db.refresh(assessment)
        return assessment
