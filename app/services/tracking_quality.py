"""The quality gate a tracking session's raw samples must pass before any
metric or skill inference derived from it is published to a Player
Profile. Pure, rule-based, and deliberately conservative: low-quality
input produces a low-confidence or REVIEW_REQUIRED/INSUFFICIENT_DATA
result rather than a fabricated full assessment (see spec "QUALITY GATE").
"""

from __future__ import annotations

VALID = "VALID"
VALID_WITH_LIMITED_CONFIDENCE = "VALID_WITH_LIMITED_CONFIDENCE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
INSUFFICIENT = "INSUFFICIENT_DATA"

# Minimum number of samples for a session to be evaluable at all —
# below this, nothing meaningful can be derived regardless of quality.
MIN_SAMPLE_COUNT = 10
LOCK_RATIO_VALID = 0.70
LOCK_RATIO_LIMITED = 0.40
BALL_COVERAGE_HIGH = 0.60
BALL_COVERAGE_MEDIUM = 0.25
POSE_COVERAGE_HIGH = 0.50
POSE_COVERAGE_MEDIUM = 0.15


def _ratio(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def evaluate_session_quality(samples: list[dict]) -> dict:
    """Compute the objective quality signals a session's samples support,
    and the overall gate outcome. Every field here is a plain count/ratio
    — nothing here is itself a skill or performance judgement."""
    total = len(samples)
    if total < MIN_SAMPLE_COUNT:
        return {
            "outcome": INSUFFICIENT_DATA,
            "sample_count": total,
            "player_lock_ratio": None,
            "ball_coverage_ratio": None,
            "pose_coverage_ratio": None,
            "overall_confidence": INSUFFICIENT,
            "reasons": [f"Only {total} samples recorded (minimum {MIN_SAMPLE_COUNT})."],
        }

    locked = sum(1 for s in samples if s.get("tracking_status") == "locked")
    ball_seen = sum(1 for s in samples if (s.get("ball_confidence") or 0) > 0)
    pose_seen = sum(1 for s in samples if s.get("pose_keypoints"))

    player_lock_ratio = _ratio(locked, total)
    ball_coverage_ratio = _ratio(ball_seen, total)
    pose_coverage_ratio = _ratio(pose_seen, total)

    reasons: list[str] = []

    if player_lock_ratio < LOCK_RATIO_LIMITED:
        outcome = REVIEW_REQUIRED
        reasons.append(
            f"Selected player was locked for only {player_lock_ratio:.0%} of the session."
        )
    elif player_lock_ratio < LOCK_RATIO_VALID:
        outcome = VALID_WITH_LIMITED_CONFIDENCE
        reasons.append(
            f"Selected player was locked for {player_lock_ratio:.0%} of the session "
            "(below the high-confidence threshold)."
        )
    else:
        outcome = VALID

    if ball_coverage_ratio < BALL_COVERAGE_MEDIUM and outcome == VALID:
        outcome = VALID_WITH_LIMITED_CONFIDENCE
        reasons.append(f"Ball detected in only {ball_coverage_ratio:.0%} of samples.")

    if player_lock_ratio >= LOCK_RATIO_VALID and ball_coverage_ratio >= BALL_COVERAGE_HIGH:
        overall_confidence = HIGH
    elif player_lock_ratio >= LOCK_RATIO_LIMITED and ball_coverage_ratio >= BALL_COVERAGE_MEDIUM:
        overall_confidence = MEDIUM
    else:
        overall_confidence = LOW

    return {
        "outcome": outcome,
        "sample_count": total,
        "player_lock_ratio": player_lock_ratio,
        "ball_coverage_ratio": ball_coverage_ratio,
        "pose_coverage_ratio": pose_coverage_ratio,
        "overall_confidence": overall_confidence,
        "reasons": reasons,
    }


def confidence_for_metric(*, data_coverage_ratio: float | None, sample_count: int) -> str:
    """Confidence for one specific derived metric — deliberately separate
    from the session-level quality gate, since e.g. ball coverage can be
    poor while player movement metrics remain HIGH confidence."""
    if data_coverage_ratio is None or sample_count < MIN_SAMPLE_COUNT:
        return INSUFFICIENT
    if data_coverage_ratio >= BALL_COVERAGE_HIGH:
        return HIGH
    if data_coverage_ratio >= BALL_COVERAGE_MEDIUM:
        return MEDIUM
    return LOW
