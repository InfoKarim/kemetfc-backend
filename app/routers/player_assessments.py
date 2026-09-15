from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api_schemas import RecordBallMasterySchema, RecordYoYoKidsSchema
from app.database import get_db
from app.services.player_assessment_service import PlayerAssessmentService
from app.services.player_service import PlayerService

router = APIRouter()


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
