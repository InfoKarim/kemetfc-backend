"""Estimates Speed, Acceleration, and Agility from an ordinary pose-tracked
video (not a dedicated squat-jump or agility-ladder drill).

Speed and Acceleration use each player's stored height as a per-frame
scale reference — the distance from nose to ankle in pixels for that
frame is assumed to equal the player's real height, which converts hip
displacement between frames into a real-world speed. This is a rough
field estimate for coach review (accuracy depends on the camera being
roughly perpendicular to the player's movement, not panning/zooming, and
the player being mostly upright and fully in frame) — not a validated,
lab-grade sprint or agility test.

Agility counts how often the player's lateral (hip-center) movement
changes direction — a different, simpler signal than the dedicated
agility-ladder analysis's foot-strike detection, applicable to any clip
with side-to-side movement rather than a specific ladder drill.
"""

from app.movement_signals import interpolate_short_gaps, smooth_signal


MIN_TRACKED_SAMPLES = 5

# Conservative, clearly-labeled ceilings for a 0-100 scale — not a
# validated sports-science benchmark, just reasonable reference points so
# a believable clip lands in a believable mid-range rather than pinning
# at 0 or 100. A coach should treat these as a rough estimate to sanity
# check, not a precise measurement.
TOP_SPEED_CEILING_M_S = 6.0
PEAK_ACCELERATION_CEILING_M_S2 = 4.0
DIRECTION_CHANGES_PER_10S_CEILING = 6.0

# Ignore lateral movement smaller than this (as a fraction of frame
# width) between frames when counting direction changes, so landmark jitter
# doesn't get counted as a change of direction.
MIN_LATERAL_MOVEMENT_FRACTION = 0.01


def _bounded_score(value: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return round(min(max(value / ceiling, 0.0), 1.0) * 100, 2)


def _percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)

    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def estimate_speed_and_acceleration(
    frames: list[dict],
    image_width: int,
    image_height: int,
    player_height_cm: float,
) -> list[tuple[str, float]]:
    if not frames or player_height_cm is None or player_height_cm <= 0:
        return []
    if image_width <= 0 or image_height <= 0:
        return []

    speeds_m_s: list[float] = []
    speed_timestamps_ms: list[float] = []
    previous = None

    for frame in frames:
        measurements = frame.get("measurements", {})
        height_px = measurements.get("body_height_pixels")
        hip_x = measurements.get("hip_center_x_normalized")
        hip_y = measurements.get("hip_center_y_normalized")

        if height_px is None or hip_x is None or hip_y is None or height_px <= 0:
            previous = None
            continue

        hip_px = (hip_x * image_width, hip_y * image_height)
        timestamp_ms = frame["timestamp_ms"]

        if previous is not None:
            dt_s = (timestamp_ms - previous["timestamp_ms"]) / 1000.0

            if dt_s > 0:
                pixels_per_cm = (
                    (height_px + previous["height_px"]) / 2.0
                ) / player_height_cm
                dx = hip_px[0] - previous["hip_px"][0]
                dy = hip_px[1] - previous["hip_px"][1]
                distance_cm = ((dx**2 + dy**2) ** 0.5) / pixels_per_cm
                speeds_m_s.append((distance_cm / 100.0) / dt_s)
                speed_timestamps_ms.append(timestamp_ms)

        previous = {
            "timestamp_ms": timestamp_ms,
            "height_px": height_px,
            "hip_px": hip_px,
        }

    if len(speeds_m_s) < MIN_TRACKED_SAMPLES:
        return []

    attributes = []
    top_speed = _percentile(speeds_m_s, 0.9)

    if top_speed is not None:
        attributes.append(
            ("Speed", _bounded_score(top_speed, TOP_SPEED_CEILING_M_S))
        )

    accelerations_m_s2 = [
        abs(speeds_m_s[index] - speeds_m_s[index - 1])
        / ((speed_timestamps_ms[index] - speed_timestamps_ms[index - 1]) / 1000.0)
        for index in range(1, len(speeds_m_s))
        if speed_timestamps_ms[index] > speed_timestamps_ms[index - 1]
    ]
    peak_acceleration = _percentile(accelerations_m_s2, 0.9)

    if peak_acceleration is not None:
        attributes.append((
            "Acceleration",
            _bounded_score(peak_acceleration, PEAK_ACCELERATION_CEILING_M_S2),
        ))

    return attributes


def estimate_agility(frames: list[dict]) -> list[tuple[str, float]]:
    raw_x = [
        frame.get("measurements", {}).get("hip_center_x_normalized")
        for frame in frames
    ]
    timestamps_ms = [frame["timestamp_ms"] for frame in frames]
    tracked = [(t, x) for t, x in zip(timestamps_ms, raw_x) if x is not None]

    if len(tracked) < MIN_TRACKED_SAMPLES:
        return []

    tracked_timestamps = [item[0] for item in tracked]
    smoothed_x = smooth_signal(
        interpolate_short_gaps([item[1] for item in tracked], max_gap=2),
        window_size=3,
    )

    direction_changes = 0
    direction = None

    for index in range(1, len(smoothed_x)):
        previous_value = smoothed_x[index - 1]
        current_value = smoothed_x[index]

        if previous_value is None or current_value is None:
            continue

        delta = current_value - previous_value

        if abs(delta) < MIN_LATERAL_MOVEMENT_FRACTION:
            continue

        current_direction = "right" if delta > 0 else "left"

        if direction is not None and current_direction != direction:
            direction_changes += 1

        direction = current_direction

    duration_s = (tracked_timestamps[-1] - tracked_timestamps[0]) / 1000.0

    if duration_s <= 0:
        return []

    changes_per_10s = direction_changes / duration_s * 10.0

    return [(
        "Agility",
        _bounded_score(changes_per_10s, DIRECTION_CHANGES_PER_10S_CEILING),
    )]
