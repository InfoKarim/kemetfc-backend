"""Assessment V1 manual-measurement API (S1B1). Backend only.

Routes (all under /v1, no DELETE and no generic PUT/PATCH anywhere):

  GET  /v1/sprint, /v1/sprint.js                         the coach web page for the S1 sprint workflow
  GET  /v1/player-picker                                 player_id + display_name only (read)
  GET  /v1/eligible-confirmers                           active coach/admin other than the caller (write roles)
  POST /v1/assessments                                   create assessment (201, replay 200)
  GET  /v1/players/{player_id}/assessments               assessment summaries for one player (read)
  GET  /v1/assessments/{assessment_id}                   assessment tree with facts only
  POST /v1/assessments/{assessment_id}/station-runs      create or get a station run (201 / 200)
  POST /v1/station-runs/{station_run_id}/trials          create a trial (201 / replay 200)
  POST /v1/trials/{trial_id}/manual-measurements         enter a time or NOT_RECORDED (201 / replay 200)
  GET  /v1/manual-measurements/{measurement_id}          one measurement with correction history
  POST /v1/manual-measurements/{measurement_id}/corrections   append a correction (201 / replay 200)

Authorization (existing roles only): admin and coach may read and write; reviewer may read
only (a reviewer cannot change a HUMAN_MANUAL time); guardians cannot reach /v1 at all.
Consent: every write needs an active guardian consent with purpose "performance_tracking"
for the player (PrivacyService.has_active_consent).
"""
import random
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.db_models import PlayerDB, UserDB
from app.services.privacy_service import PrivacyService, is_anonymised_player
from app.v1 import definitions as d
from app.v1.models import (
    V1AssessmentDB,
    V1ManualMeasurementCorrectionDB,
    V1ManualMeasurementDB,
    V1StationRunDB,
    V1TrialDB,
)
from app.v1.schemas import (
    CorrectMeasurementBody,
    CreateAssessmentBody,
    CreateStationRunBody,
    CreateTrialBody,
    RecordMeasurementBody,
)
from app.v1.service import V1AssessmentService, V1ConflictError, V1ValidationError
from app.v1.timing import InvalidTimeValue, format_seconds, parse_seconds_to_centiseconds

router = APIRouter(prefix="/v1", tags=["assessment-v1"])
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

WRITE_ROLES = {"admin", "coach"}
READ_ROLES = {"admin", "coach", "reviewer"}
CONSENT_PURPOSE = "performance_tracking"
# Only the unambiguous sprint workflow is open in this API version. S2/S5/S6 need
# left/right semantics and S3/S4 need observation rules that are not frozen yet.
ENABLED_STATIONS = {"S1"}
# A losing racer reruns against the winner's committed row. Several rounds are allowed
# because human-readable IDs come from a shared counter that can also collide briefly.
RACE_ATTEMPTS = 6


def _actor(request: Request, roles: set[str]) -> str:
    user = request.state.current_user
    if user["role"] not in roles:
        raise HTTPException(status_code=403, detail="Not allowed for this role")
    return user["user_id"]


def _require_consent(db: Session, player_id: str) -> None:
    if not PrivacyService(db=db).has_active_consent(player_id, CONSENT_PURPOSE):
        raise HTTPException(
            status_code=403,
            detail="No active guardian consent for performance tracking exists for this player",
        )


