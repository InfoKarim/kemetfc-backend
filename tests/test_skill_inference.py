from app.services import skill_inference
from app.services.tracking_features import build_feature_set
from app.services.tracking_quality import evaluate_session_quality


def sample(t, player=None, ball=None, status="locked"):
    return {
        "t_seconds": t,
        "player_center": player,
        "player_confidence": 0.9 if player else None,
        "ball_center": ball,
        "ball_confidence": 0.9 if ball else None,
        "pose_keypoints": None,
        "tracking_status": status,
    }


def _rich_samples():
    samples = []
    for i in range(40):
        t = float(i)
        px = 0.1 + 0.01 * i
        bx = px + 0.02
        samples.append(sample(t, player=[px, 0.5], ball=[bx, 0.5]))
    return samples


def test_infer_skills_returns_nothing_when_ball_relationship_insufficient():
    samples = [sample(0.0, player=[0.5, 0.5]), sample(1.0, player=[0.6, 0.5])]
    feature_set = build_feature_set(samples)
    quality = evaluate_session_quality(samples)
    results = skill_inference.infer_skills(feature_set, quality)
    skills = {r["skill"] for r in results}
    # No paired player+ball samples anywhere -> no ball_control result.
    assert "ball_control" not in skills


def test_infer_skills_produces_explainable_ball_control_result():
    samples = _rich_samples()
    feature_set = build_feature_set(samples)
    quality = evaluate_session_quality(samples)
    results = skill_inference.infer_skills(feature_set, quality)
    ball_control = next(r for r in results if r["skill"] == "ball_control")

    assert 0 <= ball_control["score"] <= 100
    assert ball_control["model_version"] == skill_inference.MODEL_VERSION
    assert "touch_consistency" in ball_control["supporting_metrics"]
    assert "mean_player_ball_distance_units" in ball_control["supporting_metrics"]
    assert ball_control["confidence"] in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"}


def test_agility_result_requires_movement_data():
    samples = [sample(0.0, player=[0.5, 0.5])]
    feature_set = build_feature_set(samples)
    quality = evaluate_session_quality(samples)
    results = skill_inference.infer_skills(feature_set, quality)
    assert not any(r["skill"] == "agility" for r in results)


def test_model_is_explicitly_marked_experimental():
    assert skill_inference.MODEL_STATUS == "experimental"
    assert skill_inference.MODEL_TYPE == "rule_based"
