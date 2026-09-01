from dataclasses import dataclass

MENTAL_FIELD_HINTS = {
    "decision_making": "<0-100 integer>  # Decision making",
    "concentration": "<0-100 integer>  # Concentration",
    "composure": "<0-100 integer>  # Composure",
    "positioning": "<0-100 integer>  # Positioning",
    "vision": "<0-100 integer>  # Vision",
}


@dataclass
class MentalProfile:
    decision_making: float
    concentration: float
    composure: float
    positioning: float
    vision: float