import pytest

from app.match_analysis_engine import analyze_match_tracks


def detection(frame, timestamp, track_id, label, x, y, confidence=0.9):
    return {
        "frame_index": frame,
        "timestamp_seconds": timestamp,
        "track_id": track_id,
        "class_name": label,
        "confidence": confidence,
        "bbox": [x - 0.02, y - 0.04, x + 0.02, y + 0.04],
        "center": [x, y],
    }


def test_match_tracks_detect_possession_change_as_pass_candidate():
    frames = [
        {"frame_index": 0, "timestamp_seconds": 0.0, "detections": [
            detection(0, 0.0, 10, "player", 0.20, 0.50),
            detection(0, 0.0, 20, "player", 0.70, 0.50),
            detection(0, 0.0, 1, "ball", 0.21, 0.52),
        ]},
        {"frame_index": 10, "timestamp_seconds": 1.0, "detections": [
            detection(10, 1.0, 10, "player", 0.25, 0.50),
            detection(10, 1.0, 20, "player", 0.65, 0.50),
            detection(10, 1.0, 1, "ball", 0.64, 0.52),
        ]},
    ]

    result = analyze_match_tracks(frames, total_sampled_frames=2)

    assert result["summary"]["players_tracked"] == 2
    assert result["summary"]["ball_detection_rate"] == 1.0
    assert result["events"][0]["event_type"] == "pass_candidate"
    assert result["events"][0]["from_track_id"] == 10
    assert result["events"][0]["to_track_id"] == 20


def test_match_tracks_build_target_player_metrics():
    frames = [
        {"frame_index": 0, "timestamp_seconds": 0.0, "detections": [
            detection(0, 0.0, 7, "player", 0.10, 0.50),
            detection(0, 0.0, 1, "ball", 0.11, 0.51),
        ]},
        {"frame_index": 10, "timestamp_seconds": 1.0, "detections": [
            detection(10, 1.0, 7, "player", 0.30, 0.50),
            detection(10, 1.0, 1, "ball", 0.31, 0.51),
        ]},
    ]

    result = analyze_match_tracks(
        frames,
        total_sampled_frames=2,
        target_track_id=7,
    )

    target = result["target_player"]
    assert target["track_id"] == 7
    assert target["frames_tracked"] == 2
    assert target["ball_control_samples"] == 2
    assert target["normalized_distance"] == pytest.approx(0.2)


def test_match_tracks_rejects_unknown_target():
    with pytest.raises(ValueError, match="Target player track"):
        analyze_match_tracks(
            [{"frame_index": 0, "timestamp_seconds": 0, "detections": []}],
            total_sampled_frames=1,
            target_track_id=99,
        )


def test_match_tracks_abstains_when_video_evidence_is_missing():
    result = analyze_match_tracks(
        [{"frame_index": 0, "timestamp_seconds": 0, "detections": []}],
        total_sampled_frames=1,
    )

    assert result["quality_control"]["abstained"] is True
    assert result["quality_control"]["score_generation_allowed"] is False
    assert "player_detection_rate_below_threshold" in result[
        "quality_control"
    ]["reasons"]
    assert result["summary"]["confidence_semantics"] == "heuristic_unvalidated"


def test_match_tracks_quality_thresholds_are_configurable():
    result = analyze_match_tracks(
        [{"frame_index": 0, "timestamp_seconds": 0, "detections": []}],
        total_sampled_frames=1,
        minimum_player_detection_rate=0,
        minimum_ball_detection_rate=0,
        minimum_mean_confidence=0,
    )

    assert result["quality_control"]["abstained"] is False
