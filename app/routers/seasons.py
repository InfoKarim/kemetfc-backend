from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api_schemas import CreateSeasonSchema
from app.database import get_db
from app.dependencies import require_admin, season_payload
from app.services.season_service import SeasonService

router = APIRouter()


@router.get("/seasons")
def get_seasons(db: Session = Depends(get_db)):
    return [season_payload(season) for season in SeasonService(db=db).list_seasons()]


@router.post("/seasons", status_code=201)
def create_season(
    season_data: CreateSeasonSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    season = SeasonService(db=db).create_season(
        name=season_data.name,
        start_date=season_data.start_date,
        end_date=season_data.end_date,
        make_active=season_data.make_active,
    )
    return season_payload(season)


@router.post("/seasons/{season_id}/activate")
def activate_season(
    season_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    season = SeasonService(db=db).set_active(season_id)

    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")

    return season_payload(season)
