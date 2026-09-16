from sqlalchemy.orm import Session

from app.db_models import CoachMessageDB, PlayerAssessmentDB
from app.services.auth_service import utcnow

# The four future KEMET Player Development Score pillars. Only "physical"
# and "technical" have a real assessment type behind them today (Yo-Yo
# Kids, Ball Mastery) — "tactical" and "coach" are listed so Assessment
# Coverage honestly reports what's still missing instead of silently
# only counting what happens to exist.
PILLARS = ("physical", "technical", "tactical", "coach")

BALL_MASTERY_SKILL_LABELS = {
    "sole_rolls": "Sole Rolls",
    "inside_outside_cuts": "Inside/Outside Cuts",
    "l_turn": "L-Turn",
    "drag_back": "Drag-Back",
}

STRENGTH_RATING_THRESHOLD = 4
PRIORITY_RATING_THRESHOLD = 2


def get_assessment_coverage(db: Session, player_id: str) -> dict:
    rows = (
        db.query(PlayerAssessmentDB.pillar)
        .filter(PlayerAssessmentDB.player_id == player_id)
        .distinct()
        .all()
    )
    covered = sorted({row[0] for row in rows})
    return {
        "covered_pillars": covered,
        "pillars": list(PILLARS),
        "coverage_percent": round(100 * len(covered) / len(PILLARS)),
    }


def _latest_assessment(db: Session, player_id: str, test_type: str):
    return (
        db.query(PlayerAssessmentDB)
        .filter(
            PlayerAssessmentDB.player_id == player_id,
            PlayerAssessmentDB.test_type == test_type,
        )
        .order_by(PlayerAssessmentDB.test_date.desc(), PlayerAssessmentDB.created_at.desc())
        .limit(2)
        .all()
    )


def get_progress(db: Session, player_id: str) -> list[dict]:
    """Latest-vs-previous for each test type that has at least two
    results. Every value here is a real, directly-recorded measurement
    (Yo-Yo distance in meters, or the mean of a coach's own 1-5 Ball
    Mastery ratings) — never a normalized or weighted score.
    """
    progress = []

    for test_type, pillar, value_fn, label in (
        (
            "yoyo_kids",
            "physical",
            lambda a: a.raw_data.get("total_distance_m"),
            "Yo-Yo Kids Test — distance covered (m)",
        ),
        (
            "ball_mastery",
            "technical",
            lambda a: round(
                sum(a.raw_data.get(skill, 0) for skill in BALL_MASTERY_SKILL_LABELS)
                / len(BALL_MASTERY_SKILL_LABELS),
                2,
            ),
            "Ball Mastery — average of 4 skill ratings (1-5 scale)",
        ),
    ):
        rows = _latest_assessment(db, player_id, test_type)
        if len(rows) < 2:
            continue
        latest, previous = rows[0], rows[1]
        latest_value = value_fn(latest)
        previous_value = value_fn(previous)
        progress.append({
            "test_type": test_type,
            "pillar": pillar,
            "label": label,
            "latest_date": latest.test_date.isoformat(),
            "latest_value": latest_value,
            "previous_date": previous.test_date.isoformat(),
            "previous_value": previous_value,
            "change": round(latest_value - previous_value, 2),
        })

    return progress


def get_strengths_and_priorities(
    db: Session,
    player_id: str,
    max_items: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Derive strengths/development priorities from real, most-recent
    coach observations only — never an invented judgment. Currently
    sourced from:

    - Ball Mastery: any skill the coach rated >= STRENGTH_RATING_THRESHOLD
      is a strength; <= PRIORITY_RATING_THRESHOLD is a priority.
    - Yo-Yo Kids: an improvement over the previous test is a strength.
      It never produces a priority — there is no validated pediatric
      distance threshold to call a result "weak" against.
    """
    strengths: list[dict] = []
    priorities: list[dict] = []

    ball_mastery_rows = _latest_assessment(db, player_id, "ball_mastery")
    if ball_mastery_rows:
        latest = ball_mastery_rows[0]
        for skill, label in BALL_MASTERY_SKILL_LABELS.items():
            rating = latest.raw_data.get(skill)
            if not isinstance(rating, int):
                continue
            if rating >= STRENGTH_RATING_THRESHOLD:
                strengths.append({
                    "title": label,
                    "detail": (
                        f"Rated {rating}/5 by the coach in the most "
                        "recent Ball Mastery assessment."
                    ),
                    "source_test_type": "ball_mastery",
                    "rating": rating,
                })
            elif rating <= PRIORITY_RATING_THRESHOLD:
                priorities.append({
                    "title": label,
                    "detail": (
                        f"Rated {rating}/5 by the coach in the most "
                        "recent Ball Mastery assessment — an area to "
                        "keep developing."
                    ),
                    "source_test_type": "ball_mastery",
                    "rating": rating,
                })

    yoyo_rows = _latest_assessment(db, player_id, "yoyo_kids")
    if len(yoyo_rows) >= 2:
        latest, previous = yoyo_rows[0], yoyo_rows[1]
        latest_distance = latest.raw_data.get("total_distance_m")
        previous_distance = previous.raw_data.get("total_distance_m")
        if (
            isinstance(latest_distance, (int, float))
            and isinstance(previous_distance, (int, float))
            and latest_distance > previous_distance
        ):
            strengths.append({
                "title": "Endurance",
                "detail": (
                    f"Distance improved from {previous_distance:g} m to "
                    f"{latest_distance:g} m since the last Yo-Yo Kids test."
                ),
                "source_test_type": "yoyo_kids",
                "rating": None,
            })

    strengths.sort(key=lambda item: (item["rating"] is None, -(item["rating"] or 0)))
    priorities.sort(key=lambda item: item["rating"])

    return strengths[:max_items], priorities[:max_items]


def get_coach_message(db: Session, player_id: str) -> dict:
    row = db.get(CoachMessageDB, player_id)
    if row is None:
        return {"message": None, "next_focus": [], "updated_at": None}
    return {
        "message": row.message,
        "next_focus": row.next_focus or [],
        "updated_at": row.updated_at.isoformat(),
    }


def set_coach_message(
    db: Session,
    player_id: str,
    message: str | None,
    next_focus: list[str],
    updated_by_user_id: str | None,
) -> dict:
    row = db.get(CoachMessageDB, player_id)
    if row is None:
        row = CoachMessageDB(player_id=player_id)
        db.add(row)
    row.message = message
    row.next_focus = next_focus
    row.updated_by_user_id = updated_by_user_id
    row.updated_at = utcnow()
    db.commit()
    return get_coach_message(db, player_id)


def build_development_report(db: Session, player_id: str) -> dict:
    strengths, priorities = get_strengths_and_priorities(db, player_id)
    return {
        "player_id": player_id,
        "coverage": get_assessment_coverage(db, player_id),
        "progress": get_progress(db, player_id),
        "strengths": strengths,
        "priorities": priorities,
        "coach_message": get_coach_message(db, player_id),
    }
