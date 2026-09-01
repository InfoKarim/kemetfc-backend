from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import PlayerDB
from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.tactical_profile import TacticalProfile
from app.match_performance import MatchPerformance


# Players created before the Tactical Profile field existed (or before it
# was redesigned to weighted categories) have no stored value, or a value
# shaped for the old 4-field version — default to a neutral midpoint rather
# than crashing or silently excluding them from tactical-aware features
# until re-scored.
_DEFAULT_TACTICAL_PROFILE = {
    "positioning_spatial_intelligence": 70.0,
    "attacking_contribution_in_possession": 70.0,
    "attacking_contribution_off_ball": 70.0,
    "defensive_tactical_contribution": 70.0,
    "transitions": 70.0,
    "decision_quality": 70.0,
    "collective_coordination": 70.0,
    "set_piece_contribution": 70.0,
}


class PlayerService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, player: Player) -> PlayerDB:
        return PlayerDB(
            player_id=player.player_id,
            first_name_ar=player.first_name_ar,
            last_name_ar=player.last_name_ar,
            first_name_en=player.first_name_en,
            last_name_en=player.last_name_en,
            date_of_birth=player.date_of_birth,
            sex=player.sex,
            team_id=player.team_id,
            physical_profile=player.physical_profile.__dict__,
            technical_profile=player.technical_profile.__dict__,
            mental_profile=player.mental_profile.__dict__,
            match_performance=player.match_performance.__dict__,
            tactical_profile=player.tactical_profile.__dict__,
            created_at=player.created_at,
            photo_filename=player.photo_filename,
        )

    def _tactical_profile(self, db_player: PlayerDB) -> TacticalProfile:
        try:
            return TacticalProfile(
                **(db_player.tactical_profile or _DEFAULT_TACTICAL_PROFILE)
            )
        except TypeError:
            # Stored value is shaped for an older version of Tactical
            # Profile (different fields) — fall back rather than crash;
            # the player just needs re-scoring under the current fields.
            return TacticalProfile(**_DEFAULT_TACTICAL_PROFILE)

    def _to_domain(self, db_player: PlayerDB) -> Player:
        return Player(
            player_id=db_player.player_id,
            first_name_ar=db_player.first_name_ar,
            last_name_ar=db_player.last_name_ar,
            first_name_en=db_player.first_name_en,
            last_name_en=db_player.last_name_en,
            date_of_birth=db_player.date_of_birth,
            sex=db_player.sex,
            team_id=db_player.team_id,
            physical_profile=PhysicalProfile(**db_player.physical_profile),
            technical_profile=TechnicalProfile(**db_player.technical_profile),
            mental_profile=MentalProfile(**db_player.mental_profile),
            match_performance=MatchPerformance(**db_player.match_performance),
            tactical_profile=self._tactical_profile(db_player),
            created_at=db_player.created_at,
            photo_filename=db_player.photo_filename,
        )

    def add_player(self, player: Player) -> None:
        self.db.merge(self._to_db(player))
        self.db.commit()

    def get_player(self, player_id: str) -> Player | None:
        db_player = self.db.get(PlayerDB, player_id)

        if db_player is None:
            return None

        return self._to_domain(db_player)

    def get_all_players(self) -> list[Player]:
        db_players = self.db.query(PlayerDB).all()
        return [self._to_domain(player) for player in db_players]

    def delete_player(self, player_id: str) -> bool:
        db_player = self.db.get(PlayerDB, player_id)

        if db_player is None:
            return False

        self.db.delete(db_player)
        self.db.commit()
        return True

    def update_player(self, player: Player) -> bool:
        existing = self.db.get(PlayerDB, player.player_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(player))
        self.db.commit()
        return True


    def get_players_by_team(
        self,
        team_id: str,
    ) -> list[Player]:
        db_players = (
            self.db.query(PlayerDB)
            .filter(PlayerDB.team_id == team_id)
            .all()
        )

        return [
            self._to_domain(player)
            for player in db_players
        ]
