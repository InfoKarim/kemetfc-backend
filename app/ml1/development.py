"""Comparing the same player across dates, without fooling ourselves. (Design module: pure rules, no data source yet.)

A change is reported only when the two measurements are genuinely comparable (same player, drill, protocol, metric
definition, analysis version and capture conditions) AND the difference is larger than what the measurement
uncertainty allows. Otherwise the answer is NOT_COMPARABLE, INSUFFICIENT_EVIDENCE or NO_RELIABLE_CHANGE, never a
guessed improvement. "Better" or "worse" is stated only for a metric whose direction a coach has agreed; there is no
rating and no overall score.
"""
import math
from datetime import date

NOT_COMPARABLE = "NOT_COMPARABLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NO_RELIABLE_CHANGE = "NO_RELIABLE_CHANGE"
CHANGED = "CHANGED"

COMPARABILITY_KEYS = ("anonymous_player_id", "drill_type", "protocol_id", "metric", "unit", "definition_version", "analysis_version")


def comparability_problems(earlier: dict, later: dict) -> list[str]:
    problems = [f"DIFFERENT_{k.upper()}" for k in COMPARABILITY_KEYS if earlier.get(k) != later.get(k)]
    for m in (earlier, later):
        if m.get("evidence_state") != "MEASURED" or m.get("value") is None or not math.isfinite(m["value"]):
            problems.append("NOT_A_MEASURED_VALUE")
            break
    ea, la = earlier.get("assessed_on"), later.get("assessed_on")
    if not isinstance(ea, date) or not isinstance(la, date) or la <= ea:
        problems.append("DATES_NOT_IN_ORDER")
    ca, cb = earlier.get("capture", {}), later.get("capture", {})
    if ca.get("orientation") != cb.get("orientation"):
        problems.append("DIFFERENT_CAMERA_ORIENTATION")
    if max(ca.get("camera_moving_fraction", 1.0), cb.get("camera_moving_fraction", 1.0)) > 0.5:
        problems.append("CAMERA_MOVING_TOO_MUCH_FOR_IMAGE_MOTION_METRICS")
    return sorted(set(problems))


def compare(earlier: dict, later: dict, *, k: float = 2.0, minimum_meaningful: float | None = None, polarity: str | None = None) -> dict:
    """polarity: 'higher_is_better' / 'lower_is_better' only if a coach has agreed it for this metric, else None."""
    problems = comparability_problems(earlier, later)
    if problems:
        return {"result": NOT_COMPARABLE, "reasons": problems}
    ua, ub = earlier.get("uncertainty"), later.get("uncertainty")
    if ua is None or ub is None or ua < 0 or ub < 0:
        return {"result": INSUFFICIENT_EVIDENCE, "reasons": ["MEASUREMENT_UNCERTAINTY_UNKNOWN"],
                "delta": round(later["value"] - earlier["value"], 6)}
    delta = later["value"] - earlier["value"]
    combined = math.hypot(ua, ub)
    out = {"delta": round(delta, 6), "combined_uncertainty": round(combined, 6), "k": k, "unit": earlier["unit"]}
    if abs(delta) <= k * combined:
        return {**out, "result": NO_RELIABLE_CHANGE, "reasons": ["DIFFERENCE_WITHIN_MEASUREMENT_UNCERTAINTY"]}
    if minimum_meaningful is not None and abs(delta) < minimum_meaningful:
        return {**out, "result": NO_RELIABLE_CHANGE, "reasons": ["CHANGE_SMALLER_THAN_THE_MINIMUM_THAT_MATTERS"]}
    direction = "increased" if delta > 0 else "decreased"
    judgement = None
    if polarity in ("higher_is_better", "lower_is_better"):
        judgement = "improved" if (delta > 0) == (polarity == "higher_is_better") else "declined"
    return {**out, "result": CHANGED, "direction": direction, "coach_agreed_judgement": judgement, "reasons": []}


def series(measurements: list[dict], **kwargs) -> list[dict]:
    """Compare each measurement with the previous COMPARABLE one for the same compatibility key; never across keys."""
    ordered = sorted(measurements, key=lambda m: m["assessed_on"])
    out = []
    for i, later in enumerate(ordered):
        earlier = next((e for e in reversed(ordered[:i]) if not [p for p in comparability_problems(e, later) if p.startswith("DIFFERENT_")]), None)
        if earlier is not None:
            out.append({"from": earlier["assessed_on"].isoformat(), "to": later["assessed_on"].isoformat(), **compare(earlier, later, **kwargs)})
    return out
