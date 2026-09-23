from dataclasses import asdict

from sqlalchemy.orm import Session

from app.development_snapshot import calculate_player_age
from app.drill_recommendations import build_drill_recommendations
from app.services.development_trend_service import DevelopmentTrendService
from app.services.drill_service import DrillService
from app.services.player_service import PlayerService
from app.trend_drill_matching import identify_training_needs


class PlayerNotFoundError(ValueError):
    def __init__(self, player_id: str):
        self.player_id = player_id
        super().__init__(f"Player {player_id} not found")


class TrendDrillRecommendationService:
    """Step 9: Player Assessment -> (Step 2 PlayerMetricDB) ->
    Development Trend Analysis (Step 3) -> Training Need
    (app/trend_drill_matching.py) -> Drill Library -> Rank Recommended
    Drills (the existing, unmodified app/drill_ranking.py) -> the
    response this service returns, ready to become an Individual
    Training Plan the same way POST /analyses/{id}/training-plans
    already does today for a manual analysis.

    Rule-based only, per the product requirement — no ML model chooses
    or re-ranks drills here. The one seam intentionally left for a
    future ML-based ranking step: swap what this service passes as
    `weaknesses` into build_drill_recommendations() without touching
    build_drill_recommendations() or rank_drills() themselves.
    """

    def __init__(self, db: Session):
        self.db = db
        self._trends = DevelopmentTrendService(db)
        self._players = PlayerService(db)
        self._drills = DrillService(db)

    def recommend(
        self,
        player_id: str,
        player_difficulty: str | None = None,
        target_duration: int | None = None,
        available_equipment: list[str] | None = None,
        limit_per_weakness: int = 3,
    ) -> dict:
        player = self._players.get_player(player_id)
        if player is None:
            raise PlayerNotFoundError(player_id)

        trends = self._trends.get_all_trends(player_id)
        rankable_weaknesses, unranked_training_needs = identify_training_needs(trends)

        if not rankable_weaknesses:
            return {
                "player_id": player_id,
                "recommendations": [],
                "unranked_training_needs": [asdict(need) for need in unranked_training_needs],
            }

        age = calculate_player_age(player.date_of_birth)
        drills = self._drills.get_all_drills()

        recommendations = build_drill_recommendations(
            weaknesses=[
                {"attribute": weakness["attribute"], "score": weakness["score"]}
                for weakness in rankable_weaknesses
            ],
            drills=drills,
            age=age,
            player_difficulty=player_difficulty,
            target_duration=target_duration,
            available_equipment=available_equipment,
            limit_per_weakness=limit_per_weakness,
        )

        # Reattach which real metrics drove each weakness group — lost
        # by build_drill_recommendations' existing {attribute, score}
        # contract, which this module deliberately did not change.
        source_metrics_by_attribute = {
            weakness["attribute"]: weakness["source_metrics"] for weakness in rankable_weaknesses
        }
        for recommendation in recommendations:
            recommendation["source_metrics"] = source_metrics_by_attribute.get(
                recommendation["weakness"], []
            )

        return {
            "player_id": player_id,
            "recommendations": recommendations,
            "unranked_training_needs": [asdict(need) for need in unranked_training_needs],
        }
