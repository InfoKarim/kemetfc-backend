from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import MatchSchema
from app.data_models import MatchData
from app.database import get_db
from app.services.id_service import next_entity_id
from app.services.match_service import MatchService
from app.services.team_service import TeamService

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.post("/matches", status_code=201)
def create_match(
    match_data: MatchSchema,
    db: Session = Depends(get_db),
):
    team_service = TeamService(db=db)

    for team_id in (
        match_data.home_team_id,
        match_data.away_team_id,
    ):
        if team_service.get_team(team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    service = MatchService(db=db)
    data = match_data.model_dump()
    data["match_id"] = data["match_id"] or next_entity_id(db, "match")
    match = MatchData(**data)
    service.add_match(match)
    return match


@router.get("/matches")
def get_all_matches(
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    return service.get_all_matches()


@router.get("/matches/{match_id}")
def get_match(
    match_id: str,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    match = service.get_match(match_id)

    if match is None:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    return match


@router.put("/matches/{match_id}")
def update_match(
    match_id: str,
    match_data: MatchSchema,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    existing_match = service.get_match(match_id)

    if existing_match is None:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    team_service = TeamService(db=db)

    for team_id in (
        match_data.home_team_id,
        match_data.away_team_id,
    ):
        if team_service.get_team(team_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Team not found",
            )

    updated_data = match_data.model_dump()
    updated_data["match_id"] = match_id

    updated_match = MatchData(**updated_data)
    service.update_match(updated_match)

    return updated_match


@router.delete("/matches/{match_id}")
def delete_match(
    match_id: str,
    db: Session = Depends(get_db),
):
    service = MatchService(db=db)
    deleted = service.delete_match(match_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Match not found",
        )

    return {"message": "Match deleted"}


@router.get("/add-match")
def add_match_page():
    return FileResponse(STATIC_DIR / "add_match.html")


@router.get("/matches-dashboard")
def matches_dashboard():
    return FileResponse(STATIC_DIR / "matches.html")
