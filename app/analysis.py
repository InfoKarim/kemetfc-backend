from app.player import Player


def analyze_player(player: Player) -> dict:
    attributes = {
        "Speed": player.physical_profile.speed,
        "Acceleration": player.physical_profile.acceleration,
        "Agility": player.physical_profile.agility,
        "Stamina": player.physical_profile.stamina,
        "Strength": player.physical_profile.strength,
        "Ball Control": player.technical_profile.ball_control,
        "Dribbling": player.technical_profile.dribbling,
        "Passing": player.technical_profile.passing,
        "Shooting": player.technical_profile.shooting,
        "Finishing": player.technical_profile.finishing,
        "Decision Making": player.mental_profile.decision_making,
        "Concentration": player.mental_profile.concentration,
        "Composure": player.mental_profile.composure,
        "Positioning": player.mental_profile.positioning,
        "Vision": player.mental_profile.vision,
    }
    sorted_attributes = sorted(
    attributes.items(),
        key=lambda item: item[1],
            reverse=True,
        )

    top_strengths = sorted_attributes[:3]
    top_weaknesses = sorted_attributes[-3:][::-1]

    return {
            "top_strengths": top_strengths,
            "top_weaknesses": top_weaknesses,
        }
   # strongest = max(attributes, key=attributes.get)
    #weakest = min(attributes, key=attributes.get)

    #return {
       # "strongest_attribute": strongest,
       # "strongest_score": attributes[strongest],
       # "weakest_attribute": weakest,
       # "weakest_score": attributes[weakest],
    #}
        
      