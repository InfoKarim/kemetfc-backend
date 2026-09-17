from app.services.tracking_quality import (
    HIGH,
    INSUFFICIENT_DATA,
    LOW,
    REVIEW_REQUIRED,
    VALID,
    VALID_WITH_LIMITED_CONFIDENCE,
    confidence_for_metric,
    evaluate_session_quality,
)


def make_samples(count, locked_ratio=1.0, ball_ratio=1.0, pose_ratio=0.0):
    samples = []
    for i in range(count):
        samples.append({
            "tracking_status": "locked" if i < count * locked_ratio else "lost",
            "ball_confidence": 0.8 if i < count * ball_ratio else 0.0,
            "pose_keypoints": [{"name": "leftHip"}] if i < count * pose_ratio else None,
        })
    return samples


def test_insufficient_data_below_minimum_sample_count():
    result = evaluate_session_quality(make_samples(5))
    assert result["outcome"] == INSUFFICIENT_DATA
    assert result["overall_confidence"] == INSUFFICIENT_DATA


def test_valid_outcome_with_strong_lock_and_ball_coverage():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.9, ball_ratio=0.8))
    assert result["outcome"] == VALID
    assert result["overall_confidence"] == HIGH


def test_review_required_when_player_lock_is_poor():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.2, ball_ratio=0.8))
    assert result["outcome"] == REVIEW_REQUIRED
    assert any("locked" in reason for reason in result["reasons"])


def test_limited_confidence_when_lock_ok_but_ball_coverage_poor():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.9, ball_ratio=0.1))
    assert result["outcome"] == VALID_WITH_LIMITED_CONFIDENCE


def test_confidence_for_metric_insufficient_below_min_samples():
    assert confidence_for_metric(data_coverage_ratio=0.9, sample_count=3) == INSUFFICIENT_DATA


def test_confidence_for_metric_scales_with_coverage():
    assert confidence_for_metric(data_coverage_ratio=0.9, sample_count=50) == HIGH
    assert confidence_for_metric(data_coverage_ratio=0.4, sample_count=50) == "MEDIUM"
    assert confidence_for_metric(data_coverage_ratio=0.05, sample_count=50) == LOW
