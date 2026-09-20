from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import PlayerDB
from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.tactical_profile import TacticalProfile
from app.weak_foot_profile import WeakFootProfile
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

# Players created before Mental Profile gained Awareness, Game reading, and
# Coachability have a stored value with only the original 5 fields — default
# the new ones to a neutral midpoint rather than crashing until re-scored.
_DEFAULT_MENTAL_PROFILE = {
    "decision_making": 70.0,
    "concentration": 70.0,
    "composure": 70.0,
    "positioning": 70.0,
    "vision": 70.0,
    "awareness": 70.0,
    "game_reading": 70.0,
    "coachability": 70.0,
}

# Players created before the Weak Foot Profile field existed have no stored
# value — default to a neutral midpoint rather than crashing until re-scored.
_DEFAULT_WEAK_FOOT_PROFILE = {
    "weak_foot_usage_pct": 20.0,
    "weak_foot_passing": 60.0,
    "weak_foot_receiving": 60.0,
    "weak_foot_dribbling": 60.0,
    "weak_foot_finishing": 60.0,
}


class JerseyNumberConflictError(ValueError):
    """Raised when a player's jersey number is already worn by another
    player on the same team. Not raised for players without a team_id —
    shirt numbers are only meaningful (and only checked) within a squad."""

    def __init__(self, jersey_number: int, team_id: str, other_player_id: str):
        self.jersey_number = jersey_number
        self.team_id = team_id
        self.other_player_id = other_player_id
        super().__init__(
            f"Jersey number {jersey_number} is already worn by another "
            f"player ({other_player_id}) on this team."
        )


class PlayerDeletionBlockedError(ValueError):
    """Raised when a player still has linked rows (videos, analyses,
    training plans, matches, guardian links, etc.) that reference it by
    foreign key. Deleting the player in that state would otherwise hit an
    unhandled IntegrityError and surface as a raw 500 with no explanation
    of what to remove first."""

    def __init__(self, player_id: str):
        self.player_id = player_id
        super().__init__(
            f"Cannot delete player {player_id}: it still has linked "
            "videos, assessments, training plans, or other records. "
            "Remove those first, then delete the player."
        )


class PlayerService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _check_jersey_number_conflict(self, player: Player) -> None:
        if player.jersey_number is None or player.team_id is None:
            return

        conflict = (
            self.db.query(PlayerDB)
            .filter(
                PlayerDB.team_id == player.team_id,
                PlayerDB.jersey_number == player.jersey_number,
                PlayerDB.player_id != player.player_id,
            )
            .first()
        )

        if conflict is not None:
            raise JerseyNumberConflictError(
                player.jersey_number, player.team_id, conflict.player_id
            )

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
            jersey_number=player.jersey_number,
            physical_profile=player.physical_profile.__dict__,
            technical_profile=player.technical_profile.__dict__,
            mental_profile=player.mental_profile.__dict__,
            match_performance=player.match_performance.__dict__,
            tactical_profile=player.tactical_profile.__dict__,
            weak_foot_profile=player.weak_foot_profile.__dict__,
            created_at=player.created_at,
            photo_filename=player.photo_filename,
            source=player.source,
            created_by_user_id=player.created_by_user_id,
        )

    def _mental_profile(self, db_player: PlayerDB) -> MentalProfile:
        return MentalProfile(
            **{**_DEFAULT_MENTAL_PROFILE, **(db_player.mental_profile or {})}
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

    def _weak_foot_profile(self, db_player: PlayerDB) -> WeakFootProfile:
        try:
            return WeakFootProfile(
                **(db_player.weak_foot_profile or _DEFAULT_WEAK_FOOT_PROFILE)
            )
        except TypeError:
            return WeakFootProfile(**_DEFAULT_WEAK_FOOT_PROFILE)

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
            jersey_number=db_player.jersey_number,
            physical_profile=PhysicalProfile(**db_player.physical_profile),
            technical_profile=TechnicalProfile(**db_player.technical_profile),
            mental_profile=self._mental_profile(db_player),
            match_performance=MatchPerformance(**db_player.match_performance),
            tactical_profile=self._tactical_profile(db_player),
            weak_foot_profile=self._weak_foot_profile(db_player),
            created_at=db_player.created_at,
            photo_filename=db_player.photo_filename,
            source=db_player.source,
            created_by_user_id=db_player.created_by_user_id,
        )

    def add_player(self, player: Player) -> None:
        self._check_jersey_number_conflict(player)
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

    def find_by_name_and_dob(
        self,
        first_name_en: str,
        last_name_en: str,
        date_of_birth,
    ) -> list[Player]:
        """Strong-identifier duplicate check (exact name + DOB match) — used
        before creating a player from a registration so staff aren't
        silently offered a second record for a child already in the
        system. Deliberately NOT fuzzy: a near-miss name is not treated as
        a match."""
        db_players = (
            self.db.query(PlayerDB)
            .filter(
                PlayerDB.first_name_en.ilike(first_name_en),
                PlayerDB.last_name_en.ilike(last_name_en),
                PlayerDB.date_of_birth == date_of_birth,
            )
            .all()
        )
        return [self._to_domain(player) for player in db_players]

    def delete_player(self, player_id: str) -> bool:
        db_player = self.db.get(PlayerDB, player_id)

        if db_player is None:
            return False

        self.db.delete(db_player)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise PlayerDeletionBlockedError(player_id) from exc
        return True

    def update_player(self, player: Player) -> bool:
        existing = self.db.get(PlayerDB, player.player_id)

        if existing is None:
            return False

        self._check_jersey_number_conflict(player)
        self.db.merge(self._to_db(player))
        self.db.commit()
        return True

    def find_by_jersey_number(self, jersey_number: int) -> list[Player]:
        """All players wearing this number, across every team — shirt
        numbers are only unique within a team, so a coach picking a player
        by number may need to disambiguate between several matches."""
        db_players = (
            self.db.query(PlayerDB)
            .filter(PlayerDB.jersey_number == jersey_number)
            .all()
        )
        return [self._to_domain(player) for player in db_players]


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
