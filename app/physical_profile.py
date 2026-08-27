from dataclasses import dataclass


@dataclass
class PhysicalProfile:
    height_cm: float
    weight_kg: float
    dominant_foot: str
    speed: float
    acceleration: float
    agility: float
    stamina: float
    strength: float