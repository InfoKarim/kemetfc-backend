import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402

from app.video_frame_sampling import (  # noqa: E402
    VideoFrameSamplingError,
    sample_frames_as_base64_jpeg,
)


def make_test_video(path, seconds: float = 4.0, fps: float = 10.0):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (64, 48))
    frame_count = int(seconds * fps)

    for i in range(frame_count):
        frame = np.full((48, 64, 3), i % 256, dtype=np.uint8)
        writer.write(frame)

    writer.release()


def test_sample_frames_returns_requested_count(tmp_path):
    video_path = tmp_path / "clip.mp4"
    make_test_video(video_path)

    frames = sample_frames_as_base64_jpeg(video_path, max_frames=5)

    assert len(frames) == 5
    assert all(isinstance(frame, str) and frame for frame in frames)


def test_sample_frames_caps_at_available_frame_count(tmp_path):
    video_path = tmp_path / "short.mp4"
    make_test_video(video_path, seconds=0.3, fps=10.0)

    frames = sample_frames_as_base64_jpeg(video_path, max_frames=8)

    assert 0 < len(frames) <= 8


def test_sample_frames_rejects_missing_file(tmp_path):
    with pytest.raises(VideoFrameSamplingError):
        sample_frames_as_base64_jpeg(tmp_path / "nope.mp4")


def test_sample_frames_rejects_non_video_file(tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"not a real video file")

    with pytest.raises(VideoFrameSamplingError):
        sample_frames_as_base64_jpeg(bogus)
