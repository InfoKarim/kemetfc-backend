from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import TeamSchema
from app.data_models import TeamData
from app.database import get_db
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id
from app.services.player_service import PlayerService
from app.services.team_service import TeamService

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.post("/teams", status_code=201)
def create_team(
    team_data: TeamSchema,
    db: Session = Depends(get_db),
):
    data = team_data.model_dump()
    data["team_id"] = data["team_id"] or next_entity_id(db, "team")
    data["created_at"] = utcnow()
    team = TeamData(**data)
    service = TeamService(db=db)
    service.add_team(team)
    return team


@router.get("/teams/{team_id}")
def get_team(
    team_id: str,
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    team = service.get_team(team_id)

    if team is None:
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return team


@router.get("/teams")
def get_all_teams(
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    return service.get_all_teams()


@router.put("/teams/{team_id}")
def update_team(
    team_id: str,
    team_data: TeamSchema,
    db: Session = Depends(get_db),
):
    if team_data.team_id is not None and team_id != team_data.team_id:
        raise HTTPException(
            status_code=400,
            detail="Team ID mismatch",
        )

    service = TeamService(db=db)
    existing_team = service.get_team(team_id)

    if existing_team is None:
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    data = team_data.model_dump()
    data["team_id"] = team_id
    data["created_at"] = existing_team.created_at
    team = TeamData(**data)

    if not service.update_team(team):
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return team


@router.delete("/teams/{team_id}")
def delete_team(
    team_id: str,
    db: Session = Depends(get_db),
):
    service = TeamService(db=db)
    player_service = PlayerService(db=db)

    if player_service.get_players_by_team(team_id):
        raise HTTPException(
            status_code=409,
            detail="Team still has assigned players",
        )

    if not service.delete_team(team_id):
        raise HTTPException(
            status_code=404,
            detail="Team not found",
        )

    return {"message": "Team deleted"}


@router.get("/teams-dashboard")
def teams_dashboard_page():
    return FileResponse(STATIC_DIR / "teams.html")


@router.get("/add-team")
def add_team_page():
    return FileResponse(STATIC_DIR / "add_team.html")


@router.get("/team-details")
def team_details_page():
    return FileResponse(STATIC_DIR / "team_details.html")
