from app.drill_ranking import calculate_weakness_priority


def test_lower_score_has_higher_priority():
    high_need = calculate_weakness_priority(50)
    medium_need = calculate_weakness_priority(65)
    low_need = calculate_weakness_priority(80)

    assert high_need > medium_need
    assert medium_need > low_need



import pytest


def test_weakness_priority_rejects_invalid_score():
    with pytest.raises(ValueError):
        calculate_weakness_priority(-1)

    with pytest.raises(ValueError):
        calculate_weakness_priority(101)


from app.drill_ranking import calculate_category_match


def test_matching_category_scores_higher():
    matching = calculate_category_match(
        drill_category="Vision",
        weakness="Vision",
    )

    non_matching = calculate_category_match(
        drill_category="Passing",
        weakness="Vision",
    )

    assert matching > non_matching


def test_category_match_is_case_insensitive():
    score = calculate_category_match(
        drill_category="vision",
        weakness="Vision",
    )

    assert score == 100.0


from app.drill_ranking import calculate_drill_score


def test_matching_drill_gets_positive_score():
    score = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
    )

    assert score > 0


def test_matching_drill_ranks_higher_than_non_matching():
    matching_score = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
    )

    non_matching_score = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Passing",
    )

    assert matching_score > non_matching_score


def test_weaker_player_gets_higher_drill_priority():
    weaker_player_score = calculate_drill_score(
        weakness="Vision",
        weakness_score=40,
        drill_category="Vision",
    )

    stronger_player_score = calculate_drill_score(
        weakness="Vision",
        weakness_score=80,
        drill_category="Vision",
    )

    assert weaker_player_score > stronger_player_score


from app.drill_ranking import rank_drills


def test_rank_drills_puts_matching_category_first():
    drills = [
        {"drill_id": "D1", "category": "Passing"},
        {"drill_id": "D2", "category": "Vision"},
        {"drill_id": "D3", "category": "Shooting"},
    ]

    ranked = rank_drills(
        drills=drills,
        weakness="Vision",
        weakness_score=50,
    )

    assert ranked[0]["drill_id"] == "D2"


from app.drill_ranking import calculate_difficulty_match


def test_matching_difficulty_scores_higher():
    matching = calculate_difficulty_match(
        drill_difficulty="beginner",
        player_difficulty="beginner",
    )

    non_matching = calculate_difficulty_match(
        drill_difficulty="advanced",
        player_difficulty="beginner",
    )

    assert matching > non_matching


def test_matching_difficulty_increases_drill_score():
    matching = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_difficulty="beginner",
        player_difficulty="beginner",
    )

    non_matching = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_difficulty="advanced",
        player_difficulty="beginner",
    )

    assert matching > non_matching


def test_rank_drills_prefers_matching_difficulty():
    drills = [
        {
            "drill_id": "D1",
            "category": "Vision",
            "difficulty": "advanced",
        },
        {
            "drill_id": "D2",
            "category": "Vision",
            "difficulty": "beginner",
        },
    ]

    ranked = rank_drills(
        drills=drills,
        weakness="Vision",
        weakness_score=50,
        player_difficulty="beginner",
    )

    assert ranked[0]["drill_id"] == "D2"


from app.drill_ranking import calculate_duration_match


def test_exact_duration_match_scores_higher():
    exact = calculate_duration_match(
        drill_duration=10,
        target_duration=10,
    )

    farther = calculate_duration_match(
        drill_duration=20,
        target_duration=10,
    )

    assert exact > farther


def test_matching_duration_increases_drill_score():
    exact = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_difficulty="beginner",
        player_difficulty="beginner",
        drill_duration=10,
        target_duration=10,
    )

    farther = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_difficulty="beginner",
        player_difficulty="beginner",
        drill_duration=20,
        target_duration=10,
    )

    assert exact > farther


def test_rank_drills_prefers_matching_duration():
    drills = [
        {
            "drill_id": "D1",
            "category": "Vision",
            "difficulty": "beginner",
            "duration_minutes": 20,
        },
        {
            "drill_id": "D2",
            "category": "Vision",
            "difficulty": "beginner",
            "duration_minutes": 10,
        },
    ]

    ranked = rank_drills(
        drills=drills,
        weakness="Vision",
        weakness_score=50,
        player_difficulty="beginner",
        target_duration=10,
    )

    assert ranked[0]["drill_id"] == "D2"


from app.drill_ranking import calculate_equipment_match


def test_available_equipment_scores_higher():
    available = calculate_equipment_match(
        drill_equipment=["ball", "cones"],
        available_equipment=["ball", "cones"],
    )

    missing = calculate_equipment_match(
        drill_equipment=["ball", "cones", "rebounder"],
        available_equipment=["ball", "cones"],
    )

    assert available > missing




def test_available_equipment_increases_drill_score():
    full_equipment = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_equipment=["ball", "cones"],
        available_equipment=["ball", "cones"],
    )

    missing_equipment = calculate_drill_score(
        weakness="Vision",
        weakness_score=50,
        drill_category="Vision",
        drill_equipment=["ball", "cones", "rebounder"],
        available_equipment=["ball", "cones"],
    )

    assert full_equipment > missing_equipment


def test_rank_drills_prefers_available_equipment():
    drills = [
        {
            "drill_id": "D1",
            "category": "Vision",
            "equipment": ["ball", "cones", "rebounder"],
        },
        {
            "drill_id": "D2",
            "category": "Vision",
            "equipment": ["ball", "cones"],
        },
    ]

    ranked = rank_drills(
        drills=drills,
        weakness="Vision",
        weakness_score=50,
        available_equipment=["ball", "cones"],
    )

    assert ranked[0]["drill_id"] == "D2"


def test_full_match_drill_score_is_100():
    score = calculate_drill_score(
        weakness="Vision",
        weakness_score=0,
        drill_category="Vision",
        drill_difficulty="beginner",
        player_difficulty="beginner",
        drill_duration=10,
        target_duration=10,
        drill_equipment=["ball", "cones"],
        available_equipment=["ball", "cones"],
    )

    assert score == 100.0


def test_rank_drills_uses_all_ranking_factors():
    drills = [
        {
            "drill_id": "D1",
            "category": "Vision",
            "difficulty": "advanced",
            "duration_minutes": 20,
            "equipment": ["ball", "cones", "rebounder"],
        },
        {
            "drill_id": "D2",
            "category": "Vision",
            "difficulty": "beginner",
            "duration_minutes": 10,
            "equipment": ["ball", "cones"],
        },
        {
            "drill_id": "D3",
            "category": "Passing",
            "difficulty": "beginner",
            "duration_minutes": 10,
            "equipment": ["ball", "cones"],
        },
    ]

    ranked = rank_drills(
        drills=drills,
        weakness="Vision",
        weakness_score=50,
        player_difficulty="beginner",
        target_duration=10,
        available_equipment=["ball", "cones"],
    )

    assert ranked[0]["drill_id"] == "D2"
