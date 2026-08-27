from sqlalchemy.orm import Session

from app.db_models import SeasonDB
from app.services.id_service import next_entity_id


class SeasonService:
    def __init__(self, db: Session):
        self.db = db

    def list_seasons(self) -> list[SeasonDB]:
        return (
            self.db.query(SeasonDB)
            .order_by(SeasonDB.is_active.desc(), SeasonDB.name.desc())
            .all()
        )

    def get_active(self) -> SeasonDB | None:
        return (
            self.db.query(SeasonDB)
            .filter(SeasonDB.is_active.is_(True))
            .first()
        )

    def create_season(
        self,
        name: str,
        start_date=None,
        end_date=None,
        make_active: bool = False,
    ) -> SeasonDB:
        if make_active:
            self.db.query(SeasonDB).update({"is_active": False})

        season = SeasonDB(
            season_id=next_entity_id(self.db, "season"),
            name=name,
            start_date=start_date,
            end_date=end_date,
            is_active=make_active,
        )
        self.db.add(season)
        self.db.commit()
        self.db.refresh(season)
        return season

    def set_active(self, season_id: str) -> SeasonDB | None:
        season = self.db.get(SeasonDB, season_id)

        if season is None:
            return None

        self.db.query(SeasonDB).update({"is_active": False})
        season.is_active = True
        self.db.commit()
        self.db.refresh(season)
        return season
