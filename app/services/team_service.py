from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.data_models import TeamData
from app.db_models import TeamDB


class TeamService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, team: TeamData) -> TeamDB:
        return TeamDB(
            team_id=team.team_id,
            name=team.name,
            age_group=team.age_group,
            coach_name=team.coach_name,
            season_id=team.season_id,
            active=team.active,
            created_at=team.created_at,
        )

    def _to_domain(self, db_team: TeamDB) -> TeamData:
        return TeamData(
            team_id=db_team.team_id,
            name=db_team.name,
            age_group=db_team.age_group,
            coach_name=db_team.coach_name,
            season_id=db_team.season_id,
            active=db_team.active,
            created_at=db_team.created_at,
        )

    def add_team(self, team: TeamData) -> None:
        self.db.merge(self._to_db(team))
        self.db.commit()

    def get_team(self, team_id: str) -> TeamData | None:
        db_team = self.db.get(TeamDB, team_id)

        if db_team is None:
            return None

        return self._to_domain(db_team)


    def get_all_teams(self) -> list[TeamData]:
        db_teams = self.db.query(TeamDB).all()

        return [
            self._to_domain(team)
            for team in db_teams
        ]

    def update_team(self, team: TeamData) -> bool:
        existing = self.db.get(TeamDB, team.team_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(team))
        self.db.commit()
        return True

    def delete_team(self, team_id: str) -> bool:
        db_team = self.db.get(TeamDB, team_id)

        if db_team is None:
            return False

        self.db.delete(db_team)
        self.db.commit()
        return True
