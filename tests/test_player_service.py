from datetime import date

from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.match_performance import MatchPerformance
from app.tactical_profile import TacticalProfile
from app.weak_foot_profile import WeakFootProfile
from app.db_models import PlayerDB
from app.services.player_service import JerseyNumberConflictError, PlayerService

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base.metadata.create_all(bind=engine)

def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    return PlayerService(db=db)

def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    return PlayerService(db=db)

def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    return PlayerService(db=db)

def test_add_and_get_player():
    service = make_service()

    player = Player(
        player_id="P001",
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        physical_profile=PhysicalProfile(
            height_cm=175.0,
            weight_kg=71.0,
            dominant_foot="left",
            speed=90.0,
            acceleration=89.0,
            agility=88.0,
            stamina=85.0,
            strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=89.0,
            passing=82.0,
            shooting=88.0,
            finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0,
            concentration=87.0,
            composure=90.0,
            positioning=92.0,
            vision=84.0,
            awareness=70.0,
            game_reading=70.0,
            coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90,
            goals=1,
            assists=0,
            shots=4,
            shots_on_target=2,
            passes_attempted=40,
            passes_completed=34,
            tackles=1,
            interceptions=0,
            rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0,
            decision_quality=70.0,
            collective_coordination=68.0,
            set_piece_contribution=65.0,
        ),
            weak_foot_profile=WeakFootProfile(
                weak_foot_usage_pct=20.0,
                weak_foot_passing=60.0,
                weak_foot_receiving=62.0,
                weak_foot_dribbling=58.0,
                weak_foot_finishing=55.0,
            ),
    )

    service.add_player(player)

    result = service.get_player("P001")

    assert result == player

def test_get_unknown_player_returns_none():
    service = make_service()

    result = service.get_player("UNKNOWN")

    assert result is None

def test_get_all_players():
    service = make_service()

    player = Player(
        player_id="P001",
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        physical_profile=PhysicalProfile(
            height_cm=175.0,
            weight_kg=71.0,
            dominant_foot="left",
            speed=90.0,
            acceleration=89.0,
            agility=88.0,
            stamina=85.0,
            strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=89.0,
            passing=82.0,
            shooting=88.0,
            finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0,
            concentration=87.0,
            composure=90.0,
            positioning=92.0,
            vision=84.0,
            awareness=70.0,
            game_reading=70.0,
            coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90,
            goals=1,
            assists=0,
            shots=4,
            shots_on_target=2,
            passes_attempted=40,
            passes_completed=34,
            tackles=1,
            interceptions=0,
            rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0,
            decision_quality=70.0,
            collective_coordination=68.0,
            set_piece_contribution=65.0,
        ),
            weak_foot_profile=WeakFootProfile(
                weak_foot_usage_pct=20.0,
                weak_foot_passing=60.0,
                weak_foot_receiving=62.0,
                weak_foot_dribbling=58.0,
                weak_foot_finishing=55.0,
            ),
    )

    service.add_player(player)

    results = service.get_all_players()

    assert len(results) == 1
    assert results[0] == player

def test_delete_player():
    service = make_service()

    player = Player(
        player_id="P001",
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        physical_profile=PhysicalProfile(
            height_cm=175.0,
            weight_kg=71.0,
            dominant_foot="left",
            speed=90.0,
            acceleration=89.0,
            agility=88.0,
            stamina=85.0,
            strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=89.0,
            passing=82.0,
            shooting=88.0,
            finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0,
            concentration=87.0,
            composure=90.0,
            positioning=92.0,
            vision=84.0,
            awareness=70.0,
            game_reading=70.0,
            coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90,
            goals=1,
            assists=0,
            shots=4,
            shots_on_target=2,
            passes_attempted=40,
            passes_completed=34,
            tackles=1,
            interceptions=0,
            rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0,
            decision_quality=70.0,
            collective_coordination=68.0,
            set_piece_contribution=65.0,
        ),
            weak_foot_profile=WeakFootProfile(
                weak_foot_usage_pct=20.0,
                weak_foot_passing=60.0,
                weak_foot_receiving=62.0,
                weak_foot_dribbling=58.0,
                weak_foot_finishing=55.0,
            ),
    )

    service.add_player(player)

    deleted = service.delete_player("P001")

    assert deleted is True
    assert service.get_player("P001") is None

