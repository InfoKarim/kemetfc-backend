import base64
from pathlib import Path

# Claude has no native video input, so an assessment clip is judged from a
# handful of still frames rather than continuous motion. More frames means
# a better (but costlier, slower) read; this cap keeps a single analysis
# request small and bounded regardless of clip length.
MAX_FRAMES = 8
MAX_VIDEO_SECONDS = 300


class VideoFrameSamplingError(ValueError):
    pass


def sample_frames_as_base64_jpeg(
    video_path: Path,
    max_frames: int = MAX_FRAMES,
) -> list[str]:
    """Pick `max_frames` evenly spaced frames from the clip (skipping the
    very start/end to avoid black lead-in/out) and return each as a
    base64-encoded JPEG, ready for a Claude vision message.
    """
    try:
        import cv2
    except ImportError as error:
        raise VideoFrameSamplingError(
            "Computer vision dependencies are not installed"
        ) from error

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise VideoFrameSamplingError("Could not read this video file")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if fps <= 0 or total_frames <= 0:
            raise VideoFrameSamplingError("Video has no readable frames")

        duration_seconds = total_frames / fps

        if duration_seconds > MAX_VIDEO_SECONDS:
            raise VideoFrameSamplingError(
                f"Video is longer than the {MAX_VIDEO_SECONDS // 60}-minute "
                "limit for AI analysis"
            )

        sample_count = min(max_frames, total_frames)
        margin = total_frames * 0.05
        positions = [
            int(margin + (total_frames - 2 * margin) * i / max(sample_count - 1, 1))
            for i in range(sample_count)
        ]

        frames_base64 = []
        for position in positions:
            capture.set(cv2.CAP_PROP_POS_FRAMES, position)
            success, frame = capture.read()
            if not success:
                continue
            encoded, buffer = cv2.imencode(".jpg", frame)
            if not encoded:
                continue
            frames_base64.append(base64.b64encode(buffer).decode("ascii"))

        if not frames_base64:
            raise VideoFrameSamplingError("Could not extract any frames from this video")

        return frames_base64
    finally:
        capture.release()
