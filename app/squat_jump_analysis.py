from statistics import fmean

from app.movement_signals import interpolate_short_gaps, smooth_signal


ANALYSIS_VERSION = "squat-jump-rules-1"


def _average_available(*values: float | None) -> float | None:
    available = [value for value in values if value is not None]
    return fmean(available) if available else None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _phase(
    name: str,
    series: list[dict],
    start_index: int,
    end_index: int,
) -> dict:
    return {
        "name": name,
        "start_timestamp_ms": series[start_index]["timestamp_ms"],
        "end_timestamp_ms": series[end_index]["timestamp_ms"],
    }


def _build_series(feature_frames: list[dict]) -> list[dict]:
    series = []

    for frame in feature_frames:
        measurements = frame.get("measurements", {})
        knee_angle = _average_available(
            measurements.get("left_knee_angle_degrees"),
            measurements.get("right_knee_angle_degrees"),
        )
        series.append(
            {
                "frame_index": frame["frame_index"],
                "timestamp_ms": frame["timestamp_ms"],
                "knee_angle": knee_angle,
                "ankle_y": measurements.get(
                    "ankle_center_y_normalized"
                ),
                "hip_y": measurements.get("hip_center_y_normalized"),
                "knee_asymmetry": measurements.get(
                    "knee_angle_asymmetry_degrees"
                ),
                "trunk_lean": measurements.get("trunk_lean_degrees"),
            }
        )

    for field in ("knee_angle", "ankle_y", "hip_y"):
        interpolated = interpolate_short_gaps(
            [item[field] for item in series],
            max_gap=2,
        )
        smoothed = smooth_signal(interpolated, window_size=5)

        for item, value in zip(series, smoothed):
            item[field] = value

    return series


def _detect_jump(
    series: list[dict],
    start_index: int,
    bottom_index: int,
    end_index: int,
    minimum_ankle_lift: float,
) -> tuple[bool, int | None, int | None, float]:
    ankle_values = [
        (index, series[index]["ankle_y"])
        for index in range(start_index, end_index + 1)
        if series[index]["ankle_y"] is not None
    ]

    if not ankle_values:
        return False, None, None, 0.0

    baseline = max(value for _, value in ankle_values)
    apex_index, apex_value = min(
        ankle_values,
        key=lambda item: item[1],
    )
    ankle_lift = baseline - apex_value

    if ankle_lift < minimum_ankle_lift or apex_index <= bottom_index:
        return False, None, None, ankle_lift

    takeoff_index = next(
        (
            index
            for index, value in ankle_values
            if index >= bottom_index
            and value <= baseline - minimum_ankle_lift
        ),
        None,
    )

    if takeoff_index is None:
        return False, None, None, ankle_lift

    landing_index = next(
        (
            index
            for index, value in ankle_values
            if index > apex_index
            and value >= baseline - (minimum_ankle_lift / 2.0)
        ),
        None,
    )

    if landing_index is None:
        return False, None, None, ankle_lift

    return True, takeoff_index, landing_index, ankle_lift


