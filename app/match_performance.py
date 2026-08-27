from dataclasses import dataclass
@dataclass
class MatchPerformance:
    minutes_played: int
    goals: int
    assists: int
    shots: int
    shots_on_target: int
    passes_attempted: int
    passes_completed: int
    tackles: int
    interceptions: int
    rating: float