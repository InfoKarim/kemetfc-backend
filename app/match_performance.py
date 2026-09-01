from dataclasses import dataclass

MATCH_PERFORMANCE_FIELD_HINTS = {
    "minutes_played": "<integer 0-90>  # Minutes played in a single representative match",
    "goals": "<integer 0-5>  # Goals scored in that match",
    "assists": "<integer 0-5>  # Assists in that match",
    "shots": "<integer 0-15>  # Total shots taken",
    "shots_on_target": "<integer, must not exceed shots>  # Shots on target",
    "passes_attempted": "<integer 5-100>  # Passes attempted",
    "passes_completed": "<integer, must not exceed passes_attempted>  # Passes completed",
    "tackles": "<integer 0-10>  # Tackles",
    "interceptions": "<integer 0-10>  # Interceptions",
    "rating": "<number 1.0-10.0, one decimal place>  # Overall match rating",
}


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