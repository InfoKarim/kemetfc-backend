from app.player import Player
from datetime import date
from app.analysis import analyze_player
from app.recommendations import generate_recommendations
from app.scoring import calculate_overall_score, get_score_label
from app.development_goals import generate_development_goals

def create_development_plan(player: Player) -> dict:
    plan = {}
    plan["player_name"] = f"{player.first_name_en} {player.last_name_en}"
    plan["player_id"] = player.player_id
    today = date.today()

    age = today.year - player.date_of_birth.year
    if (today.month, today.day) < (
        player.date_of_birth.month,
        player.date_of_birth.day,
    ):
        age -= 1
    plan["age"] = age
    analysis = analyze_player(player)
    plan["top_strengths"] = analysis["top_strengths"]
    plan["top_weaknesses"] = analysis["top_weaknesses"]
    plan["training_recommendations"] = generate_recommendations(analysis)
    plan["overall_score"] = calculate_overall_score(player)
    plan["player_level"] = get_score_label(plan["overall_score"])
    plan["development_goals"] = generate_development_goals(player)

    return plan
    