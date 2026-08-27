from app.player import Player


def calculate_overall_score(player: Player) -> float:
    physical = player.physical_profile
    technical = player.technical_profile
    mental = player.mental_profile

    physical_score = (
        physical.speed
        + physical.acceleration
        + physical.agility
        + physical.stamina
        + physical.strength
    ) / 5

    technical_score = (
        technical.ball_control
        + technical.dribbling
        + technical.passing
        + technical.shooting
        + technical.finishing
    ) / 5

    mental_score = (
        mental.decision_making
        + mental.concentration
        + mental.composure
        + mental.positioning
        + mental.vision
    ) / 5

    overall_score = (
        physical_score * 0.30
        + technical_score * 0.40
        + mental_score * 0.30
    )
    return round(overall_score, 2)
def get_score_label(score: float) -> str:
    if score >= 85:
        return "Elite"
    elif score >= 75:
        return "Excellent"
    elif score >= 65:
        return "Good"
    elif score >= 50:
        return "Developing"
    else:
        return "Needs Development"