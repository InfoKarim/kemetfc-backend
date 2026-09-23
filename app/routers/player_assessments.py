import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api_schemas import (
    CreatePredictionSchema,
    RecordBallMasterySchema,
    RecordYoYoKidsSchema,
    UpdateCoachMessageSchema,
)
from app.database import get_db
from app.db_models import PlayerAssessmentDB
from app.dependencies import require_guardian_player_access
from app.player_video_upload import SUPPORTED_VIDEO_TYPES
from app.services.development_report_service import (
    build_development_report,
    set_coach_message,
)
from app.services.development_trend_service import DevelopmentTrendService
from app.ml_prediction import InsufficientTrainingDataError
from app.ml_validation import InsufficientValidationDataError
from app.services.ml_prediction_service import MLPredictionService, NoRecordedMetricError
from app.services.ml_validation_service import MLValidationService
from app.services.player_assessment_service import PlayerAssessmentService
from app.services.player_metric_service import PlayerMetricService
from app.services.player_service import PlayerService
from app.services.smart_recommendation_service import (
    RecommendationError,
    analyze_ball_mastery_video,
    is_provider_configured,
)
from app.video_file_validation import InvalidVideoContent, validate_video_signature
from app.video_frame_sampling import (
    VideoFrameSamplingError,
    sample_frames_as_base64_jpeg,
)

router = APIRouter()

# A quick assessment clip, not a full match recording — kept far below the
# 500 MB limit used for stored match footage since this file is only ever
# read for frame sampling and then discarded.
MAX_ANALYSIS_VIDEO_BYTES = 150 * 1024 * 1024


def _require_staff(request: Request) -> None:
    """Recording/deleting an assessment is a coach/admin action — a
    guardian is a read-only observer of their own linked child's results,
    never a data-entry actor, regardless of which player is targeted.
    """
    if request.state.current_user["role"] == "guardian":
        raise HTTPException(
            status_code=403,
            detail="Guardians cannot record or delete assessments",
        )


@router.post(
    "/players/{player_id}/physical-assessments/yoyo-kids",
    status_code=201,
)
def record_yoyo_kids_assessment(
    player_id: str,
    payload: RecordYoYoKidsSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    service = PlayerAssessmentService(db=db)
    return service.record_yoyo_kids(
        player=player,
        payload=payload,
        recorded_by_user_id=request.state.current_user["user_id"],
    )


@router.post(
    "/players/{player_id}/technical-assessments/ball-mastery",
    status_code=201,
)
def record_ball_mastery_assessment(
    player_id: str,
    payload: RecordBallMasterySchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    service = PlayerAssessmentService(db=db)
    return service.record_ball_mastery(
        player=player,
        payload=payload,
        recorded_by_user_id=request.state.current_user["user_id"],
    )


@router.post("/players/{player_id}/technical-assessments/ball-mastery/analyze-video")
async def analyze_ball_mastery_assessment_video(
    player_id: str,
    request: Request,
    video: UploadFile,
    db: Session = Depends(get_db),
):
    """AI-suggested Ball Mastery ratings from a handful of still frames
    sampled off an uploaded clip — Claude has no native video input, so
    this is a judgment from snapshots, not continuous motion. Nothing is
    saved here: the coach reviews/edits the suggestion in the normal
    record-result form before it's persisted, and the uploaded video is
    discarded immediately after sampling, never stored.
    """
    _require_staff(request)

    if not is_provider_configured("claude"):
        raise HTTPException(
            status_code=404,
            detail="AI video analysis is not configured",
        )

    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    filename = video.filename or ""
    extension = Path(filename).suffix.lower()
    expected_content_type = SUPPORTED_VIDEO_TYPES.get(extension)

    if expected_content_type is None:
        raise HTTPException(status_code=400, detail="Unsupported video format")

    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            total_size = 0

            while chunk := await video.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_ANALYSIS_VIDEO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 150 MB limit for AI analysis",
                    )
                tmp_file.write(chunk)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="Video file is empty")

        with open(tmp_path, "rb") as handle:
            try:
                validate_video_signature(handle, filename)
            except InvalidVideoContent as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        try:
            frames = sample_frames_as_base64_jpeg(tmp_path)
        except VideoFrameSamplingError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        try:
            return analyze_ball_mastery_video(frames)
        except RecommendationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        if tmp_path is not None:
            os.unlink(tmp_path)


@router.get("/players/{player_id}/assessments")
def list_player_assessments(
    player_id: str,
    request: Request,
    pillar: str | None = None,
    db: Session = Depends(get_db),
):
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    require_guardian_player_access(request, db, player_id)

    service = PlayerAssessmentService(db=db)
    return {
        "assessments": service.list_for_player(player_id, pillar=pillar)
    }


