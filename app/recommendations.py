def generate_recommendations(analysis: dict) -> list[str]:
    recommendations = []

    training_map = {
        "Strength": "Add strength and resistance training.",
        "Shooting": "Practice shooting technique and accuracy.",
        "Finishing": "Practice finishing under pressure and one-on-one situations.",
        "Passing": "Work on passing accuracy and decision speed.",
        "Dribbling": "Practice close ball control and one-on-one dribbling.",
        "Ball Control": "Improve first touch and ball control drills.",
        "Speed": "Add sprint and speed training.",
        "Acceleration": "Practice short explosive acceleration drills.",
        "Agility": "Add agility and change-of-direction drills.",
        "Stamina": "Improve aerobic endurance and repeated sprint ability.",
        "Decision Making": "Use small-sided games to improve decision making.",
        "Concentration": "Practice game-awareness and concentration exercises.",
        "Composure": "Use pressure-based drills to improve composure.",
        "Positioning": "Work on positional awareness and off-the-ball movement.",
        "Vision": "Practice scanning and awareness before receiving the ball.",
    }

    for attribute, score in analysis["top_weaknesses"]:
        if score < 60:
            priority = "HIGH"
        elif score < 70:
            priority = "MEDIUM"
        else:
            priority = "LOW"
        recommendation = training_map.get(
            attribute,
            f"Work on improving {attribute}."
        )

        recommendations.append(f"[{priority}] {recommendation}")
    return recommendations  