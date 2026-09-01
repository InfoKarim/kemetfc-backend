from dataclasses import dataclass

MENTAL_FIELD_HINTS = {
    "decision_making": "<0-100 integer>  # Decision making",
    "concentration": "<0-100 integer>  # Concentration",
    "composure": "<0-100 integer>  # Composure",
    "positioning": "<0-100 integer>  # Positioning",
    "vision": "<0-100 integer>  # Vision",
    "awareness": "<0-100 integer>  # Awareness",
    "game_reading": "<0-100 integer>  # Ability to follow the gameplay",
    "coachability": "<0-100 integer>  # Ability to follow directions",
}


@dataclass
class MentalProfile:
    decision_making: float
    concentration: float
    composure: float
    positioning: float
    vision: float
    awareness: float
    game_reading: float
    coachability: float
