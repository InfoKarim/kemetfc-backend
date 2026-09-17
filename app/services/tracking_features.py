"""Pure, DB-free feature extraction from a tracking session's raw sample
stream. Every function here takes plain dicts/lists in and returns plain
dicts/lists out — no SQLAlchemy, no I/O — so it can be exhaustively unit
tested and, per the "feature store" requirement, run exactly once per
session with the result cached (see TrackingFeatureSetDB /
app.services.tracking_service) rather than recomputed from raw video on
every future experiment.

Every distance/speed value is in *normalized image-space units* (the
player/ball center coordinates are 0-1 fractions of frame width/height)
UNLESS a session's calibration_scale_m_per_unit is provided, in which case
distances also get a meters-denominated sibling value. Never silently
report meters without that explicit calibration.
"""

from __future__ import annotations

import math

FEATURE_SCHEMA_VERSION = "tracking-features-v1"

# A touch/possession distance threshold in normalized image-space units —
# deliberately conservative (calibrated empirically against a ~0.08-0.12
# typical player-bbox width at midfield framing, not a validated
# real-world distance) and documented as such; see EXPERIMENTAL labeling
# in skill_inference.py for anything derived from it.
POSSESSION_DISTANCE_THRESHOLD = 0.10
# Minimum seconds between two counted touch events, to avoid counting
# sensor jitter around the threshold as repeated touches.
MIN_TOUCH_INTERVAL_SECONDS = 0.35
# Minimum bearing change (radians) between consecutive movement segments
# to count as a "direction change" — filters out normal path curvature.
DIRECTION_CHANGE_ANGLE_THRESHOLD_RAD = math.radians(45)
# A sample's own confidence below this is excluded from motion-derived
# metrics entirely (treated as "no detection"), rather than trusted.
MIN_USABLE_CONFIDENCE = 0.35


def _usable_player_points(samples: list[dict]) -> list[dict]:
    """Samples with a confident player_center, in time order."""
    points = [
        s for s in samples
        if s.get("player_center") is not None
        and (s.get("player_confidence") or 0) >= MIN_USABLE_CONFIDENCE
    ]
    return sorted(points, key=lambda s: s["t_seconds"])


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def compute_movement_features(samples: list[dict]) -> dict:
    """Distance traveled, speed series, peak/average speed, acceleration,
    and direction-change events — derived purely from consecutive
    confident player_center samples. Returns an "insufficient_data"
    result (never a fabricated zero) if fewer than 2 usable points exist.
    """
    points = _usable_player_points(samples)
    if len(points) < 2:
        return {
            "status": "insufficient_data",
            "distance_traveled_units": None,
            "average_speed_units_per_s": None,
            "peak_speed_units_per_s": None,
            "direction_change_count": 0,
            "speed_series": [],
            "usable_sample_count": len(points),
        }

    speed_series: list[dict] = []
    total_distance = 0.0
    prev_bearing: float | None = None
    direction_changes = 0

    for prev, curr in zip(points, points[1:]):
        dt = curr["t_seconds"] - prev["t_seconds"]
        if dt <= 0:
            continue
        dist = _distance(prev["player_center"], curr["player_center"])
        total_distance += dist
        speed = dist / dt
        speed_series.append({"t_seconds": curr["t_seconds"], "speed_units_per_s": speed})

        dx = curr["player_center"][0] - prev["player_center"][0]
        dy = curr["player_center"][1] - prev["player_center"][1]
        if dx != 0 or dy != 0:
            bearing = math.atan2(dy, dx)
            if prev_bearing is not None:
                delta = abs(math.atan2(math.sin(bearing - prev_bearing), math.cos(bearing - prev_bearing)))
                if delta >= DIRECTION_CHANGE_ANGLE_THRESHOLD_RAD:
                    direction_changes += 1
            prev_bearing = bearing

    speeds = [s["speed_units_per_s"] for s in speed_series]
    duration = points[-1]["t_seconds"] - points[0]["t_seconds"]

    return {
        "status": "ok",
        "distance_traveled_units": total_distance,
        "average_speed_units_per_s": (total_distance / duration) if duration > 0 else None,
        "peak_speed_units_per_s": max(speeds) if speeds else None,
        "direction_change_count": direction_changes,
        "speed_series": speed_series,
        "usable_sample_count": len(points),
    }


