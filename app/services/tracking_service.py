"""Smart Soccer Camera tracking: session lifecycle, sample/event ingest,
derived-feature caching, and publishing results to the existing
PlayerAssessmentDB — the single integration point between the iOS
tracking app and the rest of KEMET's assessment pipeline. Never invents
a player: every session requires an explicit, already-selected
player_id from the caller (see app/routers/tracking.py), exactly
matching the "coach selects player before recording" requirement."""

from dataclasses import dataclass
from datetime import date, datetime, UTC

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db_models import (
    CoachValidationLabelDB,
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
from app.services.tracking_quality import INSUFFICIENT_DATA, REVIEW_REQUIRED, evaluate_session_quality

TRACKING_MODES = {"player_lock", "ball_track", "smart_soccer"}
TRACKING_ANALYSIS_METHODOLOGY_VERSION = "smart_soccer_tracking_v1"


class TrackingError(ValueError):
    pass


class TrackingSessionIdentityConflict(TrackingError):
    """A client_recording_id was reused with different player/metadata
    than the session it already identifies — never silently returned as
    if it were the same recording (spec Phase 8: "Reject reuse of one
    clientRecordingId with a different player or incompatible
    metadata")."""


class TrackingSampleConflict(TrackingError):
    """A batch contained a sequence number already stored with DIFFERENT
    content (or two different contents for one sequence). The whole batch
    was rejected: nothing from it was inserted."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        self.mismatched_sequences = [conflict["sequence_number"] for conflict in conflicts]
        super().__init__(
            "Sequence number(s) "
            + ", ".join(str(sequence) for sequence in self.mismatched_sequences)
            + " already exist with different telemetry content; the whole batch was rejected"
        )


class TrackingEventConflict(TrackingError):
    """A client_event_id was reused with different event content."""

    def __init__(self, client_event_id: str, differing_fields: list[str]):
        self.client_event_id = client_event_id
        self.differing_fields = differing_fields
        super().__init__(
            f"client_event_id {client_event_id!r} already exists with different content "
            f"({', '.join(differing_fields)})"
        )


class TrackingReviewRequired(TrackingError):
    """Publishing was refused until a coach reviews the session (uncertain
    identity) or because the coach reported the wrong child was tracked."""


@dataclass(frozen=True)
class SampleIngestResult:
    ingested: int
    duplicates: int
    # Exactly the submitted sequence numbers that are now durably accounted
    # for (inserted, or already stored with identical content). Never a
    # sequence that was not in the request.
    acknowledged_sequences: list[int]


@dataclass(frozen=True)
class EventIngestResult:
    event: TrackingEventDB
    duplicate: bool


# The client-authored telemetry that defines a sample's identity-relevant
# CONTENT. Server-generated fields (sample_id) and the identity itself
# (session_id + sequence_number) are deliberately not compared.
SAMPLE_CONTENT_FIELDS = (
    "t_seconds",
    "player_bbox",
    "player_center",
    "player_confidence",
    "player_track_id",
    "ball_bbox",
    "ball_center",
    "ball_confidence",
    "ball_track_id",
    "pose_keypoints",
    "gimbal_state",
    "tracking_mode",
    "tracking_status",
    "identity_ambiguous",
)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """Stored/compared as naive UTC (the convention of every DateTime column
    here). A naive client value is taken to already be UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


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
        client_recording_id: str | None = None,
    ) -> TrackingSessionDB:
        if tracking_mode not in TRACKING_MODES:
            raise TrackingError(f"Unknown tracking_mode: {tracking_mode}")

        # Idempotent retry: the iOS app's local upload queue may retry
        # this exact call (e.g. after the response was lost to a network
        # drop even though the backend's write succeeded) — matching on
        # client_recording_id returns the ORIGINAL session instead of
        # creating a second one for the same physical recording. A
        # client_recording_id reused with a DIFFERENT player is a real
        # identity conflict, never silently treated as the same recording.
        if client_recording_id is not None:
            existing = (
                self.db.query(TrackingSessionDB)
                .filter(TrackingSessionDB.client_recording_id == client_recording_id)
                .one_or_none()
            )
            if existing is not None:
                if existing.player_id != player_id:
                    raise TrackingSessionIdentityConflict(
                        f"client_recording_id {client_recording_id!r} already belongs to "
                        f"player {existing.player_id!r}, not {player_id!r}"
                    )
                return existing

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
            client_recording_id=client_recording_id,
            created_at=now,
        )
        self.db.add(session)
        try:
            self.db.commit()
        except IntegrityError:
            # Two simultaneous requests for the SAME client_recording_id
            # both passed the SELECT above before either committed (spec
            # Phase 8: "Make clientRecordingId idempotency safe under
            # simultaneous requests, not only sequential retries") — the
            # unique constraint on client_recording_id catches this at
            # the database level; the loser rolls back and returns
            # whichever row actually won, exactly like the sequential
            # retry path above, rather than raising a raw 500.
            self.db.rollback()
            winner = (
                self.db.query(TrackingSessionDB)
                .filter(TrackingSessionDB.client_recording_id == client_recording_id)
                .one_or_none()
            )
            if winner is None:
                raise
            if winner.player_id != player_id:
                raise TrackingSessionIdentityConflict(
                    f"client_recording_id {client_recording_id!r} already belongs to "
                    f"player {winner.player_id!r}, not {player_id!r}"
                ) from None
            return winner
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

    # -- idempotent, atomic telemetry ingest --------------------------------
    #
    # The database is the final authority on identity: UNIQUE(session_id,
    # sequence_number) and UNIQUE(session_id, client_event_id). The reads
    # below only CLASSIFY a request (new / identical duplicate / mismatch) so
    # it can be answered precisely; the inserts use INSERT ... ON CONFLICT DO
    # NOTHING, so a concurrent identical request can never create a second
    # row or raise, and a row that lost such a race is compared afterwards.

    def _insert_ignoring_conflicts(self, table, rows: list[dict], conflict_columns: list[str], returning_column: str) -> set:
        """INSERT ... ON CONFLICT DO NOTHING RETURNING <column>. Returns the
        set of `returning_column` values that were actually inserted."""
        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise TrackingError(f"Idempotent telemetry ingest is not supported on the {dialect!r} database")
        statement = (
            insert(table)
            .on_conflict_do_nothing(index_elements=conflict_columns)
            .returning(table.c[returning_column])
        )
        return {row[0] for row in self.db.execute(statement, rows)}

    @staticmethod
    def _sample_content(sample: dict, session: TrackingSessionDB) -> dict:
        """The canonical, comparable content of a submitted sample: the
        client-authored fields only, with the schema defaults applied."""
        defaults = {"gimbal_state": "unknown", "tracking_mode": session.tracking_mode, "tracking_status": "unknown"}
        content = {}
        for field in SAMPLE_CONTENT_FIELDS:
            value = sample.get(field)
            content[field] = defaults[field] if value is None and field in defaults else value
        return content

    @staticmethod
    def _stored_sample_content(row: TrackingSampleDB) -> dict:
        return {field: getattr(row, field) for field in SAMPLE_CONTENT_FIELDS}

    @staticmethod
    def _differing_fields(first: dict, second: dict) -> list[str]:
        return [field for field in first if first[field] != second[field]]

    def _stored_samples_by_sequence(self, session_id: str, sequences: list[int]) -> dict[int, dict]:
        if not sequences:
            return {}
        rows = (
            self.db.query(TrackingSampleDB)
            .filter(TrackingSampleDB.session_id == session_id, TrackingSampleDB.sequence_number.in_(sequences))
            .all()
        )
        return {row.sequence_number: self._stored_sample_content(row) for row in rows}

    def ingest_sample_batch(self, session_id: str, samples: list[dict]) -> SampleIngestResult:
        """All-or-nothing with respect to identity conflicts: every sequenced
        sample must be new or already stored with IDENTICAL content;
        otherwise nothing from the request is committed (TrackingSampleConflict).
        Samples without a sequence number are legacy and are simply inserted."""
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        legacy: list[dict] = []
        submitted: dict[int, dict] = {}
        conflicts: dict[int, list[str]] = {}
        duplicates = 0

        for sample in samples:
            content = self._sample_content(sample, session)
            sequence = sample.get("sequence_number")
            if sequence is None:
                legacy.append(content)
            elif sequence not in submitted:
                submitted[sequence] = content
            else:
                differing = self._differing_fields(submitted[sequence], content)
                if differing:
                    conflicts[sequence] = sorted(set(conflicts.get(sequence, [])) | set(differing))
                else:
                    duplicates += 1

        stored = self._stored_samples_by_sequence(session_id, list(submitted))
        to_insert: list[dict] = []
        for sequence, content in submitted.items():
            if sequence not in stored:
                to_insert.append({**content, "session_id": session_id, "sequence_number": sequence})
                continue
            differing = self._differing_fields(stored[sequence], content)
            if differing:
                conflicts[sequence] = sorted(set(conflicts.get(sequence, [])) | set(differing))
            else:
                duplicates += 1

        if conflicts:
            self.db.rollback()
            raise TrackingSampleConflict(self._conflict_details(conflicts))

        inserted: set[int] = set()
        if to_insert:
            inserted = self._insert_ignoring_conflicts(
                TrackingSampleDB.__table__, to_insert, ["session_id", "sequence_number"], "sequence_number"
            )
            # A row we tried to insert but the database refused was inserted
            # by a concurrent request in the meantime: compare it now.
            raced = [row for row in to_insert if row["sequence_number"] not in inserted]
            if raced:
                stored_now = self._stored_samples_by_sequence(session_id, [row["sequence_number"] for row in raced])
                for row in raced:
                    content = {field: row[field] for field in SAMPLE_CONTENT_FIELDS}
                    differing = self._differing_fields(stored_now[row["sequence_number"]], content)
                    if differing:
                        conflicts[row["sequence_number"]] = differing
                    else:
                        duplicates += 1
                if conflicts:
                    self.db.rollback()
                    raise TrackingSampleConflict(self._conflict_details(conflicts))

        if legacy:
            self.db.execute(
                TrackingSampleDB.__table__.insert(),
                [{**content, "session_id": session_id, "sequence_number": None} for content in legacy],
            )

        ingested = len(inserted) + len(legacy)
        if ingested:
            # Genuinely new telemetry makes any cached derived features stale
            # (e.g. late samples arriving after /complete). An exact duplicate
            # retry inserts nothing and leaves the cache alone.
            self.db.query(TrackingFeatureSetDB).filter(
                TrackingFeatureSetDB.session_id == session_id
            ).delete(synchronize_session=False)
        self.db.commit()
        return SampleIngestResult(ingested=ingested, duplicates=duplicates, acknowledged_sequences=sorted(submitted))

    @staticmethod
    def _conflict_details(conflicts: dict[int, list[str]]) -> list[dict]:
        return [
            {"sequence_number": sequence, "differing_fields": differing}
            for sequence, differing in sorted(conflicts.items())
        ]

    def ingest_samples(self, session_id: str, samples: list[dict]) -> int:
        """Backward-compatible wrapper: the number of newly inserted rows."""
        return self.ingest_sample_batch(session_id, samples).ingested

    def ingest_event_idempotent(
        self,
        session_id: str,
        event_type: str,
        details: dict | None = None,
        client_event_id: str | None = None,
        client_occurred_at: datetime | None = None,
    ) -> EventIngestResult:
        """A discrete diagnostic event. `occurred_at` is the SERVER receipt
        time (unchanged meaning); `client_occurred_at` is the device time.
        With a client_event_id the call is idempotent: the same id and
        content again is a duplicate, the same id with different
        client-authored content (event_type, details, client_occurred_at)
        raises TrackingEventConflict. Without one it always inserts."""
        session = self.db.get(TrackingSessionDB, session_id)
        if session is None:
            raise TrackingError("Tracking session not found")

        details = details or {}
        client_time = _to_naive_utc(client_occurred_at)

        if client_event_id is None:
            event = TrackingEventDB(
                event_id=next_entity_id(self.db, "tracking_event"),
                session_id=session_id,
                occurred_at=_now(),
                event_type=event_type,
                details=details,
                client_occurred_at=client_time,
            )
            self.db.add(event)
            self.db.commit()
            self.db.refresh(event)
            return EventIngestResult(event=event, duplicate=False)

        existing = self._find_event(session_id, client_event_id)
        if existing is None:
            row = {
                "event_id": next_entity_id(self.db, "tracking_event"),
                "session_id": session_id,
                "occurred_at": _now(),
                "event_type": event_type,
                "details": details,
                "client_event_id": client_event_id,
                "client_occurred_at": client_time,
            }
            inserted = self._insert_ignoring_conflicts(
                TrackingEventDB.__table__, [row], ["session_id", "client_event_id"], "event_id"
            )
            self.db.commit()
            if inserted:
                event = self._find_event(session_id, client_event_id)
                return EventIngestResult(event=event, duplicate=False)
            existing = self._find_event(session_id, client_event_id)   # lost a race to an identical/other request

        differing = [
            field
            for field, incoming in (("event_type", event_type), ("details", details), ("client_occurred_at", client_time))
            if getattr(existing, field) != incoming
        ]
        if differing:
            raise TrackingEventConflict(client_event_id, differing)
        return EventIngestResult(event=existing, duplicate=True)

    def _find_event(self, session_id: str, client_event_id: str) -> TrackingEventDB | None:
        return (
            self.db.query(TrackingEventDB)
            .filter(TrackingEventDB.session_id == session_id, TrackingEventDB.client_event_id == client_event_id)
            .one_or_none()
        )

    def ingest_event(self, session_id: str, event_type: str, details: dict | None = None) -> TrackingEventDB:
        """Backward-compatible wrapper for callers without a client_event_id."""
        return self.ingest_event_idempotent(session_id, event_type, details).event

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

    @staticmethod
    def samples_as_dicts(rows: list[TrackingSampleDB]) -> list[dict]:
        return [
            {
                "t_seconds": s.t_seconds,
                "sequence_number": s.sequence_number,
                "player_bbox": s.player_bbox,
                "player_center": s.player_center,
                "player_confidence": s.player_confidence,
                "player_track_id": s.player_track_id,
                "ball_bbox": s.ball_bbox,
                "ball_center": s.ball_center,
                "ball_confidence": s.ball_confidence,
                "ball_track_id": s.ball_track_id,
                "pose_keypoints": s.pose_keypoints,
                "tracking_status": s.tracking_status,
                "identity_ambiguous": s.identity_ambiguous,
            }
            for s in rows
        ]

    def get_samples_as_dicts(self, session_id: str) -> list[dict]:
        return self.samples_as_dicts(self.list_samples(session_id))

    def list_samples_after(self, session_id: str, after_sequence: int = 0, limit: int = 5000) -> list[TrackingSampleDB]:
        """Samples for export, in capture order. Legacy rows (no sequence number)
        come first in time order; sequenced rows follow by sequence."""
        query = self.db.query(TrackingSampleDB).filter(TrackingSampleDB.session_id == session_id)
        if after_sequence:
            query = query.filter(TrackingSampleDB.sequence_number > after_sequence)
        return (
            query.order_by(TrackingSampleDB.sequence_number.asc().nullsfirst(), TrackingSampleDB.t_seconds.asc(), TrackingSampleDB.sample_id.asc())
            .limit(limit)
            .all()
        )

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
        session = self.db.get(TrackingSessionDB, session_id)
        features = build_feature_set(
            samples,
            calibration_status=session.calibration_status if session else None,
            calibration_scale_m_per_unit=session.calibration_scale_m_per_unit if session else None,
        )

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

    def _latest_player_confirmation(self, session_id: str) -> dict | None:
        """The coach's most recent answer to "was the RIGHT child tracked?"."""
        label = (
            self.db.query(CoachValidationLabelDB)
            .filter(
                CoachValidationLabelDB.session_id == session_id,
                CoachValidationLabelDB.label_type == "correct_player_tracked",
            )
            .order_by(CoachValidationLabelDB.created_at.desc(), CoachValidationLabelDB.label_id.desc())
            .first()
        )
        if label is None:
            return None
        return {
            "answer": (label.value or {}).get("answer"),
            "label_id": label.label_id,
            "coach_user_id": label.coach_user_id,
            "reviewed_at": label.created_at.isoformat() if label.created_at else None,
        }

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

        # Coach review comes first: uncertain identity is never published on
        # the system's say-so, and a coach who reported the wrong child was
        # tracked blocks publishing outright.
        coach_review = self._latest_player_confirmation(session_id)
        if coach_review is not None and coach_review["answer"] == "no":
            raise TrackingReviewRequired(
                "The coach reported that the wrong player was tracked in this session; it cannot be published."
            )
        if quality["outcome"] == REVIEW_REQUIRED and (coach_review is None or coach_review["answer"] != "yes"):
            raise TrackingReviewRequired(
                "Coach review required before publishing: the tracking quality gate flagged this session "
                "(" + "; ".join(quality["reasons"]) + "). Confirm that the correct player was tracked "
                "(POST /tracking/sessions/{id}/confirm-player with answer \"yes\") to publish."
            )

        feature_set = self.get_or_compute_feature_set(session_id)
        skills = skill_inference.infer_skills(feature_set, quality)

        test_date = assessment_date or date.today()
        raw_data = {
            "session_id": session_id,
            "tracking_mode": session.tracking_mode,
            "calibration_status": session.calibration_status,
            "quality_gate": quality,
            "coach_review": coach_review,
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
            # Where these numbers live: never metres or km/h without calibration.
            "measurement_context": feature_set.get("measurement_context"),
            "touch_count_basis": ball.get("touch_count_basis"),
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
