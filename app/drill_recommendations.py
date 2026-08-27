from dataclasses import asdict

from app.data_models import DrillData
from app.drill_ranking import rank_drills


def _normalize_weakness(weakness) -> tuple[str, float]:
    if isinstance(weakness, dict):
        attribute = weakness.get("attribute")
        score = weakness.get("score")
    elif isinstance(weakness, (list, tuple)) and len(weakness) == 2:
        attribute, score = weakness
    else:
        raise ValueError(
            "each weakness must contain an attribute and score"
        )

    if not isinstance(attribute, str) or not attribute.strip():
        raise ValueError("weakness attribute cannot be empty")

    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise ValueError(
            "weakness score must be between 0 and 100"
        )

    return attribute, float(score)


def build_drill_recommendations(
    weaknesses: list,
    drills: list[DrillData],
    age: int,
    player_difficulty: str | None = None,
    target_duration: int | None = None,
    available_equipment: list[str] | None = None,
    limit_per_weakness: int = 3,
) -> list[dict]:
    eligible_drills = [
        asdict(drill)
        for drill in drills
        if drill.active and drill.min_age <= age <= drill.max_age
    ]

    recommendations = []

    for weakness in weaknesses:
        attribute, score = _normalize_weakness(weakness)

        ranked = rank_drills(
            drills=eligible_drills,
            weakness=attribute,
            weakness_score=score,
            player_difficulty=player_difficulty,
            target_duration=target_duration,
            available_equipment=available_equipment,
        )

        recommendations.append(
            {
                "weakness": attribute,
                "weakness_score": score,
                "drills": ranked[:limit_per_weakness],
            }
        )

    return recommendations
