from dataclasses import dataclass


@dataclass
class TechnicalProfile:
    ball_control: float
    dribbling: float
    passing: float
    shooting: float
    finishing: float