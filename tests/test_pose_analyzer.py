from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pose_analyzer import (
    MediaPipePoseAnalyzer,
    PoseAnalysisError,
    landmark_to_dict,
    pose_detection_quality,
)


def test_landmark_to_dict():
    landmark = SimpleNamespace(
        x=0.1,
        y=0.2,
        z=-0.3,
        visibility=0.9,
        presence=0.8,
    )

    assert landmark_to_dict(landmark) == {
        "x": 0.1,
        "y": 0.2,
        "z": -0.3,
        "visibility": 0.9,
        "presence": 0.8,
    }


@pytest.mark.parametrize(
    "rate, expected",
    [(0.2, "low"), (0.5, "medium"), (0.79, "medium"), (0.8, "high")],
)
def test_pose_detection_quality(rate, expected):
    assert pose_detection_quality(rate) == expected


def test_pose_detection_quality_rejects_invalid_rate():
    with pytest.raises(ValueError, match="detection_rate"):
        pose_detection_quality(1.1)


def test_pose_analyzer_requires_model(tmp_path):
    analyzer = MediaPipePoseAnalyzer(
        model_path=tmp_path / "missing.task"
    )

    with pytest.raises(PoseAnalysisError, match="Pose model not found"):
        analyzer.analyze(
            video_path=Path("video.mp4"),
            progress_callback=lambda progress: None,
        )


def test_pose_analyzer_rejects_invalid_sample_interval(tmp_path):
    with pytest.raises(ValueError, match="sample_every_n_frames"):
        MediaPipePoseAnalyzer(
            model_path=tmp_path / "pose.task",
            sample_every_n_frames=0,
        )


def test_pose_analyzer_rejects_unsupported_movement_type(tmp_path):
    with pytest.raises(ValueError, match="Unsupported movement_type"):
        MediaPipePoseAnalyzer(
            model_path=tmp_path / "pose.task",
            movement_type="unknown_drill",
        )
