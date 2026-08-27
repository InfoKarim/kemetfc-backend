from pathlib import Path

import pytest

from app.full_match_analyzer import FullMatchAnalysisError, FullMatchAnalyzer


class Tensor:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def int(self):
        return self

    def tolist(self):
        return self.values


class Boxes:
    def __init__(self, xyxy):
        self.xyxy = Tensor(xyxy)
        self.cls = Tensor([0, 1])
        self.conf = Tensor([0.9, 0.8])
        self.id = Tensor([7, None])


class Result:
    names = {0: "player", 1: "sports ball"}
    orig_shape = (100, 200)

    def __init__(self, player_x, ball_x):
        self.boxes = Boxes([
            [player_x, 20, player_x + 20, 80],
            [ball_x, 45, ball_x + 4, 49],
        ])


class FakeModel:
    def track(self, **kwargs):
        assert kwargs["stream"] is True
        assert kwargs["persist"] is True
        return iter([Result(20, 28), Result(40, 48)])


def test_full_match_analyzer_requires_model(tmp_path):
    analyzer = FullMatchAnalyzer(model_path=tmp_path / "football.pt")

    with pytest.raises(FullMatchAnalysisError, match="model not found"):
        analyzer.analyze(Path("match.mp4"), lambda value: None)


def test_full_match_analyzer_validates_sampling(tmp_path):
    with pytest.raises(ValueError, match="sample_every_n_frames"):
        FullMatchAnalyzer(
            model_path=tmp_path / "football.pt",
            sample_every_n_frames=0,
        )


def test_full_match_analyzer_streams_tracks_into_metrics(tmp_path):
    model_path = tmp_path / "football.pt"
    video_path = tmp_path / "match.mp4"
    model_path.write_bytes(b"model")
    video_path.write_bytes(b"video")
    progress = []
    analyzer = FullMatchAnalyzer(
        model_path=model_path,
        target_track_id=7,
        model_factory=lambda path: FakeModel(),
        video_metadata_reader=lambda path: (10.0, 4),
    )

    result = analyzer.analyze(video_path, progress.append)

    assert result["analysis_type"] == "full_match"
    assert result["summary"]["sampled_frames"] == 2
    assert result["target_player"]["track_id"] == 7
    assert result["target_player"]["frames_tracked"] == 2
    assert progress[-1] == 100.0


def test_full_match_analyzer_wraps_tracker_failure(tmp_path):
    class BrokenModel:
        def track(self, **kwargs):
            raise RuntimeError("bad checkpoint")

    model_path = tmp_path / "football.pt"
    video_path = tmp_path / "match.mp4"
    model_path.write_bytes(b"model")
    video_path.write_bytes(b"video")
    analyzer = FullMatchAnalyzer(
        model_path=model_path,
        model_factory=lambda path: BrokenModel(),
        video_metadata_reader=lambda path: (30.0, 10),
    )

    with pytest.raises(FullMatchAnalysisError, match="tracking failed"):
        analyzer.analyze(video_path, lambda value: None)
