"""The quality gate a tracking session's raw samples must pass before any
metric or skill inference derived from it is published to a Player
Profile. Pure, rule-based, and deliberately conservative: low-quality
input produces a low-confidence or REVIEW_REQUIRED/INSUFFICIENT_DATA
result rather than a fabricated full assessment (see spec "QUALITY GATE").

Phase 2 additions: target_lost_duration_seconds, telemetry_completeness_ratio,
and id_switch_risk_count — a wrong-player lock is treated as seriously as
the spec demands ("A model that detects people accurately but switches to
another child is not acceptable"), so any session with detected switch
risk is forced to at least REVIEW_REQUIRED regardless of how good its
other ratios look.
"""

from __future__ import annotations

import math

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

# A "locked" player's bounding-box center should never jump this far
# between two consecutive samples at KEMET's bounded sample rate (see
# TelemetryUploader's ~15-20Hz effective inference rate on the iOS
# side) — a jump past this is independently re-derived here as a
# possible wrong-player lock, mirroring (not trusting blindly) the
# iOS-side PlayerTracker's own plausibility gate, so a client-side bug
# or a genuinely hard case still gets caught server-side.
POSSIBLE_ID_SWITCH_DISTANCE = 0.25
# Below this, we treat two samples as too far apart in time to compare
# for a "jump" at all (e.g. a gap during TEMPORARILY_LOST/SEARCHING).
ID_SWITCH_MAX_DT_SECONDS = 0.5

# Minimum acceptable average sample rate — used only to compute
# telemetry_completeness_ratio, a QA signal, never as a hard requirement.
EXPECTED_MIN_SAMPLE_RATE_HZ = 5.0


def _ratio(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _target_lost_duration_seconds(samples: list[dict]) -> float:
    ordered = sorted(samples, key=lambda s: s["t_seconds"])
    longest = 0.0
    current_start: float | None = None
    for sample in ordered:
        if sample.get("tracking_status") != "locked":
            if current_start is None:
                current_start = sample["t_seconds"]
        else:
            if current_start is not None:
                longest = max(longest, sample["t_seconds"] - current_start)
                current_start = None
    if current_start is not None:
        longest = max(longest, ordered[-1]["t_seconds"] - current_start)
    return longest


def _telemetry_completeness_ratio(samples: list[dict]) -> float:
    ordered = sorted(samples, key=lambda s: s["t_seconds"])
    duration = ordered[-1]["t_seconds"] - ordered[0]["t_seconds"]
    if duration <= 0:
        return 1.0
    expected = duration * EXPECTED_MIN_SAMPLE_RATE_HZ
    return min(1.0, len(ordered) / expected) if expected > 0 else 1.0


def _id_switch_risk_count(samples: list[dict]) -> int:
    ordered = [
        s for s in sorted(samples, key=lambda s: s["t_seconds"])
        if s.get("tracking_status") == "locked" and s.get("player_center") is not None
    ]
    switches = 0
    for prev, curr in zip(ordered, ordered[1:]):
        dt = curr["t_seconds"] - prev["t_seconds"]
        if dt <= 0 or dt > ID_SWITCH_MAX_DT_SECONDS:
            continue
        if _distance(prev["player_center"], curr["player_center"]) > POSSIBLE_ID_SWITCH_DISTANCE:
            switches += 1
    return switches


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
            "target_lost_duration_seconds": None,
            "telemetry_completeness_ratio": None,
            "id_switch_risk_count": None,
            "overall_confidence": INSUFFICIENT,
            "reasons": [f"Only {total} samples recorded (minimum {MIN_SAMPLE_COUNT})."],
        }

    locked = sum(1 for s in samples if s.get("tracking_status") == "locked")
    ball_seen = sum(1 for s in samples if (s.get("ball_confidence") or 0) > 0)
    pose_seen = sum(1 for s in samples if s.get("pose_keypoints"))

    player_lock_ratio = _ratio(locked, total)
    ball_coverage_ratio = _ratio(ball_seen, total)
    pose_coverage_ratio = _ratio(pose_seen, total)
    target_lost_duration_seconds = _target_lost_duration_seconds(samples)
    telemetry_completeness_ratio = _telemetry_completeness_ratio(samples)
    id_switch_risk_count = _id_switch_risk_count(samples)

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

    if telemetry_completeness_ratio < 0.5 and outcome == VALID:
        outcome = VALID_WITH_LIMITED_CONFIDENCE
        reasons.append(
            f"Telemetry completeness is only {telemetry_completeness_ratio:.0%} of the "
            "expected minimum sample rate — recording may have gaps."
        )

    # A wrong-player lock is treated seriously, per spec: any detected
    # possible ID switch forces at least REVIEW_REQUIRED, overriding a
    # VALID outcome the other ratios alone would have produced.
    if id_switch_risk_count > 0:
        outcome = REVIEW_REQUIRED
        reasons.append(
            f"{id_switch_risk_count} sample(s) show an implausible position jump while "
            "\"locked\" — possible wrong-player tracking. Coach review required."
        )

    if player_lock_ratio >= LOCK_RATIO_VALID and ball_coverage_ratio >= BALL_COVERAGE_HIGH and id_switch_risk_count == 0:
        overall_confidence = HIGH
    elif player_lock_ratio >= LOCK_RATIO_LIMITED and ball_coverage_ratio >= BALL_COVERAGE_MEDIUM and id_switch_risk_count == 0:
        overall_confidence = MEDIUM
    else:
        overall_confidence = LOW

    return {
        "outcome": outcome,
        "sample_count": total,
        "player_lock_ratio": player_lock_ratio,
        "ball_coverage_ratio": ball_coverage_ratio,
        "pose_coverage_ratio": pose_coverage_ratio,
        "target_lost_duration_seconds": target_lost_duration_seconds,
        "telemetry_completeness_ratio": telemetry_completeness_ratio,
        "id_switch_risk_count": id_switch_risk_count,
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
