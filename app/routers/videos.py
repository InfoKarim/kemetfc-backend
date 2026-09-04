import csv
from datetime import datetime
import io
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.analysis_result_storage import get_analysis_result_storage
from app.api_schemas import (
    CreateVideoAnalysisJobSchema,
    MLDatasetEntryCreateSchema,
    MLDatasetEntryReviewSchema,
    PlayerVideoUploadMetadataSchema,
    ReviewVideoAnalysisJobSchema,
    UpdateVideoAnalysisJobSchema,
    VideoSchema,
)
from app.data_models import DataRecord, VideoAnalysisJobData, VideoData
from app.database import get_db
from app.db_models import VideoDB
from app.dependencies import (
    is_minor,
    ml_dataset_entry_payload,
    require_admin,
    require_guardian_player_access,
    require_guardian_video_access,
)
from app.player_video_upload import (
    PlayerVideoUploadError,
    delete_player_video,
    save_player_video,
)
from app.services.data_record_service import DataRecordService
from app.services.id_service import next_entity_id
from app.services.ml_dataset_service import MLDatasetEntryError, MLDatasetService
from app.services.player_service import PlayerService
from app.services.privacy_service import PrivacyService
from app.services.video_analysis_job_service import VideoAnalysisJobService
from app.services.video_service import VideoDeletionError, VideoService
from app.video_analysis_publication import VideoAnalysisPublisher
from app.video_storage import VideoStorageError, get_video_storage

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/ml-dataset-registry")
def ml_dataset_registry_page(request: Request):
    require_admin(request)
    return FileResponse(STATIC_DIR / "ml_dataset_registry.html")


@router.post("/videos", status_code=201)
def create_video(
    video_data: VideoSchema,
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    data = video_data.model_dump()
    data["video_id"] = data["video_id"] or next_entity_id(db, "video")
    video = VideoData(**data)
    service.add_video(video)
    return video


@router.post("/videos/upload", status_code=201)
def upload_player_video(
    request: Request,
    metadata: str = Form(...),
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        upload_data = (
            PlayerVideoUploadMetadataSchema.model_validate(
                json.loads(metadata)
            )
        )
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid video metadata",
        )

    player_service = PlayerService(db=db)

    player = player_service.get_player(upload_data.player_id)

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    require_guardian_player_access(request, db, player.player_id)

    record_service = DataRecordService(db=db)
    video_service = VideoService(db=db)

    upload_data.video_id = (
        upload_data.video_id or next_entity_id(db, "video")
    )
    upload_data.record_id = (
        upload_data.record_id or next_entity_id(db, "record")
    )

    if record_service.get_record(upload_data.record_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Data record already exists",
        )

    if video_service.get_video(upload_data.video_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Video already exists",
        )

    try:
        saved = save_player_video(
            video=video,
            video_id=upload_data.video_id,
        )
    except PlayerVideoUploadError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        )

    created_at = datetime.now()

    record = DataRecord(
        record_id=upload_data.record_id,
        player_id=upload_data.player_id,
        source_type="video",
        created_at=created_at,
        data_type="player_video",
        status="completed",
        original_file_path=saved.public_path,
        analysis_id=f"PENDING_{upload_data.video_id}",
        schema_version=upload_data.schema_version,
        created_by=upload_data.created_by,
    )

    player_video = VideoData(
        video_id=upload_data.video_id,
        record_id=upload_data.record_id,
        video_type=upload_data.video_type,
        duration_seconds=upload_data.duration_seconds,
        recorded_at=created_at,
        session_id=upload_data.session_id,
        location_id=upload_data.location_id,
        capture_device=upload_data.capture_device,
        resolution=upload_data.resolution,
        frame_rate_fps=upload_data.frame_rate_fps,
        file_size_mb=saved.file_size_mb,
        file_format=saved.file_format,
        file_path=saved.public_path,
        checksum=saved.checksum,
        original_preserved=True,
        ai_processing_status="pending",
        ai_processed_at=None,
        ai_model_version=None,
        ai_confidence_score=None,
        requires_human_review=False,
        review_reason="",
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        analysis_approved=False,
        approved_by=None,
        approved_at=None,
    )

    try:
        record_service.add_record(record)
        video_service.add_video(player_video)
    except Exception:
        db.rollback()
        record_service.delete_record(upload_data.record_id)
        delete_player_video(saved.filename)
        raise

    PrivacyService(db=db).record_event(
        actor_user_id=request.state.current_user["user_id"],
        action="minor_video_uploaded" if is_minor(player.date_of_birth) else "video_uploaded",
        resource_type="video",
        resource_id=player_video.video_id,
        details={"player_id": player.player_id},
    )

    return player_video