def _player_of_assessment(db: Session, assessment_id: str) -> V1AssessmentDB:
    assessment = db.get(V1AssessmentDB, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _chain_of_trial(db: Session, trial_id: str):
    trial = db.get(V1TrialDB, trial_id)
    if trial is None:
        raise HTTPException(status_code=404, detail="Trial not found")
    run = db.get(V1StationRunDB, trial.station_run_id)
    return trial, run, _player_of_assessment(db, run.assessment_id)


def _fail(error: Exception):
    status = 409 if isinstance(error, V1ConflictError) else 422
    raise HTTPException(status_code=status, detail=str(error))


def _once_or_reconcile(db: Session, action):
    """Run a write. If two identical requests race, the database uniqueness decides:
    the loser rolls back and runs again, where it now finds the winner's row and either
    replays it or reports a conflict. Never silently creates a second record."""
    for attempt in range(1, RACE_ATTEMPTS + 1):
        try:
            return action()
        except IntegrityError:
            db.rollback()
            if attempt == RACE_ATTEMPTS:
                raise HTTPException(status_code=409, detail="Conflicting concurrent request")
            time.sleep(random.uniform(0.005, 0.03))
        except (V1ValidationError, InvalidTimeValue) as error:
            db.rollback()
            _fail(error)


def _decimal(centiseconds):
    return format_seconds(centiseconds)


def _correction_view(c: V1ManualMeasurementCorrectionDB) -> dict:
    return {
        "correction_id": c.correction_id, "sequence_number": c.sequence_number,
        "original_value": _decimal(c.original_centiseconds),
        "corrected_value": _decimal(c.corrected_centiseconds),
        "reason_code": c.reason_code, "reason_text": c.reason_text,
        "corrected_by": c.corrected_by_user_id, "corrected_at": c.corrected_at,
        "confirmed_by": c.confirmed_by_user_id,
    }


def measurement_view(db: Session, m: V1ManualMeasurementDB) -> dict:
    history = (db.query(V1ManualMeasurementCorrectionDB)
               .filter_by(manual_measurement_id=m.manual_measurement_id)
               .order_by(V1ManualMeasurementCorrectionDB.sequence_number).all())
    effective = history[-1].corrected_centiseconds if history else m.value_centiseconds
    return {
        "measurement_id": m.manual_measurement_id, "trial_id": m.trial_id,
        "metric_id": m.metric_id, "metric_definition_version": m.metric_definition_version,
        "entry_status": m.entry_status, "displayed_value": _decimal(m.value_centiseconds),
        "unit": m.unit, "not_recorded_reason": m.not_recorded_reason,
        "measurement_source": m.measurement_source, "measurement_method": m.measurement_method,
        "entered_by": m.entered_by_user_id, "entered_at": m.entered_at,
        "original_value": _decimal(m.value_centiseconds),
        "effective_value": _decimal(effective),
        "corrections": [_correction_view(c) for c in history],
    }


def _trial_view(db: Session, t: V1TrialDB, with_measurements=True) -> dict:
    view = {
        "trial_id": t.trial_id, "station_run_id": t.station_run_id,
        "attempt_number": t.attempt_number, "validity": t.validity,
        "invalid_reason": t.invalid_reason, "created_by": t.created_by_user_id,
        "created_at": t.created_at,
    }
    if with_measurements:
        view["manual_measurements"] = [
            measurement_view(db, m) for m in
            db.query(V1ManualMeasurementDB).filter_by(trial_id=t.trial_id)
            .order_by(V1ManualMeasurementDB.manual_measurement_id)
        ]
    return view


def _run_view(db: Session, r: V1StationRunDB, with_trials=True) -> dict:
    view = {"station_run_id": r.station_run_id, "assessment_id": r.assessment_id,
            "station_id": r.station_id, "created_by": r.created_by_user_id,
            "created_at": r.created_at}
    if with_trials:
        view["trials"] = [
            _trial_view(db, t) for t in
            db.query(V1TrialDB).filter_by(station_run_id=r.station_run_id)
            .order_by(V1TrialDB.series_key, V1TrialDB.attempt_number)
        ]
    return view


def _assessment_view(db: Session, a: V1AssessmentDB, with_runs=True) -> dict:
    view = {
        "assessment_id": a.assessment_id, "player_id": a.player_id, "title": a.title,
        "assessment_date": a.assessment_date.isoformat(), "protocol_code": a.protocol_code,
        "standard_version": a.standard_version, "status": a.status,
        "created_by": a.created_by_user_id, "created_at": a.created_at,
    }
    if with_runs:
        view["station_runs"] = [
            _run_view(db, r) for r in
            db.query(V1StationRunDB).filter_by(assessment_id=a.assessment_id)
            .order_by(V1StationRunDB.station_id)
        ]
    return view


def _respond(body: dict, created: bool) -> JSONResponse:
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(body))


# --------------------------------------------------------------------- assessments

