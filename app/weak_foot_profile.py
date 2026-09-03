from dataclasses import dataclass

WEAK_FOOT_FIELD_HINTS = {
    "weak_foot_usage_pct": (
        "<0-100 integer>  # Estimated % of on-ball actions (passes, "
        "touches, shots) performed with the non-dominant foot"
    ),
    "weak_foot_passing": "<0-100 integer>  # Passing quality with the non-dominant foot",
    "weak_foot_receiving": "<0-100 integer>  # First-touch control with the non-dominant foot",
    "weak_foot_dribbling": "<0-100 integer>  # Dribbling/close control with the non-dominant foot",
    "weak_foot_finishing": "<0-100 integer>  # Shooting/finishing with the non-dominant foot",
}


@dataclass
class WeakFootProfile:
    weak_foot_usage_pct: float
    weak_foot_passing: float
    weak_foot_receiving: float
    weak_foot_dribbling: float
    weak_foot_finishing: float
