from datetime import date

from app.analysis import analyze_player
from app.player import Player
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.match_performance import MatchPerformance

def test_analyze_player_returns_top_strengths_and_weaknesses():
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
    )

    analysis = analyze_player(player)

    assert analysis["top_strengths"] == [
        ("Positioning", 92.0),
        ("Finishing", 91.0),
        ("Speed", 90.0),
    ]

    assert analysis["top_weaknesses"] == [
        ("Strength", 70.0),
        ("Passing", 82.0),
        ("Vision", 84.0),
    ]
