def calculate_weakness_priority(score: float) -> float:
    if not 0 <= score <= 100:
        raise ValueError(
            "score must be between 0 and 100"
        )

    return 100 - score


def calculate_category_match(
    drill_category: str,
    weakness: str,
) -> float:
    if drill_category.strip().lower() == weakness.strip().lower():
        return 100.0

    return 0.0


def calculate_drill_score(
    weakness: str,
    weakness_score: float,
    drill_category: str,
    drill_difficulty: str | None = None,
    player_difficulty: str | None = None,
    drill_duration: int | None = None,
    target_duration: int | None = None,
    drill_equipment: list[str] | None = None,
    available_equipment: list[str] | None = None,
) -> float:
    weakness_priority = calculate_weakness_priority(
        weakness_score
    )

    category_match = calculate_category_match(
        drill_category,
        weakness,
    )

    difficulty_match = 0.0

    if (
        drill_difficulty is not None
        and player_difficulty is not None
    ):
        difficulty_match = calculate_difficulty_match(
            drill_difficulty,
            player_difficulty,
        )

    duration_match = 0.0

    if (
        drill_duration is not None
        and target_duration is not None
    ):
        duration_match = calculate_duration_match(
            drill_duration,
            target_duration,
        )

    equipment_match = 0.0

    if (
        drill_equipment is not None
        and available_equipment is not None
    ):
        equipment_match = calculate_equipment_match(
            drill_equipment,
            available_equipment,
        )

    return (
        weakness_priority * 0.30
        + category_match * 0.40
        + difficulty_match * 0.15
        + duration_match * 0.10
        + equipment_match * 0.05
    )


def rank_drills(
    drills: list[dict],
    weakness: str,
    weakness_score: float,
    player_difficulty: str | None = None,
    target_duration: int | None = None,
    available_equipment: list[str] | None = None,
) -> list[dict]:
    return sorted(
        drills,
        key=lambda drill: calculate_drill_score(
            weakness=weakness,
            weakness_score=weakness_score,
            drill_category=drill["category"],
            drill_difficulty=drill.get("difficulty"),
            player_difficulty=player_difficulty,
            drill_duration=drill.get("duration_minutes"),
            target_duration=target_duration,
            drill_equipment=drill.get("equipment"),
            available_equipment=available_equipment,
        ),
        reverse=True,
    )



def calculate_difficulty_match(
    drill_difficulty: str,
    player_difficulty: str,
) -> float:
    if drill_difficulty.strip().lower() == player_difficulty.strip().lower():
        return 100.0

    return 0.0


def calculate_duration_match(
    drill_duration: int,
    target_duration: int,
) -> float:
    difference = abs(drill_duration - target_duration)

    return max(0.0, 100.0 - difference)


def calculate_equipment_match(
    drill_equipment: list[str],
    available_equipment: list[str],
) -> float:
    required = {
        item.strip().lower()
        for item in drill_equipment
    }

    available = {
        item.strip().lower()
        for item in available_equipment
    }

    if not required:
        return 100.0

    matched = len(required & available)

    return (matched / len(required)) * 100.0
