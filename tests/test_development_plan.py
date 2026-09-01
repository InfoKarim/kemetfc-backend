from datetime import date

from app.development_plan import create_development_plan
from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.match_performance import MatchPerformance
from app.tactical_profile import TacticalProfile

def make_player():
    return Player(
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
            game_understanding=70.0,
            defensive_positioning=68.0,
            off_ball_movement=72.0,
            pressing_intensity=69.0,
        ),
    )

def test_create_development_plan_basic_info():
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
            game_understanding=70.0,
            defensive_positioning=68.0,
            off_ball_movement=72.0,
            pressing_intensity=69.0,
        ),
    )

    plan = create_development_plan(player)

    assert plan["player_id"] == "P001"
    assert plan["player_name"] == "Mohamed Salah"
    assert plan["overall_score"] == 86.98
    assert plan["player_level"] == "Elite"
    assert "top_strengths" in plan
    assert "top_weaknesses" in plan
    assert "training_recommendations" in plan
    assert "development_goals" in plan

def test_age_before_birthday_this_year():
    player = make_player()
    today = date.today()

    player.date_of_birth = date(
        today.year - 20,
        12,
        31,
    )

    plan = create_development_plan(player)

    assert plan["age"] == 19