@router.post("/assessments")
def create_assessment(body: CreateAssessmentBody, request: Request, db: Session = Depends(get_db)):
    actor = _actor(request, WRITE_ROLES)
    if db.get(PlayerDB, body.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    _require_consent(db, body.player_id)
    service = V1AssessmentService(db)

    def act():
        saved = service.create_assessment(body.player_id, body.assessment_date, actor,
                                          title=body.title, client_entry_id=body.client_entry_id)
        return saved, not service.replayed

    assessment, created = _once_or_reconcile(db, act)
    return _respond(_assessment_view(db, assessment, with_runs=False), created=created)


@router.get("/player-picker")
def player_picker(request: Request, db: Session = Depends(get_db)):
    """The only player fields the sprint screen needs. No profile, date of birth, team,
    guardian or medical data, and no anonymised (deleted) players."""
    _actor(request, READ_ROLES)
    players = [p for p in db.query(PlayerDB).all() if not is_anonymised_player(p)]
    rows = [{"player_id": p.player_id,
             "display_name": f"{p.first_name_en or ''} {p.last_name_en or ''}".strip()} for p in players]
    rows.sort(key=lambda r: (r["display_name"].lower(), r["player_id"]))
    return {"players": rows}


@router.get("/eligible-confirmers")
def eligible_confirmers(request: Request, db: Session = Depends(get_db)):
    """Who may be named as the second person on a correction: active coaches and admins other
    than the caller, the same rule the correction route enforces. Nobody else is listed."""
    actor = _actor(request, WRITE_ROLES)
    users = (db.query(UserDB)
             .filter(UserDB.active.is_(True), UserDB.role.in_(sorted(WRITE_ROLES)), UserDB.user_id != actor)
             .order_by(UserDB.username).all())
    return {"confirmers": [{"user_id": u.user_id, "username": u.username} for u in users]}


@router.get("/players/{player_id}/assessments")
def list_player_assessments(player_id: str, request: Request, db: Session = Depends(get_db)):
    """Summaries only (no times), so a coach can reopen an assessment after leaving the page."""
    _actor(request, READ_ROLES)
    if db.get(PlayerDB, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    rows = (db.query(V1AssessmentDB).filter_by(player_id=player_id)
            .order_by(V1AssessmentDB.created_at.desc(), V1AssessmentDB.assessment_id.desc()).all())
    return jsonable_encoder({"assessments": [_assessment_view(db, a, with_runs=False) for a in rows]})


@router.get("/assessments/{assessment_id}")
def read_assessment(assessment_id: str, request: Request, db: Session = Depends(get_db)):
    _actor(request, READ_ROLES)
    return jsonable_encoder(_assessment_view(db, _player_of_assessment(db, assessment_id)))


# ------------------------------------------------------------------------ station runs

@router.post("/assessments/{assessment_id}/station-runs")
def create_or_get_station_run(assessment_id: str, body: CreateStationRunBody, request: Request,
                              db: Session = Depends(get_db)):
    actor = _actor(request, WRITE_ROLES)
    assessment = _player_of_assessment(db, assessment_id)
    if body.station_id not in ENABLED_STATIONS:
        raise HTTPException(status_code=422,
                            detail="This station is not enabled in this API version (S1 only)")
    _require_consent(db, assessment.player_id)
    existing = db.query(V1StationRunDB).filter_by(
        assessment_id=assessment_id, station_id=body.station_id).one_or_none()
    if existing is not None:
        return _respond(_run_view(db, existing, with_trials=False), created=False)
    service = V1AssessmentService(db)

    def act():
        found = db.query(V1StationRunDB).filter_by(
            assessment_id=assessment_id, station_id=body.station_id).one_or_none()
        if found is not None:
            return found, False
        try:
            return service.start_station_run(assessment_id, body.station_id, actor), True
        except V1ConflictError:  # a twin request committed first
            db.rollback()
            found = db.query(V1StationRunDB).filter_by(
                assessment_id=assessment_id, station_id=body.station_id).one_or_none()
            if found is None:
                raise
            return found, False

    run, created = _once_or_reconcile(db, act)
    return _respond(_run_view(db, run, with_trials=False), created=created)


# ----------------------------------------------------------------------------- trials

@router.post("/station-runs/{station_run_id}/trials")
def create_trial(station_run_id: str, body: CreateTrialBody, request: Request,
                 db: Session = Depends(get_db)):
    actor = _actor(request, WRITE_ROLES)
    run = db.get(V1StationRunDB, station_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Station run not found")
    assessment = _player_of_assessment(db, run.assessment_id)
    _require_consent(db, assessment.player_id)
    service = V1AssessmentService(db)

    def existing_trial():
        return db.query(V1TrialDB).filter_by(
            station_run_id=station_run_id, series_key="", attempt_number=body.attempt_number
        ).one_or_none()

    def replay_or_conflict(found):
        if (found.validity, found.invalid_reason) != (body.validity, body.invalid_reason):
            raise HTTPException(status_code=409,
                                detail="This attempt number already exists with different content")
        return _respond(_trial_view(db, found, with_measurements=False), created=False)

    if existing_trial() is not None:
        return replay_or_conflict(existing_trial())

    def act():
        found = existing_trial()
        if found is not None:
            return found, False
        try:
            return service.add_trial(station_run_id, body.attempt_number, actor, series_key="",
                                     validity=body.validity, invalid_reason=body.invalid_reason), True
        except V1ConflictError:  # a twin request committed first
            db.rollback()
            found = existing_trial()
            if found is None:
                raise
            return found, False

    trial, created = _once_or_reconcile(db, act)
    if not created:
        return replay_or_conflict(trial)
    return _respond(_trial_view(db, trial, with_measurements=False), created=True)


# ---------------------------------------------------------------------- measurements

@router.post("/trials/{trial_id}/manual-measurements")
def record_manual_measurement(trial_id: str, body: RecordMeasurementBody, request: Request,
                              db: Session = Depends(get_db)):
    actor = _actor(request, WRITE_ROLES)
    trial, run, assessment = _chain_of_trial(db, trial_id)
    _require_consent(db, assessment.player_id)
    if body.measurement_source not in (None, d.MEASUREMENT_SOURCE_MANUAL):
        raise HTTPException(status_code=422, detail="Official V1 times are HUMAN_MANUAL only")
    service = V1AssessmentService(db)
    # Shape checks that depend on the entry status (the value rules live in the service).
    if body.entry_status == "RECORDED":
        if body.displayed_value is None or body.not_recorded_reason is not None:
            raise HTTPException(status_code=422,
                                detail="A recorded time needs displayed_value and no not_recorded_reason")
    elif body.displayed_value is not None or not body.not_recorded_reason:
        raise HTTPException(status_code=422,
                            detail="NOT_RECORDED needs a reason and must not carry a value")
    unit = body.unit if body.unit is not None else d.UNIT_SECONDS

    def act():
        if body.entry_status == "RECORDED":
            saved = service.record_manual_time(
                trial_id, body.metric_id, body.displayed_value, unit, actor,
                client_entry_id=body.client_entry_id)
        else:
            saved = service.record_not_recorded(
                trial_id, body.metric_id, body.not_recorded_reason, actor,
                client_entry_id=body.client_entry_id)
        return saved, not service.replayed

    measurement, created = _once_or_reconcile(db, act)
    return _respond(measurement_view(db, measurement), created=created)


@router.get("/manual-measurements/{measurement_id}")
def read_manual_measurement(measurement_id: str, request: Request, db: Session = Depends(get_db)):
    _actor(request, READ_ROLES)
    measurement = db.get(V1ManualMeasurementDB, measurement_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return jsonable_encoder(measurement_view(db, measurement))


# ------------------------------------------------------------------------- corrections

@router.post("/manual-measurements/{measurement_id}/corrections")
def correct_manual_measurement(measurement_id: str, body: CorrectMeasurementBody, request: Request,
                               db: Session = Depends(get_db)):
    actor = _actor(request, WRITE_ROLES)
    measurement = db.get(V1ManualMeasurementDB, measurement_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    _, _, assessment = _chain_of_trial(db, measurement.trial_id)
    _require_consent(db, assessment.player_id)

    # Frozen standard 6.3: every correction needs a second person.
    confirmer = db.get(UserDB, body.confirmed_by_user_id)
    if (confirmer is None or not confirmer.active or confirmer.role not in WRITE_ROLES
            or confirmer.user_id == actor):
        raise HTTPException(
            status_code=422,
            detail="A correction needs a different active coach or admin as confirmer")
    try:
        corrected = parse_seconds_to_centiseconds(body.corrected_value)
        expected = parse_seconds_to_centiseconds(body.expected_effective_value)
    except InvalidTimeValue as error:
        raise HTTPException(status_code=422, detail=str(error))
    service = V1AssessmentService(db)

    def replay_of_same_request():
        history = service.history(measurement_id)
        last = history[-1] if history else None
        if (last is not None and last.original_centiseconds == expected
                and last.corrected_centiseconds == corrected and last.reason_code == body.reason_code
                and last.corrected_by_user_id == actor
                and last.confirmed_by_user_id == body.confirmed_by_user_id):
            return last
        return None

    def act():
        replay = replay_of_same_request()
        if replay is not None:                      # the same request again
            return replay, False
        history = service.history(measurement_id)
        current = history[-1].corrected_centiseconds if history else measurement.value_centiseconds
        if current != expected:
            raise V1ConflictError("The value changed since you read it; read it again")
        try:
            return service.correct_manual_time(
                measurement_id, body.corrected_value, actor, body.reason_code,
                reason_text=body.reason_text, confirmed_by_user_id=body.confirmed_by_user_id), True
        except V1ValidationError:
            db.rollback()
            replay = replay_of_same_request()       # a twin committed between our two reads
            if replay is None:
                raise
            return replay, False

    correction, created = _once_or_reconcile(db, act)
    view = measurement_view(db, measurement)
    return _respond({"correction": _correction_view(correction), "measurement": view}, created=created)


# ----------------------------------------------------------------------- web page (S1B2)

@router.get("/sprint", include_in_schema=False)
def sprint_page():
    return FileResponse(STATIC_DIR / "v1_sprint.html", media_type="text/html")


@router.get("/sprint.js", include_in_schema=False)
def sprint_script():
    return FileResponse(STATIC_DIR / "v1_sprint.js", media_type="application/javascript")
