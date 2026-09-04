from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import (
    AddPlanVideoSchema,
    UpdateTrainingPlanDetailsSchema,
    UpdateTrainingPlanStatusSchema,
)
from app.database import get_db
from app.services.training_plan_service import TrainingPlanService

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/training-plans")
def get_all_training_plans(
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    return service.get_all_plans()


@router.get("/training-plans/{plan_id}")
def get_training_plan(
    plan_id: str,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    return plan


@router.patch("/training-plans/{plan_id}/status")
def update_training_plan_status(
    plan_id: str,
    status_data: UpdateTrainingPlanStatusSchema,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    plan.status = status_data.status
    service.update_plan(plan)
    return plan


@router.patch("/training-plans/{plan_id}")
def update_training_plan_details(
    plan_id: str,
    plan_data: UpdateTrainingPlanDetailsSchema,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    if plan_data.player_difficulty is not None:
        plan.player_difficulty = plan_data.player_difficulty

    if plan_data.target_duration is not None:
        plan.target_duration = plan_data.target_duration

    if plan_data.available_equipment is not None:
        plan.available_equipment = plan_data.available_equipment

    service.update_plan(plan)
    return plan


@router.delete("/training-plans/{plan_id}")
def delete_training_plan(
    plan_id: str,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    deleted = service.delete_plan(plan_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    return {"message": "Training plan deleted"}


@router.post("/training-plans/{plan_id}/recommendations/videos", status_code=201)
def add_video_to_training_plan(
    plan_id: str,
    video_data: AddPlanVideoSchema,
    db: Session = Depends(get_db),
):
    service = TrainingPlanService(db=db)
    plan = service.get_plan(plan_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Training plan not found",
        )

    video_drill = {
        "drill_id": None,
        "name": video_data.title,
        "category": video_data.weakness,
        "description": (
            f"AI-suggested video via {video_data.channel}"
            if video_data.channel
            else "AI-suggested training video"
        ),
        "min_age": 0,
        "max_age": 99,
        "difficulty": None,
        "duration_minutes": None,
        "equipment": [],
        "video_url": video_data.url,
        "active": True,
    }

    recommendations = list(plan.recommendations or [])
    group = next(
        (
            item
            for item in recommendations
            if item.get("weakness") == video_data.weakness
        ),
        None,
    )

    if group is None:
        group = {
            "weakness": video_data.weakness,
            "weakness_score": None,
            "drills": [],
        }
        recommendations.append(group)

    drills = list(group.get("drills") or [])

    if not any(
        drill.get("video_url") == video_data.url for drill in drills
    ):
        drills.append(video_drill)

    group["drills"] = drills
    plan.recommendations = recommendations
    service.update_plan(plan)
    return plan


@router.get("/training-plan-details")
def training_plan_details_page():
    return FileResponse(STATIC_DIR / "training_plan_details.html")


@router.get("/training-plans-dashboard")
def training_plans_dashboard():
    return FileResponse(STATIC_DIR / "training_plans.html")
