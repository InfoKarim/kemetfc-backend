from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import MatchDB
from app.data_models import MatchData


class MatchService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, match: MatchData) -> MatchDB:
        return MatchDB(
            match_id=match.match_id,
            competition_id=match.competition_id,
            season_id=match.season_id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            match_date=match.match_date,
            venue_id=match.venue_id,
            status=match.status,
            home_score=match.home_score,
            away_score=match.away_score,
        )

    def _to_domain(self, db_match: MatchDB) -> MatchData:
        return MatchData(
            match_id=db_match.match_id,
            competition_id=db_match.competition_id,
            season_id=db_match.season_id,
            home_team_id=db_match.home_team_id,
            away_team_id=db_match.away_team_id,
            match_date=db_match.match_date,
            venue_id=db_match.venue_id,
            status=db_match.status,
            home_score=db_match.home_score,
            away_score=db_match.away_score,
        )

    def add_match(self, match: MatchData) -> None:
        self.db.merge(self._to_db(match))
        self.db.commit()

    def get_match(self, match_id: str) -> MatchData | None:
        db_match = self.db.get(MatchDB, match_id)

        if db_match is None:
            return None

        return self._to_domain(db_match)

    def get_all_matches(self) -> list[MatchData]:
        db_matches = self.db.query(MatchDB).all()
        return [self._to_domain(match) for match in db_matches]

    def delete_match(self, match_id: str) -> bool:
        db_match = self.db.get(MatchDB, match_id)

        if db_match is None:
            return False

        self.db.delete(db_match)
        self.db.commit()
        return True

    def update_match(self, match: MatchData) -> bool:
        existing = self.db.get(MatchDB, match.match_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(match))
        self.db.commit()
        return True