def compute_ball_relationship_features(samples: list[dict]) -> dict:
    """Player-to-ball distance series, possession intervals, and a
    conservative touch-event count — all derived from samples where BOTH
    player and ball are confidently detected in the same sample."""
    paired = [
        s for s in sorted(samples, key=lambda s: s["t_seconds"])
        if s.get("player_center") is not None
        and s.get("ball_center") is not None
        and (s.get("player_confidence") or 0) >= MIN_USABLE_CONFIDENCE
        and (s.get("ball_confidence") or 0) >= MIN_USABLE_CONFIDENCE
    ]
    if not paired:
        return {
            "status": "insufficient_data",
            "player_ball_distance_series": [],
            "possession_intervals": [],
            "possession_seconds": None,
            "touch_count": None,
            "paired_sample_count": 0,
        }

    distance_series = [
        {"t_seconds": s["t_seconds"], "distance_units": _distance(s["player_center"], s["ball_center"])}
        for s in paired
    ]

    possession_intervals: list[dict] = []
    in_possession = False
    interval_start = None
    touch_count = 0
    last_touch_t: float | None = None

    for point in distance_series:
        close = point["distance_units"] <= POSSESSION_DISTANCE_THRESHOLD
        if close and not in_possession:
            in_possession = True
            interval_start = point["t_seconds"]
            if last_touch_t is None or (point["t_seconds"] - last_touch_t) >= MIN_TOUCH_INTERVAL_SECONDS:
                touch_count += 1
                last_touch_t = point["t_seconds"]
        elif not close and in_possession:
            in_possession = False
            possession_intervals.append({"start_t": interval_start, "end_t": point["t_seconds"]})
            interval_start = None

    if in_possession and interval_start is not None:
        possession_intervals.append({"start_t": interval_start, "end_t": distance_series[-1]["t_seconds"]})

    possession_seconds = sum(i["end_t"] - i["start_t"] for i in possession_intervals)

    return {
        "status": "ok",
        "player_ball_distance_series": distance_series,
        "possession_intervals": possession_intervals,
        "possession_seconds": possession_seconds,
        "touch_count": touch_count,
        "paired_sample_count": len(paired),
    }


def compute_pose_features(samples: list[dict]) -> dict:
    """Coarse, interpretable body-control indicators from Vision body-pose
    keypoints, where present. Deliberately shallow (torso lean angle,
    stance width, left/right symmetry of shoulder-hip alignment) rather
    than a black-box "balance score" — every number here traces to named
    joints. Pose is only sampled at a lower rate than bbox samples on the
    iOS client (battery/CPU), so coverage is usually well under 100%."""
    pose_samples = [s for s in samples if s.get("pose_keypoints")]
    total_samples = len(samples)
    if not pose_samples or total_samples == 0:
        return {
            "status": "insufficient_data",
            "pose_coverage_ratio": 0.0,
            "average_torso_lean_deg": None,
            "sample_count": 0,
        }

    def joint(points: list[dict], name: str) -> dict | None:
        for p in points:
            if p.get("name") == name:
                return p
        return None

    lean_angles = []
    for sample in pose_samples:
        kps = sample["pose_keypoints"]
        left_shoulder = joint(kps, "leftShoulder")
        right_shoulder = joint(kps, "rightShoulder")
        left_hip = joint(kps, "leftHip")
        right_hip = joint(kps, "rightHip")
        if not all([left_shoulder, right_shoulder, left_hip, right_hip]):
            continue
        shoulder_mid = ((left_shoulder["x"] + right_shoulder["x"]) / 2, (left_shoulder["y"] + right_shoulder["y"]) / 2)
        hip_mid = ((left_hip["x"] + right_hip["x"]) / 2, (left_hip["y"] + right_hip["y"]) / 2)
        dx = shoulder_mid[0] - hip_mid[0]
        dy = shoulder_mid[1] - hip_mid[1]
        # Lean from vertical, in degrees — 0 = perfectly upright torso.
        lean_angles.append(abs(math.degrees(math.atan2(dx, -dy))) if dy != 0 else None)

    lean_angles = [a for a in lean_angles if a is not None]

    return {
        "status": "ok" if lean_angles else "insufficient_data",
        "pose_coverage_ratio": len(pose_samples) / total_samples,
        "average_torso_lean_deg": (sum(lean_angles) / len(lean_angles)) if lean_angles else None,
        "sample_count": len(pose_samples),
    }


def build_feature_set(samples: list[dict]) -> dict:
    """The full derived-feature payload cached in TrackingFeatureSetDB —
    combines all of the above into one versioned, storable object."""
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "movement": compute_movement_features(samples),
        "ball_relationship": compute_ball_relationship_features(samples),
        "pose": compute_pose_features(samples),
        "sample_count": len(samples),
    }