def test_delete_unknown_player_returns_false():
    service = make_service()

    deleted = service.delete_player("UNKNOWN")

    assert deleted is False

def test_update_player():
    service = make_service()

    player = Player(
        player_id="P001",
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        physical_profile=PhysicalProfile(
            height_cm=175.0,
            weight_kg=71.0,
            dominant_foot="left",
            speed=90.0,
            acceleration=89.0,
            agility=88.0,
            stamina=85.0,
            strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=89.0,
            passing=82.0,
            shooting=88.0,
            finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0,
            concentration=87.0,
            composure=90.0,
            positioning=92.0,
            vision=84.0,
            awareness=70.0,
            game_reading=70.0,
            coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90,
            goals=1,
            assists=0,
            shots=4,
            shots_on_target=2,
            passes_attempted=40,
            passes_completed=34,
            tackles=1,
            interceptions=0,
            rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0,
            decision_quality=70.0,
            collective_coordination=68.0,
            set_piece_contribution=65.0,
        ),
            weak_foot_profile=WeakFootProfile(
                weak_foot_usage_pct=20.0,
                weak_foot_passing=60.0,
                weak_foot_receiving=62.0,
                weak_foot_dribbling=58.0,
                weak_foot_finishing=55.0,
            ),
    )

    service.add_player(player)

    player.first_name_en = "Mo"

    updated = service.update_player(player)

    assert updated is True
    assert service.get_player("P001").first_name_en == "Mo"

def test_update_unknown_player_returns_false():
    service = make_service()

    player = Player(
        player_id="UNKNOWN",
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        physical_profile=PhysicalProfile(
            height_cm=175.0,
            weight_kg=71.0,
            dominant_foot="left",
            speed=90.0,
            acceleration=89.0,
            agility=88.0,
            stamina=85.0,
            strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=89.0,
            passing=82.0,
            shooting=88.0,
            finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0,
            concentration=87.0,
            composure=90.0,
            positioning=92.0,
            vision=84.0,
            awareness=70.0,
            game_reading=70.0,
            coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90,
            goals=1,
            assists=0,
            shots=4,
            shots_on_target=2,
            passes_attempted=40,
            passes_completed=34,
            tackles=1,
            interceptions=0,
            rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0,
            decision_quality=70.0,
            collective_coordination=68.0,
            set_piece_contribution=65.0,
        ),
            weak_foot_profile=WeakFootProfile(
                weak_foot_usage_pct=20.0,
                weak_foot_passing=60.0,
                weak_foot_receiving=62.0,
                weak_foot_dribbling=58.0,
                weak_foot_finishing=55.0,
            ),
    )

    updated = service.update_player(player)

    assert updated is False
    assert service.get_player("UNKNOWN") is None


def test_get_player_defaults_old_shaped_mental_profile():
    service = make_service()

    service.db.add(
        PlayerDB(
            player_id="P900",
            first_name_ar="محمد",
            last_name_ar="صلاح",
            first_name_en="Mohamed",
            last_name_en="Salah",
            date_of_birth=date(1992, 6, 15),
            sex="male",
            physical_profile=PhysicalProfile(
                height_cm=175.0, weight_kg=71.0, dominant_foot="left",
                speed=90.0, acceleration=89.0, agility=88.0,
                stamina=85.0, strength=70.0,
            ).__dict__,
            technical_profile=TechnicalProfile(
                ball_control=90.0, dribbling=89.0, passing=82.0,
                shooting=88.0, finishing=91.0,
            ).__dict__,
            mental_profile={
                "decision_making": 88.0,
                "concentration": 87.0,
                "composure": 90.0,
                "positioning": 92.0,
                "vision": 84.0,
            },
            match_performance=MatchPerformance(
                minutes_played=90, goals=1, assists=0, shots=4,
                shots_on_target=2, passes_attempted=40, passes_completed=34,
                tackles=1, interceptions=0, rating=8.5,
            ).__dict__,
            tactical_profile={
                "attacking": 70.0,
                "defending": 68.0,
                "positioning": 72.0,
                "transitions": 69.0,
            },
        )
    )
    service.db.commit()

    result = service.get_player("P900")

    assert result.mental_profile.decision_making == 88.0
    assert result.mental_profile.awareness == 70.0
    assert result.mental_profile.game_reading == 70.0
    assert result.mental_profile.coachability == 70.0
    assert result.tactical_profile.positioning_spatial_intelligence == 70.0


