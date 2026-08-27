from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pose_analyzer import MediaPipePoseAnalyzer


def main() -> None:
    with TemporaryDirectory() as directory:
        video_path = Path(directory) / "pose-smoke-test.mp4"
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            10.0,
            (320, 240),
        )

        if not writer.isOpened():
            raise RuntimeError("Could not create smoke-test video")

        for _ in range(12):
            writer.write(
                np.zeros((240, 320, 3), dtype=np.uint8)
            )

        writer.release()
        progress = []
        result = MediaPipePoseAnalyzer(
            model_path=Path(
                "models/pose_landmarker_lite.task"
            ),
            model_version="pose-landmarker-lite-float16",
            sample_every_n_frames=3,
        ).analyze(video_path, progress.append)

        print(
            {
                "frames_analyzed": result["video"]["frames_analyzed"],
                "frames_with_pose": result["summary"][
                    "frames_with_pose"
                ],
                "detection_rate": result["summary"][
                    "detection_rate"
                ],
                "last_progress": progress[-1],
            }
        )


if __name__ == "__main__":
    main()
