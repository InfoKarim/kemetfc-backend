"""Skill inference layer — EXPERIMENTAL.

IMPORTANT / HONEST STATUS: this module does NOT contain a trained neural
network. KEMET FC has no annotated training dataset yet (assessments only
started being collected this session), and training/validating a temporal
model (TCN/LSTM/Transformer) requires real accumulated, coach-labeled data
this platform does not have. Building and then claiming a "trained model"
here would be fabricating model performance, which the product spec this
module implements explicitly forbids.

What this module actually is: a transparent, rule-based, fully-explainable
scorer over the objective features in tracking_features.py — every score
traces directly to named supporting metrics, with no hidden weights beyond
the documented constants below. It is registered in MLModelRegistryDB
under model_type="rule_based" and status="experimental" so it is never
confused with a validated model, and every result carries
model_version="rule-based-experimental-v0.1" plus a confidence derived
from tracking_quality.py — never a bare number presented as ground truth.

Swapping in a real trained temporal model later requires no change to
callers: implement `SkillInferenceModel` and register it in the model
registry with a new model_id; tracking_service.py resolves the active
model by name from the registry rather than importing this module by name.
"""

from __future__ import annotations

from typing import Protocol

from app.services.tracking_quality import HIGH, INSUFFICIENT, LOW, MEDIUM

MODEL_NAME = "skill-inference"
MODEL_VERSION = "rule-based-experimental-v0.1"
MODEL_TYPE = "rule_based"
MODEL_STATUS = "experimental"

# Every weight below is a documented, transparent product decision, not a
# learned parameter — this is the whole point of shipping a rule-based
# placeholder instead of an unvalidated black box.
_TOUCH_RATE_TARGET_PER_MIN = 20.0
_DIRECTION_CHANGE_TARGET_PER_MIN = 12.0


class SkillInferenceModel(Protocol):
    """Interface a future trained model must implement to be a drop-in
    replacement — resolved by name/version from MLModelRegistryDB, never
    hardcoded, so rollback is a registry status flip, not a code change."""

    def infer(self, feature_set: dict, quality: dict) -> dict: ...


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _confidence_from_quality(quality: dict, *, requires_ball: bool) -> str:
    overall = quality.get("overall_confidence", INSUFFICIENT)
    if overall == INSUFFICIENT:
        return INSUFFICIENT
    if requires_ball:
        ball_ratio = quality.get("ball_coverage_ratio") or 0.0
        if ball_ratio < 0.25:
            return LOW
        if ball_ratio < 0.60:
            return MEDIUM
    return overall


def _ball_control_score(feature_set: dict, quality: dict) -> dict | None:
    ball = feature_set.get("ball_relationship", {})
    movement = feature_set.get("movement", {})
    if ball.get("status") != "ok":
        return None

    touch_count = ball.get("touch_count") or 0
    possession_seconds = ball.get("possession_seconds") or 0.0
    duration = None
    speed_series = movement.get("speed_series") or []
    if speed_series:
        duration = speed_series[-1]["t_seconds"] - speed_series[0]["t_seconds"]
    touch_rate_per_min = (touch_count / duration * 60) if duration else 0.0

    distances = [p["distance_units"] for p in ball.get("player_ball_distance_series", [])]
    mean_distance = (sum(distances) / len(distances)) if distances else None

    direction_changes = movement.get("direction_change_count") or 0
    direction_change_rate = (direction_changes / duration * 60) if duration else 0.0

    touch_consistency = round(
        _clamp(touch_rate_per_min / _TOUCH_RATE_TARGET_PER_MIN, 0, 1) * 100
    )
    proximity_component = round(
        _clamp(1 - (mean_distance / 0.30 if mean_distance is not None else 1), 0, 1) * 100
    )
    direction_control = round(
        _clamp(1 - abs(direction_change_rate - _DIRECTION_CHANGE_TARGET_PER_MIN) / _DIRECTION_CHANGE_TARGET_PER_MIN, 0, 1) * 100
    )

    composite = round((touch_consistency + proximity_component + direction_control) / 3)

    return {
        "skill": "ball_control",
        "score": composite,
        "scale": "0-100",
        "confidence": _confidence_from_quality(quality, requires_ball=True),
        "supporting_metrics": {
            "touch_consistency": touch_consistency,
            "mean_player_ball_distance_units": mean_distance,
            "direction_change_control": direction_control,
            "touch_count": touch_count,
            "possession_seconds": possession_seconds,
        },
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "data_quality_status": quality.get("outcome"),
    }


def _agility_score(feature_set: dict, quality: dict) -> dict | None:
    movement = feature_set.get("movement", {})
    if movement.get("status") != "ok":
        return None

    peak_speed = movement.get("peak_speed_units_per_s")
    avg_speed = movement.get("average_speed_units_per_s")
    direction_changes = movement.get("direction_change_count") or 0

    burst_component = round(
        _clamp((peak_speed / avg_speed - 1) if avg_speed else 0, 0, 2) / 2 * 100
    ) if avg_speed else 0
    direction_component = round(_clamp(direction_changes / 15, 0, 1) * 100)

    composite = round((burst_component + direction_component) / 2)

    return {
        "skill": "agility",
        "score": composite,
        "scale": "0-100",
        "confidence": _confidence_from_quality(quality, requires_ball=False),
        "supporting_metrics": {
            "peak_speed_units_per_s": peak_speed,
            "average_speed_units_per_s": avg_speed,
            "direction_change_count": direction_changes,
        },
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "data_quality_status": quality.get("outcome"),
    }


def infer_skills(feature_set: dict, quality: dict) -> list[dict]:
    """Return every skill result this rule-based model can support given
    the available features — never fabricates a result for a skill whose
    prerequisite features are missing (e.g. no ball_control without any
    paired player+ball samples)."""
    results = []
    for builder in (_ball_control_score, _agility_score):
        result = builder(feature_set, quality)
        if result is not None:
            results.append(result)
    return results