def _make_player(player_id, team_id=None, jersey_number=None):
    return Player(
        player_id=player_id,
        first_name_ar="محمد",
        last_name_ar="صلاح",
        first_name_en="Mohamed",
        last_name_en="Salah",
        date_of_birth=date(1992, 6, 15),
        sex="male",
        team_id=team_id,
        jersey_number=jersey_number,
        physical_profile=PhysicalProfile(
            height_cm=175.0, weight_kg=71.0, dominant_foot="left",
            speed=90.0, acceleration=89.0, agility=88.0,
            stamina=85.0, strength=70.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0, dribbling=89.0, passing=82.0,
            shooting=88.0, finishing=91.0,
        ),
        mental_profile=MentalProfile(
            decision_making=88.0, concentration=87.0, composure=90.0,
            positioning=92.0, vision=84.0, awareness=70.0,
            game_reading=70.0, coachability=70.0,
        ),
        match_performance=MatchPerformance(
            minutes_played=90, goals=1, assists=0, shots=4,
            shots_on_target=2, passes_attempted=40, passes_completed=34,
            tackles=1, interceptions=0, rating=8.5,
        ),
        tactical_profile=TacticalProfile(
            positioning_spatial_intelligence=70.0,
            attacking_contribution_in_possession=68.0,
            attacking_contribution_off_ball=72.0,
            defensive_tactical_contribution=69.0,
            transitions=71.0, decision_quality=70.0,
            collective_coordination=68.0, set_piece_contribution=65.0,
        ),
        weak_foot_profile=WeakFootProfile(
            weak_foot_usage_pct=20.0, weak_foot_passing=60.0,
            weak_foot_receiving=62.0, weak_foot_dribbling=58.0,
            weak_foot_finishing=55.0,
        ),
    )


def test_add_player_allows_same_jersey_number_on_different_teams():
    service = make_service()

    service.add_player(_make_player("P700", team_id="TEAM_A", jersey_number=10))
    service.add_player(_make_player("P701", team_id="TEAM_B", jersey_number=10))

    assert service.get_player("P700").jersey_number == 10
    assert service.get_player("P701").jersey_number == 10


def test_add_player_rejects_duplicate_jersey_number_on_same_team():
    service = make_service()
    service.add_player(_make_player("P702", team_id="TEAM_A", jersey_number=10))

    with pytest.raises(JerseyNumberConflictError):
        service.add_player(_make_player("P703", team_id="TEAM_A", jersey_number=10))

    assert service.get_player("P703") is None


def test_add_player_skips_jersey_check_without_team():
    service = make_service()
    service.add_player(_make_player("P704", team_id=None, jersey_number=10))

    # No team on either player — the same number is never a conflict.
    service.add_player(_make_player("P705", team_id=None, jersey_number=10))

    assert service.get_player("P704").jersey_number == 10
    assert service.get_player("P705").jersey_number == 10


def test_update_player_rejects_duplicate_jersey_number_on_same_team():
    service = make_service()
    service.add_player(_make_player("P706", team_id="TEAM_A", jersey_number=10))
    service.add_player(_make_player("P707", team_id="TEAM_A", jersey_number=11))

    conflicting_update = _make_player("P707", team_id="TEAM_A", jersey_number=10)

    with pytest.raises(JerseyNumberConflictError):
        service.update_player(conflicting_update)

    assert service.get_player("P707").jersey_number == 11


def test_update_player_keeps_own_jersey_number():
    service = make_service()
    service.add_player(_make_player("P708", team_id="TEAM_A", jersey_number=10))

    unchanged = _make_player("P708", team_id="TEAM_A", jersey_number=10)
    updated = service.update_player(unchanged)

    assert updated is True
    assert service.get_player("P708").jersey_number == 10


def test_find_by_jersey_number():
    service = make_service()
    service.add_player(_make_player("P709", team_id="TEAM_A", jersey_number=9))
    service.add_player(_make_player("P710", team_id="TEAM_B", jersey_number=9))
    service.add_player(_make_player("P711", team_id="TEAM_A", jersey_number=4))

    matches = service.find_by_jersey_number(9)

    assert {player.player_id for player in matches} == {"P709", "P710"}


