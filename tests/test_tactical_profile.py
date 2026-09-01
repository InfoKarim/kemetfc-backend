import pytest

from app.tactical_profile import TACTICAL_CATEGORY_WEIGHTS, TacticalProfile


def test_category_weights_sum_to_one():
    assert sum(TACTICAL_CATEGORY_WEIGHTS.values()) == pytest.approx(1.0)


def test_weighted_score_matches_hand_computed_example():
    # Same relative scores as a real weighted-assessment example (each
    # category's 0-5 score scaled to 0-100 here, since this app's fields
    # are 0-100 like every other profile): Positioning 100, Attacking in
    # possession 100, Attacking off ball 90, Defensive 44, Transitions
    # 100, Decision quality 100, Collective coordination 98, Set-piece 94
    # -> weighted total 89.7.
    profile = TacticalProfile(
        positioning_spatial_intelligence=100.0,
        attacking_contribution_in_possession=100.0,
        attacking_contribution_off_ball=90.0,
        defensive_tactical_contribution=44.0,
        transitions=100.0,
        decision_quality=100.0,
        collective_coordination=98.0,
        set_piece_contribution=94.0,
    )

    assert profile.weighted_score() == pytest.approx(89.7, abs=0.05)


def test_weighted_score_is_100_when_every_category_is_maxed():
    profile = TacticalProfile(**{category: 100.0 for category in TACTICAL_CATEGORY_WEIGHTS})
    assert profile.weighted_score() == pytest.approx(100.0)


def test_weighted_score_is_0_when_every_category_is_0():
    profile = TacticalProfile(**{category: 0.0 for category in TACTICAL_CATEGORY_WEIGHTS})
    assert profile.weighted_score() == 0.0
