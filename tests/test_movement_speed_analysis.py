import pytest

from app.movement_speed_analysis import estimate_agility, estimate_speed_and_acceleration


IMAGE_WIDTH = 100
IMAGE_HEIGHT = 100
PLAYER_HEIGHT_CM = 150.0


def make_frame(timestamp_ms, hip_x_normalized, hip_y_normalized=0.5, body_height_pixels=150.0):
    return {
        "timestamp_ms": timestamp_ms,
        "measurements": {
            "body_height_pixels": body_height_pixels,
            "hip_center_x_normalized": hip_x_normalized,
            "hip_center_y_normalized": hip_y_normalized,
        },
    }


def test_estimate_speed_from_constant_lateral_movement():
    # body_height_pixels=150 with a real height of 150cm -> 1 pixel == 1 cm.
    # Hip moves 5 pixels (5cm) every 100ms across 6 frames -> constant 0.5 m/s.
    frames = [
        make_frame(index * 100, 0.05 * index) for index in range(6)
    ]

    attributes = estimate_speed_and_acceleration(
        frames, IMAGE_WIDTH, IMAGE_HEIGHT, PLAYER_HEIGHT_CM
    )
    scores = dict(attributes)

    # 0.5 m/s against a 6.0 m/s ceiling.
    assert scores["Speed"] == pytest.approx(0.5 / 6.0 * 100, abs=0.1)
    # Constant speed -> zero acceleration.
    assert scores["Acceleration"] == pytest.approx(0.0, abs=0.1)


def test_estimate_speed_caps_at_100_for_very_fast_movement():
    # Full frame width crossed every 50ms is far beyond the 6 m/s ceiling.
    frames = [
        make_frame(index * 50, min(0.99, 0.5 * index)) for index in range(6)
    ]

    attributes = estimate_speed_and_acceleration(
        frames, IMAGE_WIDTH, IMAGE_HEIGHT, PLAYER_HEIGHT_CM
    )
    scores = dict(attributes)

    assert scores["Speed"] == 100.0


def test_estimate_speed_returns_nothing_without_player_height():
    frames = [make_frame(index * 100, 0.05 * index) for index in range(6)]

    assert estimate_speed_and_acceleration(frames, IMAGE_WIDTH, IMAGE_HEIGHT, None) == []
    assert estimate_speed_and_acceleration(frames, IMAGE_WIDTH, IMAGE_HEIGHT, 0) == []


def test_estimate_speed_returns_nothing_with_too_few_tracked_frames():
    frames = [make_frame(0, 0.5), make_frame(100, 0.55)]

    assert estimate_speed_and_acceleration(
        frames, IMAGE_WIDTH, IMAGE_HEIGHT, PLAYER_HEIGHT_CM
    ) == []


def test_estimate_speed_skips_frames_missing_measurements():
    # 8 frames with frame 3 broken leaves pairs (0,1) (1,2) (4,5) (5,6) (6,7)
    # -- 5 valid pairs, exactly enough to clear MIN_TRACKED_SAMPLES.
    frames = [make_frame(index * 100, 0.05 * index) for index in range(8)]
    frames[3]["measurements"] = {}

    attributes = estimate_speed_and_acceleration(
        frames, IMAGE_WIDTH, IMAGE_HEIGHT, PLAYER_HEIGHT_CM
    )

    assert dict(attributes)["Speed"] == pytest.approx(0.5 / 6.0 * 100, abs=0.1)


def test_estimate_agility_finds_no_direction_changes_in_monotonic_movement():
    frames = [make_frame(index * 100, 0.05 * index) for index in range(8)]

    attributes = estimate_agility(frames)

    assert dict(attributes)["Agility"] == pytest.approx(0.0, abs=0.1)


def test_estimate_agility_detects_oscillating_movement():
    xs = [0.3, 0.3, 0.3, 0.7, 0.7, 0.7, 0.3, 0.3, 0.3, 0.7, 0.7, 0.7]
    frames = [make_frame(index * 100, x) for index, x in enumerate(xs)]

    attributes = estimate_agility(frames)

    assert dict(attributes)["Agility"] > 0.0


def test_estimate_agility_returns_nothing_with_too_few_tracked_frames():
    frames = [make_frame(0, 0.5), make_frame(100, 0.6)]

    assert estimate_agility(frames) == []
