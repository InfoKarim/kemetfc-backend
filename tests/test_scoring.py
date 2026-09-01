from datetime import date

from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.match_performance import MatchPerformance
from app.tactical_profile import TacticalProfile
from app.scoring import calculate_overall_score, get_score_label

def test_get_score_label():
    assert get_score_label(90) == "Elite"
    assert get_score_label(85) == "Elite"
    assert get_score_label(84.9) == "Excellent"
    assert get_score_label(75) == "Excellent"
    assert get_score_label(74.9) == "Good"
    assert get_score_label(65) == "Good"
    assert get_score_label(64.9) == "Developing"
    assert get_score_label(50) == "Developing"
    assert get_score_label(49.9) == "Needs Development"

def test_calculate_overall_score():
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
            speed=80.0,
            acceleration=80.0,
            agility=80.0,
            stamina=80.0,
            strength=80.0,
        ),
        technical_profile=TechnicalProfile(
            ball_control=90.0,
            dribbling=90.0,
            passing=90.0,
            shooting=90.0,
            finishing=90.0,
        ),
        mental_profile=MentalProfile(
            decision_making=70.0,
            concentration=70.0,
            composure=70.0,
            positioning=70.0,
            vision=70.0,
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

    score = calculate_overall_score(player)

    assert score == 81.0
