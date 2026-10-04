"""Backend operations for the V1 data foundation (no HTTP routes yet).

There is intentionally no delete, remove or update-value operation here: normal staff
mistakes are fixed with an appended correction. Physical removal belongs only to the
approved privacy workflow (``PrivacyService``).

Not in S1A: who may call these (roles and consent gates), plausibility warnings such as
"42.50 s for a 20 m sprint", and the second-person confirmation workflow. They belong to
the API slice; this layer never changes a value it is given.
"""
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import AuditEventDB, PlayerDB, UserDB
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id
from app.services.privacy_service import is_anonymised_player
from app.v1 import definitions as d
from app.v1.models import (
    V1AssessmentDB,
    V1ManualMeasurementCorrectionDB,
    V1ManualMeasurementDB,
    V1MetricDefinitionDB,
    V1StationRunDB,
    V1TrialDB,
)
from app.v1.timing import parse_seconds_to_centiseconds


class V1ValidationError(ValueError):
    """A request breaks a hard V1 rule. Nothing is stored."""


class V1ConflictError(V1ValidationError):
    """The request collides with an existing record."""


class V1AssessmentService:
    def __init__(self, db: Session):
        self.db = db
        # True when the last create call returned an already-saved record (a retry).
        self.replayed = False

    # -- assessments, station runs, trials -----------------------------------------

    def create_assessment(self, player_id, assessment_date, created_by_user_id,
                          title=None, client_entry_id=None) -> V1AssessmentDB:
        self.replayed = False
        if client_entry_id is not None:
            existing = self.db.query(V1AssessmentDB).filter_by(client_entry_id=client_entry_id).one_or_none()
            if existing is not None:
                if (existing.player_id, existing.assessment_date) != (player_id, assessment_date):
                    raise V1ConflictError("client_entry_id was already used for a different assessment")
                self.replayed = True
                return existing
        player = self.db.get(PlayerDB, player_id)
        if player is None or is_anonymised_player(player):
            raise V1ValidationError("Player not found")
        self._require_user(created_by_user_id)
        assessment = V1AssessmentDB(
            assessment_id=next_entity_id(self.db, "v1_assessment"), player_id=player_id,
            title=title, assessment_date=assessment_date, protocol_code=d.PROTOCOL_CODE,
            standard_version=d.STANDARD_VERSION, status="IN_PROGRESS",
            created_by_user_id=created_by_user_id, created_at=utcnow(),
            client_entry_id=client_entry_id,
        )
        self.db.add(assessment)
        self._audit(created_by_user_id, "v1_assessment_created", "v1_assessment", assessment.assessment_id)
        self.db.commit()
        return assessment

    def start_station_run(self, assessment_id, station_id, created_by_user_id, commit=True) -> V1StationRunDB:
        assessment = self._assessment(assessment_id)
        if assessment.status != "IN_PROGRESS":
            raise V1ValidationError("Assessment is not in progress")
        if station_id not in d.STATIONS:
            raise V1ValidationError("Unknown station")
        self._require_user(created_by_user_id)
        if self.db.query(V1StationRunDB).filter_by(assessment_id=assessment_id, station_id=station_id).first():
            raise V1ConflictError("This station already has a run in this assessment")
        run = V1StationRunDB(
            station_run_id=next_entity_id(self.db, "v1_station_run"), assessment_id=assessment_id,
            station_id=station_id, created_by_user_id=created_by_user_id, created_at=utcnow(),
        )
        self.db.add(run)
        self._finish(commit)
        return run

    def add_trial(self, station_run_id, attempt_number, created_by_user_id, series_key="",
                  validity="VALID", invalid_reason=None, commit=True) -> V1TrialDB:
        run = self.db.get(V1StationRunDB, station_run_id)
        if run is None:
            raise V1ValidationError("Station run not found")
        if self._assessment(run.assessment_id).status != "IN_PROGRESS":
            raise V1ValidationError("Assessment is not in progress")
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
            raise V1ValidationError("Attempt number must be a whole number of 1 or more")
        if series_key not in d.STATION_SERIES[run.station_id]:
            raise V1ValidationError("Series key is not valid for this station")
        if validity not in d.TRIAL_VALIDITIES:
            raise V1ValidationError("Validity must be VALID or INVALID")
        if validity == "INVALID":
            if invalid_reason not in d.INVALID_REASONS[run.station_id]:
                raise V1ValidationError("An invalid attempt needs a reason from this station's list")
        elif invalid_reason is not None:
            raise V1ValidationError("A valid attempt has no invalid reason")
        self._require_user(created_by_user_id)
        if self.db.query(V1TrialDB).filter_by(
                station_run_id=station_run_id, series_key=series_key,
                attempt_number=attempt_number).first():
            raise V1ConflictError("This attempt number already exists in this station run")
        trial = V1TrialDB(
            trial_id=next_entity_id(self.db, "v1_trial"), station_run_id=station_run_id,
            series_key=series_key, attempt_number=attempt_number, validity=validity,
            invalid_reason=invalid_reason, created_by_user_id=created_by_user_id,
            created_at=utcnow(),
        )
        self.db.add(trial)
        self._finish(commit)
        return trial

    # -- manual measurements ---------------------------------------------------------

    def record_manual_time(self, trial_id, metric_id, value, unit, entered_by_user_id,
                           source=d.MEASUREMENT_SOURCE_MANUAL, client_entry_id=None, commit=True):
        """Store the displayed stopwatch value exactly. Raises, and stores nothing, for a
        value that cannot be stored as typed; it never rounds, scales or "fixes" it."""
        centiseconds = parse_seconds_to_centiseconds(value)
        return self._record(trial_id, metric_id, "RECORDED", centiseconds, None, unit,
                            source, entered_by_user_id, client_entry_id, commit)

    def record_not_recorded(self, trial_id, metric_id, reason, entered_by_user_id,
                            client_entry_id=None, commit=True):
        """The timing failed: store no number and no performance verdict."""
        if reason not in d.NOT_RECORDED_REASONS:
            raise V1ValidationError("Unknown NOT_RECORDED reason")
        return self._record(trial_id, metric_id, "NOT_RECORDED", None, reason, d.UNIT_SECONDS,
                            d.MEASUREMENT_SOURCE_MANUAL, entered_by_user_id, client_entry_id, commit)

    def _record(self, trial_id, metric_id, status, centiseconds, reason, unit, source,
                entered_by_user_id, client_entry_id, commit=True):
        self.replayed = False
        if client_entry_id is not None:
            existing = self.db.query(V1ManualMeasurementDB).filter_by(client_entry_id=client_entry_id).one_or_none()
            if existing is not None:
                if (existing.trial_id, existing.metric_id, existing.entry_status,
                        existing.value_centiseconds) != (trial_id, metric_id, status, centiseconds):
                    raise V1ConflictError("client_entry_id was already used for a different entry")
                self.replayed = True
                return existing
        if source != d.MEASUREMENT_SOURCE_MANUAL:
            raise V1ValidationError("Official V1 times are HUMAN_MANUAL only")
        trial = self.db.get(V1TrialDB, trial_id)
        if trial is None:
            raise V1ValidationError("Trial not found")
        run = self.db.get(V1StationRunDB, trial.station_run_id)
        if self._assessment(run.assessment_id).status != "IN_PROGRESS":
            raise V1ValidationError("Assessment is not in progress")
        definition = self.db.query(V1MetricDefinitionDB).filter_by(
            metric_id=metric_id, is_active=True).order_by(V1MetricDefinitionDB.definition_version.desc()).first()
        if definition is None:
            raise V1ValidationError("Unknown or inactive metric")
        if definition.station_id != run.station_id:
            raise V1ValidationError("This metric does not belong to the trial's station")
        if unit != definition.unit:
            raise V1ValidationError(f"Unit must be {definition.unit}")
        self._require_user(entered_by_user_id)
        saved = self.db.query(V1ManualMeasurementDB).filter_by(trial_id=trial_id, metric_id=metric_id).first()
        if saved is not None:
            if client_entry_id is not None and saved.client_entry_id == client_entry_id:
                # The same request, committed by a racing twin between our two lookups.
                if (saved.entry_status, saved.value_centiseconds) != (status, centiseconds):
                    raise V1ConflictError("client_entry_id was already used for a different entry")
                self.replayed = True
                return saved
            raise V1ConflictError("This trial already has an entry for this metric; correct it instead")
        measurement = V1ManualMeasurementDB(
            manual_measurement_id=next_entity_id(self.db, "v1_manual_measurement"),
            trial_id=trial_id, metric_id=metric_id,
            metric_definition_version=definition.definition_version, entry_status=status,
            value_centiseconds=centiseconds, unit=definition.unit, not_recorded_reason=reason,
            measurement_source=d.MEASUREMENT_SOURCE_MANUAL,
            measurement_method=d.MEASUREMENT_METHOD_MANUAL,
            entered_by_user_id=entered_by_user_id, entered_at=utcnow(),
            client_entry_id=client_entry_id,
        )
        self.db.add(measurement)
        self._audit(entered_by_user_id, "v1_manual_measurement_recorded", "v1_manual_measurement",
                    measurement.manual_measurement_id, {"entry_status": status})
        self._finish(commit)
        return measurement

    # -- one attempt in one step (Unified Player Assessment, Phase 1) ---------------------------

    def save_attempt(self, assessment_id, station_id, series_key, attempt_number, validity,
                     invalid_reason, entry_status, displayed_value, not_recorded_reason,
                     user_id, client_entry_id):
        """Save one attempt exactly as the coach reached it: the station run (created on first
        use), the trial and, for a VALID attempt, its official time or its NOT_RECORDED reason,
        all in ONE transaction. Returns ``(run, trial, measurement_or_None, created)``.

        It is safe to send twice. The same ``client_entry_id`` with the same content returns
        what was saved (``created`` False). An attempt number that already holds different
        content raises ``V1ConflictError``: this method never overwrites or merges. Nothing is
        stored when anything is rejected."""
        if not client_entry_id:
            raise V1ValidationError("An attempt needs a client_entry_id so it can be retried safely")
        if station_id not in d.ENABLED_STATIONS:
            raise V1ValidationError("This station is not available yet")
        if series_key not in d.STATION_SERIES[station_id]:
            raise V1ValidationError("Series key is not valid for this station")
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
            raise V1ValidationError("Attempt number must be a whole number of 1 or more")
        if validity not in d.TRIAL_VALIDITIES:
            raise V1ValidationError("Validity must be VALID or INVALID")
        if validity == "INVALID":
            if invalid_reason not in d.INVALID_REASONS[station_id]:
                raise V1ValidationError("An invalid attempt needs a reason from this station's list")
            if entry_status is not None or displayed_value is not None or not_recorded_reason is not None:
                raise V1ValidationError("A not-valid attempt carries no time")
        else:
            if invalid_reason is not None:
                raise V1ValidationError("A valid attempt has no invalid reason")
            if entry_status == "RECORDED":
                if displayed_value is None or not_recorded_reason is not None:
                    raise V1ValidationError("A recorded time needs a value and no not-recorded reason")
            elif entry_status == "NOT_RECORDED":
                if displayed_value is not None or not_recorded_reason not in d.NOT_RECORDED_REASONS:
                    raise V1ValidationError("NOT_RECORDED needs one of the listed reasons and no value")
            else:
                raise V1ValidationError("A valid attempt needs a time or a not-recorded reason")
        centiseconds = (parse_seconds_to_centiseconds(displayed_value)
                        if entry_status == "RECORDED" else None)
        assessment = self._assessment(assessment_id)
        if assessment.status != "IN_PROGRESS":
            raise V1ValidationError("Assessment is not in progress")
        self._require_user(user_id)
        metric_id = d.STATION_METRIC[station_id]
        wanted = (entry_status, centiseconds, not_recorded_reason)

        # The same operation again: found by its key, which is stored on the measurement.
        twin = self.db.query(V1ManualMeasurementDB).filter_by(client_entry_id=client_entry_id).one_or_none()
        if twin is not None:
            trial = self.db.get(V1TrialDB, twin.trial_id)
            run = self.db.get(V1StationRunDB, trial.station_run_id)
            same = ((run.assessment_id, run.station_id, trial.series_key, trial.attempt_number,
                     trial.validity, trial.invalid_reason)
                    == (assessment_id, station_id, series_key, attempt_number, validity, invalid_reason)
                    and (twin.entry_status, twin.value_centiseconds, twin.not_recorded_reason) == wanted)
            if not same:
                raise V1ConflictError("client_entry_id was already used for a different attempt")
            return run, trial, twin, False

        created = False
        run = self.db.query(V1StationRunDB).filter_by(
            assessment_id=assessment_id, station_id=station_id).one_or_none()
        if run is None:
            run = self.start_station_run(assessment_id, station_id, user_id, commit=False)
            created = True
        trial = self.db.query(V1TrialDB).filter_by(
            station_run_id=run.station_run_id, series_key=series_key,
            attempt_number=attempt_number).one_or_none()
        if trial is not None:
            if (trial.validity, trial.invalid_reason) != (validity, invalid_reason):
                raise V1ConflictError("This attempt number already exists with different content")
        else:
            trial = self.add_trial(run.station_run_id, attempt_number, user_id, series_key=series_key,
                                   validity=validity, invalid_reason=invalid_reason, commit=False)
            created = True
        if validity == "INVALID":
            self._finish(True)
            return run, trial, None, created
        saved = self.db.query(V1ManualMeasurementDB).filter_by(trial_id=trial.trial_id, metric_id=metric_id).first()
        if saved is not None:
            # The trial already holds an entry that belongs to some other operation.
            raise V1ConflictError("This attempt already has a different saved entry")
        if entry_status == "RECORDED":
            measurement = self.record_manual_time(trial.trial_id, metric_id, displayed_value, d.UNIT_SECONDS,
                                                  user_id, client_entry_id=client_entry_id, commit=False)
        else:
            measurement = self.record_not_recorded(trial.trial_id, metric_id, not_recorded_reason, user_id,
                                                   client_entry_id=client_entry_id, commit=False)
        self._finish(True)
        return run, trial, measurement, True

    # -- corrections (append-only) ----------------------------------------------------

    def correct_manual_time(self, manual_measurement_id, new_value, corrected_by_user_id,
                            reason_code, reason_text=None, confirmed_by_user_id=None):
        measurement = self.db.get(V1ManualMeasurementDB, manual_measurement_id)
        if measurement is None:
            raise V1ValidationError("Measurement not found")
        if measurement.entry_status != "RECORDED":
            raise V1ValidationError("Only a recorded time can be corrected; repeat the attempt instead")
        if reason_code not in d.CORRECTION_REASONS:
            raise V1ValidationError("Unknown correction reason")
        if reason_code == "OTHER" and not (reason_text or "").strip():
            raise V1ValidationError("Reason OTHER needs a text")
        corrected = parse_seconds_to_centiseconds(new_value)
        self._require_user(corrected_by_user_id)
        if confirmed_by_user_id is not None:
            if confirmed_by_user_id == corrected_by_user_id:
                raise V1ValidationError("The confirmer must be a different person")
            self._require_user(confirmed_by_user_id)
        history = self.history(manual_measurement_id)
        current = history[-1].corrected_centiseconds if history else measurement.value_centiseconds
        if corrected == current:
            raise V1ValidationError("The correction does not change the value")
        correction = V1ManualMeasurementCorrectionDB(
            correction_id=next_entity_id(self.db, "v1_manual_measurement_correction"),
            manual_measurement_id=manual_measurement_id, sequence_number=len(history) + 1,
            original_centiseconds=current, corrected_centiseconds=corrected,
            reason_code=reason_code, reason_text=reason_text,
            corrected_by_user_id=corrected_by_user_id, corrected_at=utcnow(),
            confirmed_by_user_id=confirmed_by_user_id,
        )
        self.db.add(correction)
        self._audit(corrected_by_user_id, "v1_manual_measurement_correction_recorded",
                    "v1_manual_measurement", manual_measurement_id,
                    {"correction_id": correction.correction_id, "sequence_number": correction.sequence_number})
        self.db.commit()
        return correction

    def history(self, manual_measurement_id) -> list[V1ManualMeasurementCorrectionDB]:
        return (self.db.query(V1ManualMeasurementCorrectionDB)
                .filter_by(manual_measurement_id=manual_measurement_id)
                .order_by(V1ManualMeasurementCorrectionDB.sequence_number).all())

    def effective_centiseconds(self, manual_measurement_id) -> int | None:
        measurement = self.db.get(V1ManualMeasurementDB, manual_measurement_id)
        if measurement is None:
            raise V1ValidationError("Measurement not found")
        history = self.history(manual_measurement_id)
        return history[-1].corrected_centiseconds if history else measurement.value_centiseconds

    # -- helpers ------------------------------------------------------------------------

    def _finish(self, commit):
        """Commit now, or only make the pending rows visible so a caller can finish a larger
        step (a whole attempt) in one transaction."""
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    def _assessment(self, assessment_id) -> V1AssessmentDB:
        assessment = self.db.get(V1AssessmentDB, assessment_id)
        if assessment is None:
            raise V1ValidationError("Assessment not found")
        return assessment

    def _require_user(self, user_id):
        if self.db.get(UserDB, user_id) is None:
            raise V1ValidationError("User not found")

    def _audit(self, actor_user_id, action, resource_type, resource_id, details=None):
        """Existing AuditEventDB: ids, action, actor, time and result only."""
        self.db.add(AuditEventDB(
            event_id=str(uuid4()), occurred_at=utcnow(), actor_user_id=actor_user_id,
            action=action, resource_type=resource_type, resource_id=resource_id,
            details={"result": "ok", **(details or {})},
        ))
