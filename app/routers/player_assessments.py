import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api_schemas import RecordBallMasterySchema, RecordYoYoKidsSchema
from app.database import get_db
from app.player_video_upload import SUPPORTED_VIDEO_TYPES
from app.services.player_assessment_service import PlayerAssessmentService
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
    pillar: str | None = None,
    db: Session = Depends(get_db),
):
    player = PlayerService(db=db).get_player(player_id)

    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    service = PlayerAssessmentService(db=db)
    return {
        "assessments": service.list_for_player(player_id, pillar=pillar)
    }


@router.delete("/player-assessments/{assessment_id}")
def delete_player_assessment(
    assessment_id: str,
    db: Session = Depends(get_db),
):
    service = PlayerAssessmentService(db=db)
    deleted = service.delete_assessment(assessment_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Assessment not found")

    return {"message": "Assessment deleted"}