def analyze_squat_jumps(
    pose_features: dict,
    minimum_knee_range_degrees: float = 25.0,
    minimum_ankle_lift: float = 0.025,
    minimum_cycle_duration_ms: int = 400,
    maximum_cycle_duration_ms: int = 6000,
) -> dict:
    series = _build_series(pose_features.get("frames", []))
    knee_values = [
        item["knee_angle"]
        for item in series
        if item["knee_angle"] is not None
    ]
    base_result = {
        "schema_version": "1.0",
        "analysis_type": "squat_jump",
        "analysis_version": ANALYSIS_VERSION,
        "requires_coach_review": True,
        "thresholds": {
            "minimum_knee_range_degrees": minimum_knee_range_degrees,
            "minimum_ankle_lift_normalized": minimum_ankle_lift,
            "minimum_cycle_duration_ms": minimum_cycle_duration_ms,
            "maximum_cycle_duration_ms": maximum_cycle_duration_ms,
        },
        "summary": {
            "movement_cycle_count": 0,
            "jump_count": 0,
        },
        "repetitions": [],
    }

    if len(knee_values) < 5:
        base_result["status"] = "insufficient_pose_data"
        return base_result

    standing_reference = _percentile(knee_values, 0.8)
    minimum_knee_angle = min(knee_values)
    knee_range = standing_reference - minimum_knee_angle
    base_result["standing_knee_reference_degrees"] = standing_reference
    base_result["observed_knee_range_degrees"] = knee_range

    if knee_range < minimum_knee_range_degrees:
        base_result["status"] = "insufficient_movement"
        return base_result

    descent_threshold = standing_reference - 12.0
    bottom_threshold = standing_reference - minimum_knee_range_degrees
    recovery_threshold = standing_reference - 10.0
    state = "ready"
    start_index = None
    bottom_index = None
    bottom_angle = None
    repetitions = []

    for index, item in enumerate(series):
        angle = item["knee_angle"]

        if angle is None:
            continue

        if state == "ready":
            if angle <= descent_threshold:
                start_index = max(0, index - 1)
                bottom_index = index
                bottom_angle = angle
                state = "descending"
            continue

        if state == "descending":
            if angle < bottom_angle:
                bottom_angle = angle
                bottom_index = index

            if (
                bottom_angle <= bottom_threshold
                and index > bottom_index
                and angle >= bottom_angle + 8.0
            ):
                state = "ascending"
            elif (
                item["timestamp_ms"]
                - series[start_index]["timestamp_ms"]
                > maximum_cycle_duration_ms
            ):
                state = "ready"
            continue

        duration = (
            item["timestamp_ms"]
            - series[start_index]["timestamp_ms"]
        )

        if duration > maximum_cycle_duration_ms:
            state = "ready"
            start_index = None
            bottom_index = None
            bottom_angle = None
            continue

        if state == "ascending" and angle >= recovery_threshold:
            if duration < minimum_cycle_duration_ms:
                continue

            jump_detected, takeoff_index, landing_index, ankle_lift = (
                _detect_jump(
                    series=series,
                    start_index=start_index,
                    bottom_index=bottom_index,
                    end_index=index,
                    minimum_ankle_lift=minimum_ankle_lift,
                )
            )

            if not jump_detected and ankle_lift >= minimum_ankle_lift:
                continue

            phases = [
                _phase("descent", series, start_index, bottom_index),
            ]

            if jump_detected:
                phases.extend(
                    [
                        _phase(
                            "ascent",
                            series,
                            bottom_index,
                            takeoff_index,
                        ),
                        _phase(
                            "flight",
                            series,
                            takeoff_index,
                            landing_index,
                        ),
                        _phase(
                            "landing_recovery",
                            series,
                            landing_index,
                            index,
                        ),
                    ]
                )
            else:
                phases.append(
                    _phase("ascent_recovery", series, bottom_index, index)
                )

            window = series[start_index: index + 1]
            asymmetry_values = [
                frame["knee_asymmetry"]
                for frame in window
                if frame["knee_asymmetry"] is not None
            ]
            trunk_values = [
                frame["trunk_lean"]
                for frame in window
                if frame["trunk_lean"] is not None
            ]
            hip_values = [
                frame["hip_y"]
                for frame in window
                if frame["hip_y"] is not None
            ]
            repetitions.append(
                {
                    "repetition_number": len(repetitions) + 1,
                    "start_timestamp_ms": series[start_index][
                        "timestamp_ms"
                    ],
                    "end_timestamp_ms": item["timestamp_ms"],
                    "duration_ms": duration,
                    "jump_detected": jump_detected,
                    "phases": phases,
                    "measurements": {
                        "minimum_knee_angle_degrees": bottom_angle,
                        "knee_range_degrees": (
                            standing_reference - bottom_angle
                        ),
                        "ankle_lift_normalized": ankle_lift,
                        "hip_vertical_range_normalized": (
                            max(hip_values) - min(hip_values)
                            if hip_values
                            else None
                        ),
                        "mean_knee_asymmetry_degrees": (
                            fmean(asymmetry_values)
                            if asymmetry_values
                            else None
                        ),
                        "mean_trunk_lean_degrees": (
                            fmean(trunk_values) if trunk_values else None
                        ),
                    },
                }
            )
            state = "ready"
            start_index = None
            bottom_index = None
            bottom_angle = None

    base_result["status"] = (
        "completed" if repetitions else "no_complete_cycles_detected"
    )
    base_result["repetitions"] = repetitions
    base_result["summary"] = {
        "movement_cycle_count": len(repetitions),
        "jump_count": sum(
            repetition["jump_detected"] for repetition in repetitions
        ),
    }
    return base_result
