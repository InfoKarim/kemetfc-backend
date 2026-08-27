from types import SimpleNamespace

import pytest

from app.full_match_analyzer import FullMatchAnalyzer
from app.run_video_analysis_worker import build_analyzer, select_movement_type


def test_worker_selects_supported_analysis_type():
    assert select_movement_type("agility_ladder") == "agility_ladder"
    assert select_movement_type("squat_jump") == "squat_jump"


def test_worker_does_not_treat_pose_estimation_as_movement():
    assert select_movement_type("pose_estimation") is None


def test_worker_allows_explicit_movement_override():
    assert select_movement_type(
        "pose_estimation",
        override="agility_ladder",
    ) == "agility_ladder"


def test_worker_builds_full_match_analyzer(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DETECTION_MODEL_PATH", "models/football.pt")
    job = SimpleNamespace(analysis_type="full_match", target_track_id=27)

    analyzer = build_analyzer(job)

    assert isinstance(analyzer, FullMatchAnalyzer)
    assert analyzer.target_track_id == 27


def test_worker_requires_football_model(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DETECTION_MODEL_PATH", raising=False)
    job = SimpleNamespace(analysis_type="full_match", target_track_id=None)

    with pytest.raises(RuntimeError, match="FOOTBALL_DETECTION_MODEL_PATH"):
        build_analyzer(job)
