"""Smart Soccer Camera tracking API — consumed by the native iOS
KemetFCTracker app (session lifecycle, batched telemetry ingest) and by
the existing web dashboard (viewing results: heat map, metrics, quality,
coach validation labels). Every write here requires the coach/admin role;
guardians get read-only access to their own child's sessions, matching
the same access-control shape as every other player-scoped endpoint in
this app (require_guardian_player_access)."""

from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import (
    CompleteTrackingSessionSchema,
    ConfirmPlayerTrackedSchema,
    CreateTrackingSessionSchema,
    IngestTrackingEventSchema,
    IngestTrackingSamplesSchema,
    PublishTrackingAssessmentSchema,
    RecordCoachValidationLabelSchema,
    RegisterMLModelSchema,
    UpdateMLModelStatusSchema,
)
from app.database import get_db
from app.db_models import CoachValidationLabelDB
from app.dependencies import require_admin, require_guardian_player_access
from app.services.heatmap_service import build_heatmap_payload
from app.services.id_service import next_entity_id
from app.services.model_registry_service import ModelRegistryError, ModelRegistryService
from app.services.player_service import PlayerService
from app.services.tracking_service import TrackingError, TrackingService

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _require_coach_or_admin(request: Request) -> None:
    if request.state.current_user["role"] not in {"admin", "coach"}:
        raise HTTPException(status_code=403, detail="Coach or admin access required")


def _session_payload(session) -> dict:
    return {
        "session_id": session.session_id,
        "player_id": session.player_id,
        "coach_user_id": session.coach_user_id,
        "video_id": session.video_id,
        "tracking_mode": session.tracking_mode,
        "gimbal_model": session.gimbal_model,
        "calibration_status": session.calibration_status,
        "calibration_scale_m_per_unit": session.calibration_scale_m_per_unit,
        "status": session.status,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "player_detector_version": session.player_detector_version,
        "ball_detector_version": session.ball_detector_version,
        "ball_model_status": session.ball_model_status,
        "pose_model_version": session.pose_model_version,
        "tracker_algorithm_version": session.tracker_algorithm_version,
        "framing_algorithm_version": session.framing_algorithm_version,
        "ios_app_version": session.ios_app_version,
    }


def _event_payload(event) -> dict:
    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "occurred_at": event.occurred_at,
        "event_type": event.event_type,
        "details": event.details,
    }


def _label_payload(label) -> dict:
    return {
        "label_id": label.label_id,
        "session_id": label.session_id,
        "label_type": label.label_type,
        "value": label.value,
        "notes": label.notes,
        "coach_user_id": label.coach_user_id,
        "created_at": label.created_at,
    }


def _model_payload(model) -> dict:
    return {
        "model_id": model.model_id,
        "model_name": model.model_name,
        "model_version": model.model_version,
        "model_type": model.model_type,
        "status": model.status,
        "deployment_date": model.deployment_date,
        "training_dataset_version": model.training_dataset_version,
        "evaluation_metrics": model.evaluation_metrics,
        "coreml_artifact_version": model.coreml_artifact_version,
        "backend_artifact_version": model.backend_artifact_version,
        "notes": model.notes,
        "created_at": model.created_at,
    }


@router.get("/tracking-analysis")
def tracking_analysis_page():
    return FileResponse(STATIC_DIR / "tracking_analysis.html")


@router.post("/tracking/sessions", status_code=201)
def create_tracking_session(
    payload: CreateTrackingSessionSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    # The player was already selected by the coach in the iOS app's own
    # player-selection workflow (QR/search/session) BEFORE this call —
    # this endpoint only records that choice, never infers or searches
    # for a player. require_guardian_player_access is a no-op for
    # coach/admin and correctly 404s a guardian trying to record tracking
    # for a child that isn't theirs (guardians never call this in
    # practice, but the check is symmetric with every other endpoint).
    _require_coach_or_admin(request)

    if PlayerService(db=db).get_player(payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        session = TrackingService(db=db).create_session(
            player_id=payload.player_id,
            coach_user_id=request.state.current_user["user_id"],
            tracking_mode=payload.tracking_mode,
            gimbal_model=payload.gimbal_model,
            calibration_scale_m_per_unit=payload.calibration_scale_m_per_unit,
            player_detector_version=payload.player_detector_version,
            ball_detector_version=payload.ball_detector_version,
            ball_model_status=payload.ball_model_status,
            pose_model_version=payload.pose_model_version,
            tracker_algorithm_version=payload.tracker_algorithm_version,
            framing_algorithm_version=payload.framing_algorithm_version,
            ios_app_version=payload.ios_app_version,
        )
    except TrackingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return _session_payload(session)


@router.get("/tracking/sessions/{session_id}")
def get_tracking_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    session = TrackingService(db=db).get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)
    return _session_payload(session)


@router.get("/players/{player_id}/tracking-sessions")
def list_tracking_sessions_for_player(player_id: str, request: Request, db: Session = Depends(get_db)):
    require_guardian_player_access(request, db, player_id)
    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    sessions = TrackingService(db=db).list_sessions_for_player(player_id)
    return {"sessions": [_session_payload(s) for s in sessions]}


@router.post("/tracking/sessions/{session_id}/samples", status_code=201)
def ingest_tracking_samples(
    session_id: str,
    payload: IngestTrackingSamplesSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    # A coach may only stream telemetry into a session THEY started —
    # server-side, never trusting a session_id alone from the client.
    if session.coach_user_id != request.state.current_user["user_id"] and request.state.current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not your tracking session")

    try:
        count = service.ingest_samples(
            session_id, [sample.model_dump() for sample in payload.samples]
        )
    except TrackingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {"ingested": count}


@router.post("/tracking/sessions/{session_id}/events", status_code=201)
def ingest_tracking_event(
    session_id: str,
    payload: IngestTrackingEventSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    if session.coach_user_id != request.state.current_user["user_id"] and request.state.current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not your tracking session")

    try:
        event = service.ingest_event(session_id, payload.event_type, payload.details)
    except TrackingError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _event_payload(event)


@router.get("/tracking/sessions/{session_id}/events")
def list_tracking_events(session_id: str, request: Request, db: Session = Depends(get_db)):
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)
    return {"events": [_event_payload(e) for e in service.list_events(session_id)]}


@router.post("/tracking/sessions/{session_id}/complete")
def complete_tracking_session(
    session_id: str,
    payload: CompleteTrackingSessionSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    if session.coach_user_id != request.state.current_user["user_id"] and request.state.current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not your tracking session")

    try:
        completed = service.complete_session(session_id, video_id=payload.video_id)
    except TrackingError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _session_payload(completed)


@router.get("/tracking/sessions/{session_id}/quality")
def get_tracking_quality(session_id: str, request: Request, db: Session = Depends(get_db)):
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)
    return service.get_quality(session_id)


@router.get("/tracking/sessions/{session_id}/features")
def get_tracking_features(session_id: str, request: Request, db: Session = Depends(get_db)):
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)
    return service.get_or_compute_feature_set(session_id)


@router.get("/tracking/sessions/{session_id}/heatmap")
def get_tracking_heatmap(session_id: str, request: Request, db: Session = Depends(get_db)):
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)

    samples = service.get_samples_as_dicts(session_id)
    return build_heatmap_payload(samples)