@router.delete("/player-assessments/{assessment_id}")
def delete_player_assessment(
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_staff(request)
    assessment = db.get(PlayerAssessmentDB, assessment_id)

    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")

    require_guardian_player_access(request, db, assessment.player_id)

    PlayerAssessmentService(db=db).delete_assessment(assessment_id)

    return {"message": "Assessment deleted"}


@router.get("/players/{player_id}/development-report")
def get_player_development_report(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """The Guardian-facing summary: assessment coverage, latest-vs-
    previous progress, coach-observed strengths/priorities, and the
    coach's current message — never a fabricated overall score. See
    build_development_report() for exactly what evidence backs each
    field.
    """
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    require_guardian_player_access(request, db, player_id)

    return build_development_report(db, player_id)


# Day-based approximations, not calendar-precise months — documented
# here rather than silently rounded, since "6 months" varies 181-184
# real calendar days depending on which months it spans.
DEVELOPMENT_HISTORY_PERIODS: dict[str, int | None] = {
    "30d": 30,
    "90d": 90,
    "6m": 180,
    "12m": 365,
    "all": None,
}


@router.get("/players/{player_id}/development-history")
def get_player_development_history(
    player_id: str,
    request: Request,
    period: str = "all",
    metric_name: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Chart-ready time series for the Development Trend Chart: one
    ordered (date, score) list per metric. Returns every metric the
    player has recorded data for when ``metric_name`` is omitted, so the
    frontend can offer "which metrics appear on the chart" as a client-
    side filter over real data rather than a second round trip.
    """
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    require_guardian_player_access(request, db, player_id)

    if period not in DEVELOPMENT_HISTORY_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid period. Choose one of: "
                f"{', '.join(DEVELOPMENT_HISTORY_PERIODS)}"
            ),
        )

    since = None
    period_days = DEVELOPMENT_HISTORY_PERIODS[period]
    if period_days is not None:
        since = date.today() - timedelta(days=period_days)

    metric_service = PlayerMetricService(db)
    names = metric_name or metric_service.list_metric_names(player_id)

    return {
        "player_id": player_id,
        "period": period,
        "metrics": {
            name: [
                {"recorded_at": row.recorded_at, "score": row.metric_score}
                for row in metric_service.list_for_player(
                    player_id, metric_name=name, since=since
                )
            ]
            for name in names
        },
    }


@router.get("/players/{player_id}/development-trends")
def get_player_development_trends(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Per-metric direction/velocity/plateau/acceleration summary (see
    DevelopmentTrendService). ``metrics_without_trend`` names any metric
    the player has real recorded data for but that has no configured,
    defensible minimum_meaningful_change yet — surfaced explicitly
    rather than silently dropped, so the frontend/coach can see the gap
    instead of wondering why a metric with data has no trend.
    """
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    require_guardian_player_access(request, db, player_id)

    trend_service = DevelopmentTrendService(db)
    all_metric_names = set(PlayerMetricService(db).list_metric_names(player_id))
    trends = trend_service.get_all_trends(player_id)
    trended_names = {trend.metric_name for trend in trends}

    return {
        "player_id": player_id,
        "trends": trends,
        "metrics_without_trend": sorted(all_metric_names - trended_names),
    }


@router.post("/players/{player_id}/predictions", status_code=201)
def create_player_prediction(
    player_id: str,
    payload: CreatePredictionSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    """Baseline ML prediction (Step 5 — see app/ml_prediction.py's
    header for the model, its features, and what it deliberately does
    not claim). Staff-only: this trains/evaluates a model on demand, not
    a passive read like development-history/trends.
    """
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        return MLPredictionService(db).predict(
            player_id=player_id,
            metric_name=payload.metric_name,
            forecast_days=payload.forecast_days,
            model_scope=payload.model_scope,
        )
    except NoRecordedMetricError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InsufficientTrainingDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/players/{player_id}/predictions")
def list_player_predictions(
    player_id: str,
    request: Request,
    metric_name: str | None = None,
    db: Session = Depends(get_db),
):
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    return {
        "predictions": MLPredictionService(db).list_for_player(
            player_id, metric_name=metric_name
        )
    }


@router.get("/players/{player_id}/model-validation")
def get_model_validation(
    player_id: str,
    request: Request,
    metric_name: str,
    model_scope: str = "population",
    db: Session = Depends(get_db),
):
    """Step 6: a genuine held-out evaluation (MAE/RMSE/R² on a
    chronological train/validation/test split) of the same baseline
    model /predictions trains — read-only, nothing persisted. For
    model_scope="population" the path's player_id is only used for the
    existence/auth check; the population fit and its evaluation pool
    every player's own history for this metric, same as
    POST /predictions' population scope.
    """
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        return MLValidationService(db).validate(
            metric_name=metric_name,
            model_scope=model_scope,
            player_id=player_id,
        )
    except InsufficientValidationDataError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/players/{player_id}/coach-message")
def update_player_coach_message(
    player_id: str,
    payload: UpdateCoachMessageSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_staff(request)
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    return set_coach_message(
        db,
        player_id=player_id,
        message=payload.message,
        next_focus=payload.next_focus,
        updated_by_user_id=request.state.current_user["user_id"],
    )
