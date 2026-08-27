from app.data_models import DrillData
from app.drill_recommendations import build_drill_recommendations


def make_drill(
    drill_id: str,
    category: str,
) -> DrillData:
    return DrillData(
        drill_id=drill_id,
        name=f"{category} Drill",
        category=category,
        description=f"Practice {category}.",
        min_age=7,
        max_age=13,
        difficulty="beginner",
        duration_minutes=10,
        equipment=["ball", "cones"],
        video_url=f"/drills/{drill_id}.mp4",
        active=True,
    )


def test_matching_drill_is_recommended_first():
    weaknesses = [
        {
            "attribute": "Vision",
            "score": 50,
        }
    ]

    drills = [
        make_drill("D_PASSING", "Passing"),
        make_drill("D_VISION", "Vision"),
    ]

    recommendations = build_drill_recommendations(
        weaknesses=weaknesses,
        drills=drills,
        age=10,
        player_difficulty="beginner",
        target_duration=10,
        available_equipment=["ball", "cones"],
    )

    assert recommendations[0]["weakness"] == "Vision"
    assert recommendations[0]["drills"][0]["drill_id"] == "D_VISION"