@router.post("/tracking/sessions/{session_id}/publish")
def publish_tracking_assessment(
    session_id: str,
    payload: PublishTrackingAssessmentSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    """Admin/coach reviews and explicitly publishes a session's derived
    metrics/skill inference to the Player Profile — never automatic, so
    a coach always has the chance to review before a result becomes
    part of the player's history (see COACH REVIEW step in the
    workflow)."""
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")

    try:
        assessment = service.publish_assessment(
            session_id,
            actor_user_id=request.state.current_user["user_id"],
            assessment_date=payload.assessment_date,
        )
    except TrackingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        "assessment_id": assessment.assessment_id,
        "player_id": assessment.player_id,
        "tracking_session_id": assessment.tracking_session_id,
        "calculated_metrics": assessment.calculated_metrics,
    }


@router.post("/tracking/sessions/{session_id}/coach-labels", status_code=201)
def record_coach_validation_label(
    session_id: str,
    payload: RecordCoachValidationLabelSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")

    label = CoachValidationLabelDB(
        label_id=next_entity_id(db, "coach_validation_label"),
        session_id=session_id,
        label_type=payload.label_type,
        value=payload.value,
        notes=payload.notes,
        coach_user_id=request.state.current_user["user_id"],
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return _label_payload(label)


@router.post("/tracking/sessions/{session_id}/confirm-player", status_code=201)
def confirm_player_tracked(
    session_id: str,
    payload: ConfirmPlayerTrackedSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    """The coach review step (spec section 29): after seeing the
    session's Tracking Quality / Player Lock Coverage / Ball Coverage /
    warnings, the coach explicitly confirms whether the RIGHT child was
    tracked. A thin, purpose-built wrapper around the generic
    coach-labels endpoint (same underlying CoachValidationLabelDB row,
    label_type="correct_player_tracked") — this is real, valuable future
    training-label ground truth, never inferred automatically."""
    _require_coach_or_admin(request)
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")

    label = CoachValidationLabelDB(
        label_id=next_entity_id(db, "coach_validation_label"),
        session_id=session_id,
        label_type="correct_player_tracked",
        value={"answer": payload.answer},
        notes=payload.notes,
        coach_user_id=request.state.current_user["user_id"],
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return _label_payload(label)


@router.get("/tracking/sessions/{session_id}/coach-labels")
def list_coach_validation_labels(session_id: str, request: Request, db: Session = Depends(get_db)):
    service = TrackingService(db=db)
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    require_guardian_player_access(request, db, session.player_id)

    labels = (
        db.query(CoachValidationLabelDB)
        .filter(CoachValidationLabelDB.session_id == session_id)
        .order_by(CoachValidationLabelDB.created_at.desc())
        .all()
    )
    return {"labels": [_label_payload(label) for label in labels]}


# --- Model registry (admin-only) --------------------------------------

@router.get("/tracking/admin/models")
def list_ml_models(request: Request, db: Session = Depends(get_db), model_name: str | None = None):
    require_admin(request)
    models = ModelRegistryService(db=db).list_models(model_name=model_name)
    return {"models": [_model_payload(m) for m in models]}


@router.post("/tracking/admin/models", status_code=201)
def register_ml_model(payload: RegisterMLModelSchema, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    try:
        model = ModelRegistryService(db=db).register_model(
            actor_user_id=request.state.current_user["user_id"],
            model_name=payload.model_name,
            model_version=payload.model_version,
            model_type=payload.model_type,
            status=payload.status,
            training_dataset_version=payload.training_dataset_version,
            evaluation_metrics=payload.evaluation_metrics,
            coreml_artifact_version=payload.coreml_artifact_version,
            backend_artifact_version=payload.backend_artifact_version,
            notes=payload.notes,
        )
    except ModelRegistryError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _model_payload(model)


@router.patch("/tracking/admin/models/{model_id}")
def update_ml_model_status(
    model_id: str,
    payload: UpdateMLModelStatusSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        model = ModelRegistryService(db=db).set_status(model_id, payload.status)
    except ModelRegistryError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return _model_payload(model)
