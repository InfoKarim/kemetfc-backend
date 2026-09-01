from dataclasses import dataclass


# Category name -> weight (must sum to 1.0), matching a standard weighted
# tactical-assessment framework: each category is scored 0-100 like every
# other profile field in this app, then weighted to produce one overall
# tactical score.
TACTICAL_CATEGORY_WEIGHTS = {
    "positioning_spatial_intelligence": 0.20,
    "attacking_contribution_in_possession": 0.15,
    "attacking_contribution_off_ball": 0.15,
    "defensive_tactical_contribution": 0.15,
    "transitions": 0.15,
    "decision_quality": 0.10,
    "collective_coordination": 0.05,
    "set_piece_contribution": 0.05,
}


@dataclass
class TacticalProfile:
    positioning_spatial_intelligence: float
    attacking_contribution_in_possession: float
    attacking_contribution_off_ball: float
    defensive_tactical_contribution: float
    transitions: float
    decision_quality: float
    collective_coordination: float
    set_piece_contribution: float

    def weighted_score(self) -> float:
        return round(
            sum(
                getattr(self, category) * weight
                for category, weight in TACTICAL_CATEGORY_WEIGHTS.items()
            ),
            2,
        )
