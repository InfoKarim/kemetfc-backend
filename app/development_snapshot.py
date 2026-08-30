from dataclasses import asdict
from datetime import date

from app.data_models import AIAnalysisRecord, DrillData, TrainingPlanData
from app.drill_recommendations import build_drill_recommendations
from app.player import Player


def calculate_player_age(birth_date: date, today: date | None = None) -> int:
    current_date = today or date.today()
    age = current_date.year - birth_date.year

    if (current_date.month, current_date.day) < (
        birth_date.month,
        birth_date.day,
    ):
        age -= 1

    return age


def _attribute_name(item) -> str | None:
    if isinstance(item, dict):
        value = item.get("attribute")
    elif isinstance(item, (list, tuple)) and item:
        value = item[0]
    else:
        return None

    if not isinstance(value, str) or not value.strip():
        return None

    return value.strip()


def _coach_summary(analysis: AIAnalysisRecord | None) -> str:
    if analysis is None:
        return "Complete a player analysis to generate a coach summary."

    strengths = [
        name
        for item in analysis.strengths
        if (name := _attribute_name(item)) is not None
    ]
    weaknesses = [
        name
        for item in analysis.weaknesses
        if (name := _attribute_name(item)) is not None
    ]

    parts = []

    if strengths:
        parts.append(f"Key strengths: {', '.join(strengths[:3])}.")

    if weaknesses:
        parts.append(
            f"Development focus: {', '.join(weaknesses[:3])}."
        )

    return " ".join(parts) or "The latest analysis is ready for coach review."


def build_development_snapshot(
    player: Player,
    analyses: list[AIAnalysisRecord],
    drills: list[DrillData],
    training_plans: list[TrainingPlanData],
    player_difficulty: str | None = None,
    target_duration: int | None = None,
    available_equipment: list[str] | None = None,
) -> dict:
    age = calculate_player_age(player.date_of_birth)
    latest_analysis = max(
        analyses,
        key=lambda analysis: analysis.created_at,
        default=None,
    )
    latest_plan = max(
        training_plans,
        key=lambda plan: plan.created_at,
        default=None,
    )

    recommendations = []

    if latest_analysis is not None:
        recommendations = build_drill_recommendations(
            weaknesses=latest_analysis.weaknesses,
            drills=drills,
            age=age,
            player_difficulty=player_difficulty,
            target_duration=target_duration,
            available_equipment=available_equipment,
        )

    ability_profile = [
        {"label": "Speed", "score": player.physical_profile.speed},
        {"label": "Stamina", "score": player.physical_profile.stamina},
        {"label": "Passing", "score": player.technical_profile.passing},
        {"label": "Dribbling", "score": player.technical_profile.dribbling},
        {"label": "Ball Control", "score": player.technical_profile.ball_control},
        {"label": "Game IQ", "score": player.mental_profile.decision_making},
    ]

    development_focus = []

    if latest_analysis is not None:
        development_focus = [
            name
            for item in latest_analysis.weaknesses
            if (name := _attribute_name(item)) is not None
        ][:3]

    return {
        "player": {
            "player_id": player.player_id,
            "name": f"{player.first_name_en} {player.last_name_en}",
            "age": age,
            "sex": player.sex,
            "team_id": player.team_id,
            "height_cm": player.physical_profile.height_cm,
            "preferred_foot": player.physical_profile.dominant_foot,
            "photo_filename": player.photo_filename,
        },
        "ability_profile": ability_profile,
        "latest_analysis": (
            asdict(latest_analysis)
            if latest_analysis is not None
            else None
        ),
        "coach_summary": _coach_summary(latest_analysis),
        "development_focus": development_focus,
        "drill_recommendations": recommendations,
        "latest_training_plan": (
            asdict(latest_plan)
            if latest_plan is not None
            else None
        ),
    }