@router.get("/uploads/videos/{filename}")
def get_uploaded_player_video(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        video_id = Path(filename).stem
        video = db.get(VideoDB, video_id)

        if video is None or Path(video.file_path).name != filename:
            raise VideoStorageError("Video not found", status_code=404)

        storage = get_video_storage()
        path = storage.local_path(filename)

        PrivacyService(db=db).record_event(
            actor_user_id=request.state.current_user["user_id"],
            action="video_accessed",
            resource_type="video",
            resource_id=video_id,
            details={},
        )

        if path is not None:
            return FileResponse(path, filename=path.name)

        download_url = storage.create_download_url(filename)

        if download_url is None:
            raise VideoStorageError("Video not found", status_code=404)

        return RedirectResponse(download_url, status_code=307)
    except (PlayerVideoUploadError, VideoStorageError) as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        )


@router.get("/videos")
def get_all_videos(
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    return service.get_all_videos()


@router.post("/videos/{video_id}/analysis-jobs", status_code=201)
def create_video_analysis_job(
    video_id: str,
    job_data: CreateVideoAnalysisJobSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    video_service = VideoService(db=db)

    if video_service.get_video(video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_guardian_video_access(request, db, video_id)

    job_service = VideoAnalysisJobService(db=db)

    job_id = job_data.job_id or next_entity_id(db, "analysis_job")

    if job_service.get_job(job_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Analysis job already exists",
        )

    job = VideoAnalysisJobData(
        job_id=job_id,
        video_id=video_id,
        analysis_type=job_data.analysis_type,
        status="queued",
        created_at=datetime.now(),
        started_at=None,
        completed_at=None,
        progress_percent=0.0,
        attempt_count=0,
        max_attempts=job_data.max_attempts,
        model_name=None,
        model_version=None,
        result_path=None,
        error_message=None,
        target_track_id=job_data.target_track_id,
    )
    job_service.add_job(job)
    return job


@router.get("/videos/{video_id}/analysis-jobs")
def get_video_analysis_jobs(
    video_id: str,
    db: Session = Depends(get_db),
):
    if VideoService(db=db).get_video(video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")

    return VideoAnalysisJobService(db=db).get_video_jobs(video_id)


@router.get("/analysis-jobs/{job_id}")
def get_video_analysis_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    job = VideoAnalysisJobService(db=db).get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    return job


@router.get("/analysis-jobs/{job_id}/result")
def get_video_analysis_result(
    job_id: str,
    db: Session = Depends(get_db),
):
    job = VideoAnalysisJobService(db=db).get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Analysis job is not completed",
        )

    try:
        payload = get_analysis_result_storage().read(
            job_id,
            job.result_path,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return Response(content=payload, media_type="application/json")


@router.put("/analysis-jobs/{job_id}/review")
def review_video_analysis_job(
    job_id: str,
    review_data: ReviewVideoAnalysisJobSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    service = VideoAnalysisJobService(db=db)

    try:
        job = service.review_job(
            job_id=job_id,
            reviewed_by=request.state.current_user["user_id"],
            **review_data.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    VideoAnalysisPublisher(db=db).apply_human_review(
        job_id=job.job_id,
        video_id=job.video_id,
        review_status=job.review_status,
        reviewed_by=job.reviewed_by,
        review_notes=job.review_notes,
    )

    return job


@router.patch("/analysis-jobs/{job_id}")
def update_video_analysis_job(
    job_id: str,
    update_data: UpdateVideoAnalysisJobSchema,
    db: Session = Depends(get_db),
):
    service = VideoAnalysisJobService(db=db)

    try:
        job = service.transition_job(
            job_id=job_id,
            **update_data.model_dump(),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error))

    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    return job


@router.get("/videos/{video_id}")
def get_video(
    video_id: str,
    db: Session = Depends(get_db),
):
    service = VideoService(db=db)
    video = service.get_video(video_id)

    if video is None:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return video


@router.put("/videos/{video_id}")
def update_video(
    video_id: str,
    video_data: VideoSchema,
    db: Session = Depends(get_db),
):
    if video_data.video_id is not None and video_id != video_data.video_id:
        raise HTTPException(
            status_code=400,
            detail="Video ID cannot be changed",
        )

    service = VideoService(db=db)
    data = video_data.model_dump()
    data["video_id"] = video_id
    video = VideoData(**data)
    updated = service.update_video(video)

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return video


@router.delete("/videos/{video_id}")
def delete_video(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    service = VideoService(db=db)
    try:
        deleted = service.delete_video(video_id)
    except VideoDeletionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return {"message": "Video deleted"}


@router.post("/videos/{video_id}/ml-dataset-entry", status_code=201)
def flag_video_for_ml_dataset(
    video_id: str,
    entry_data: MLDatasetEntryCreateSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    service = MLDatasetService(db=db)

    try:
        entry = service.flag_video(
            entry_id=next_entity_id(db, "ml_dataset_entry"),
            video_id=video_id,
            flagged_by_user_id=request.state.current_user["user_id"],
            **entry_data.model_dump(),
        )
    except MLDatasetEntryError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return ml_dataset_entry_payload(entry)


@router.get("/ml-dataset-entries")
def list_ml_dataset_entries(
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    return [
        ml_dataset_entry_payload(entry)
        for entry in MLDatasetService(db=db).list_entries()
    ]


@router.patch("/ml-dataset-entries/{entry_id}")
def review_ml_dataset_entry(
    entry_id: str,
    review_data: MLDatasetEntryReviewSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    entry = MLDatasetService(db=db).review_entry(
        entry_id=entry_id,
        status=review_data.status,
        reviewed_by_user_id=request.state.current_user["user_id"],
    )

    if entry is None:
        raise HTTPException(status_code=404, detail="Dataset entry not found")

    return ml_dataset_entry_payload(entry)


@router.get("/ml-dataset-entries/export.csv")
def export_ml_dataset_entries_csv(
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    entries = [
        entry
        for entry in MLDatasetService(db=db).list_entries()
        if entry.status == "approved"
    ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "entry_id", "video_id", "team_id", "age_band", "sex_cohort",
        "camera_id", "lighting", "consent_id", "flagged_at",
    ])
    for entry in entries:
        writer.writerow([
            entry.entry_id,
            entry.video_id,
            entry.team_id or "",
            entry.age_band,
            entry.sex_cohort,
            entry.camera_id,
            entry.lighting,
            entry.consent_id,
            entry.flagged_at.isoformat(),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=ml_dataset_sources.csv"
        },
    )


@router.get("/add-video")
def add_video_page():
    return FileResponse(STATIC_DIR / "add_training_video.html")


@router.get("/upload-player-video")
def upload_player_video_page():
    return FileResponse(STATIC_DIR / "add_video.html")


@router.get("/videos-dashboard")
def videos_dashboard():
    return FileResponse(STATIC_DIR / "videos.html")


@router.get("/video-analysis-details")
def video_analysis_details_page():
    return FileResponse(STATIC_DIR / "video_analysis_details.html")


@router.get("/body-analysis-3d")
def body_analysis_3d_page():
    return FileResponse(STATIC_DIR / "body_analysis_3d.html")
