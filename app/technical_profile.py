from dataclasses import dataclass

TECHNICAL_FIELD_HINTS = {
    "ball_control": "<0-100 integer>  # Ball control",
    "dribbling": "<0-100 integer>  # Dribbling",
    "passing": "<0-100 integer>  # Passing",
    "shooting": "<0-100 integer>  # Shooting",
    "finishing": "<0-100 integer>  # Finishing",
}


@dataclass
class TechnicalProfile:
    ball_control: float
    dribbling: float
    passing: float
    shooting: float
    finishing: float