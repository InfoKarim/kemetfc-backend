from dataclasses import asdict
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api_schemas import DrillRecommendationSchema, DrillSchema
from app.data_models import DrillData
from app.database import get_db
from app.drill_ranking import rank_drills
from app.drill_upload import DrillUploadError, save_drill_video
from app.services.drill_service import DrillService
from app.services.id_service import next_entity_id
from app.video_storage import VideoStorageError, get_drill_video_storage

router = APIRouter()
logger = logging.getLogger("trainingbuddy.http")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.post("/drills", status_code=201)
def create_drill(
    drill_data: DrillSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    data = drill_data.model_dump()
    data["drill_id"] = data["drill_id"] or next_entity_id(db, "drill")
    drill = DrillData(**data)
    service.add_drill(drill)
    return drill


@router.get("/drills/{drill_id}")
def get_drill(
    drill_id: str,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    drill = service.get_drill(drill_id)

    if drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    return drill


@router.get("/drills")
def get_all_drills(
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    return service.get_all_drills()


@router.put("/drills/{drill_id}")
def update_drill(
    drill_id: str,
    drill_data: DrillSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)

    existing_drill = service.get_drill(drill_id)

    if existing_drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    updated_data = drill_data.model_dump()
    updated_data["drill_id"] = drill_id

    drill = DrillData(**updated_data)
    service.update_drill(drill)

    if (
        existing_drill.video_url.startswith("/uploads/drills/")
        and existing_drill.video_url != drill.video_url
    ):
        try:
            get_drill_video_storage().delete(
                Path(existing_drill.video_url).name
            )
        except VideoStorageError:
            logger.warning(
                "Could not remove replaced drill video %s",
                existing_drill.video_url,
            )

    return drill


@router.delete("/drills/{drill_id}")
def delete_drill(
    drill_id: str,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)
    drill = service.get_drill(drill_id)

    if drill is None:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    if drill.video_url.startswith("/uploads/drills/"):
        filename = Path(drill.video_url).name
        try:
            get_drill_video_storage().delete(filename)
        except VideoStorageError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error),
            ) from error

    deleted = service.delete_drill(drill_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Drill not found",
        )

    return {"message": "Drill deleted"}


@router.post("/drills/recommendations")
def recommend_drills(
    criteria: DrillRecommendationSchema,
    db: Session = Depends(get_db),
):
    service = DrillService(db=db)

    eligible_drills = [
        asdict(drill)
        for drill in service.get_drills_for_age(criteria.age)
        if drill.active
    ]

    return rank_drills(
        drills=eligible_drills,
        weakness=criteria.weakness,
        weakness_score=criteria.weakness_score,
        player_difficulty=criteria.player_difficulty,
        target_duration=criteria.target_duration,
        available_equipment=criteria.available_equipment,
    )


@router.post("/drills/upload", status_code=201)
def upload_drill_video(
    metadata: str = Form(...),
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        payload = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid drill metadata JSON",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Drill metadata must be an object",
        )

    validation_payload = dict(payload)
    validation_payload["drill_id"] = (
        validation_payload.get("drill_id")
        or next_entity_id(db, "drill")
    )
    validation_payload["video_url"] = "/pending.mp4"

    try:
        drill = DrillData(**validation_payload)
        video_url = save_drill_video(video, drill.drill_id)
    except DrillUploadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    drill.video_url = video_url

    service = DrillService(db=db)
    service.add_drill(drill)

    return drill


@router.get("/uploads/drills/{filename}")
def get_uploaded_drill_video(filename: str):
    try:
        storage = get_drill_video_storage()
        video_path = storage.local_path(filename)

        if video_path is not None:
            return FileResponse(video_path, filename=video_path.name)

        download_url = storage.create_download_url(filename)

        if download_url is None:
            raise VideoStorageError("Video not found", status_code=404)

        return RedirectResponse(download_url, status_code=307)
    except (DrillUploadError, VideoStorageError) as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        )


@router.get("/drill-library")
def get_drill_library_page():
    return FileResponse(
        STATIC_DIR / "drill_library.html",
        media_type="text/html",
    )
