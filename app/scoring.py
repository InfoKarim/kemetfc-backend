from app.player import Player


def calculate_overall_score(player: Player) -> float:
    physical = player.physical_profile
    technical = player.technical_profile
    mental = player.mental_profile
    tactical = player.tactical_profile

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
        + mental.awareness
        + mental.game_reading
        + mental.coachability
    ) / 8

    tactical_score = tactical.weighted_score()

    # Equal quarters across all four judged profiles. Physical is
    # deliberately not weighted above the others for youth players: raw
    # physical dominance at this age is often a relative-age/maturation
    # effect rather than footballing ability, and shouldn't outweigh
    # technical, mental, or tactical development in the headline score.
    overall_score = (
        physical_score * 0.25
        + technical_score * 0.25
        + mental_score * 0.25
        + tactical_score * 0.25
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
