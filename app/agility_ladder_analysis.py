from statistics import fmean

from app.movement_signals import interpolate_short_gaps, smooth_signal


ANALYSIS_VERSION = "agility-ladder-rules-1"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _prepare_signal(values: list[float | None]) -> list[float | None]:
    return smooth_signal(
        interpolate_short_gaps(values, max_gap=2),
        window_size=3,
    )


def _detect_foot_events(
    frames: list[dict],
    side: str,
    minimum_foot_lift_ratio: float,
    minimum_event_duration_ms: int,
    maximum_event_duration_ms: int,
) -> list[dict]:
    signal_name = f"{side}_ankle_hip_vertical_ratio"
    knee_name = f"{side}_knee_hip_vertical_ratio"
    foot_signal = _prepare_signal(
        [frame["measurements"].get(signal_name) for frame in frames]
    )
    knee_signal = _prepare_signal(
        [frame["measurements"].get(knee_name) for frame in frames]
    )
    available = [value for value in foot_signal if value is not None]

    if len(available) < 5:
        return []

    ground_reference = _percentile(available, 0.8)
    observed_lift = ground_reference - min(available)

    if observed_lift < minimum_foot_lift_ratio:
        return []

    required_lift = max(
        minimum_foot_lift_ratio,
        observed_lift * 0.35,
    )
    lift_threshold = ground_reference - required_lift
    contact_threshold = ground_reference - (required_lift * 0.45)
    state = "grounded"
    lift_start = None
    peak_index = None
    peak_value = None
    events = []

    for index, value in enumerate(foot_signal):
        if value is None:
            continue

        if state == "grounded":
            if value <= lift_threshold:
                state = "lifted"
                lift_start = index
                peak_index = index
                peak_value = value
            continue

        if value < peak_value:
            peak_index = index
            peak_value = value

        duration = (
            frames[index]["timestamp_ms"]
            - frames[lift_start]["timestamp_ms"]
        )

        if duration > maximum_event_duration_ms:
            state = "grounded"
            lift_start = None
            peak_index = None
            peak_value = None
            continue

        if (
            duration >= minimum_event_duration_ms
            and value >= contact_threshold
        ):
            knee_values = [
                knee_signal[item_index]
                for item_index in range(lift_start, index + 1)
                if knee_signal[item_index] is not None
            ]
            knee_reference_values = [
                knee_signal[item_index]
                for item_index in range(max(0, lift_start - 3), lift_start + 1)
                if knee_signal[item_index] is not None
            ]
            knee_reference = (
                max(knee_reference_values)
                if knee_reference_values
                else None
            )
            peak_knee_drive = (
                knee_reference - min(knee_values)
                if knee_reference is not None and knee_values
                else None
            )
            events.append(
                {
                    "foot": side,
                    "lift_start_timestamp_ms": frames[lift_start][
                        "timestamp_ms"
                    ],
                    "peak_lift_timestamp_ms": frames[peak_index][
                        "timestamp_ms"
                    ],
                    "contact_timestamp_ms": frames[index]["timestamp_ms"],
                    "air_time_ms": duration,
                    "foot_lift_torso_ratio": (
                        ground_reference - peak_value
                    ),
                    "knee_drive_torso_ratio": peak_knee_drive,
                }
            )
            state = "grounded"
            lift_start = None
            peak_index = None
            peak_value = None

    return events


def _mean_absolute_measurement(
    frames: list[dict],
    name: str,
) -> float | None:
    values = [
        abs(frame["measurements"][name])
        for frame in frames
        if frame["measurements"].get(name) is not None
    ]
    return fmean(values) if values else None


def analyze_agility_ladder(
    pose_features: dict,
    minimum_foot_lift_ratio: float = 0.18,
    minimum_event_duration_ms: int = 60,
    maximum_event_duration_ms: int = 1500,
) -> dict:
    frames = pose_features.get("frames", [])
    result = {
        "schema_version": "1.0",
        "analysis_type": "agility_ladder",
        "analysis_version": ANALYSIS_VERSION,
        "requires_coach_review": True,
        "thresholds": {
            "minimum_foot_lift_torso_ratio": minimum_foot_lift_ratio,
            "minimum_event_duration_ms": minimum_event_duration_ms,
            "maximum_event_duration_ms": maximum_event_duration_ms,
        },
        "summary": {
            "step_count": 0,
            "left_step_count": 0,
            "right_step_count": 0,
            "cadence_steps_per_minute": None,
            "alternation_rate": None,
            "step_count_imbalance": None,
        },
        "steps": [],
    }

    if len(frames) < 5:
        result["status"] = "insufficient_pose_data"
        return result

    events = []

    for side in ("left", "right"):
        events.extend(
            _detect_foot_events(
                frames=frames,
                side=side,
                minimum_foot_lift_ratio=minimum_foot_lift_ratio,
                minimum_event_duration_ms=minimum_event_duration_ms,
                maximum_event_duration_ms=maximum_event_duration_ms,
            )
        )

    events.sort(key=lambda event: event["contact_timestamp_ms"])

    for number, event in enumerate(events, start=1):
        event["step_number"] = number

    left_count = sum(event["foot"] == "left" for event in events)
    right_count = sum(event["foot"] == "right" for event in events)
    cadence = None
    alternation_rate = None

    if len(events) >= 2:
        event_duration = (
            events[-1]["contact_timestamp_ms"]
            - events[0]["contact_timestamp_ms"]
        )

        if event_duration > 0:
            cadence = (len(events) - 1) * 60_000 / event_duration

        alternations = sum(
            current["foot"] != previous["foot"]
            for previous, current in zip(events, events[1:])
        )
        alternation_rate = alternations / (len(events) - 1)

    posture = {
        "mean_absolute_trunk_lean_degrees": _mean_absolute_measurement(
            frames,
            "trunk_lean_degrees",
        ),
        "mean_absolute_hip_tilt_degrees": _mean_absolute_measurement(
            frames,
            "hip_tilt_degrees",
        ),
        "mean_absolute_shoulder_tilt_degrees": (
            _mean_absolute_measurement(frames, "shoulder_tilt_degrees")
        ),
        "mean_knee_asymmetry_degrees": _mean_absolute_measurement(
            frames,
            "knee_angle_asymmetry_degrees",
        ),
    }
    result["status"] = "completed" if events else "no_steps_detected"
    result["steps"] = events
    result["summary"] = {
        "step_count": len(events),
        "left_step_count": left_count,
        "right_step_count": right_count,
        "cadence_steps_per_minute": cadence,
        "alternation_rate": alternation_rate,
        "step_count_imbalance": (
            abs(left_count - right_count) / len(events)
            if events
            else None
        ),
        "mean_foot_lift_torso_ratio": (
            fmean(event["foot_lift_torso_ratio"] for event in events)
            if events
            else None
        ),
        "mean_knee_drive_torso_ratio": (
            fmean(
                event["knee_drive_torso_ratio"]
                for event in events
                if event["knee_drive_torso_ratio"] is not None
            )
            if any(
                event["knee_drive_torso_ratio"] is not None
                for event in events
            )
            else None
        ),
        **posture,
    }
    return result
