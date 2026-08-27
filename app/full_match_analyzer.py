from pathlib import Path
from typing import Callable

from app.match_analysis_engine import MatchTrackAccumulator
from app.video_analysis_worker import ProgressCallback


class FullMatchAnalysisError(RuntimeError):
    pass


CLASS_ALIASES = {
    "person": "player",
    "player": "player",
    "goalkeeper": "goalkeeper",
    "referee": "referee",
    "sports ball": "ball",
    "ball": "ball",
    "football": "ball",
}


def _video_metadata(video_path: Path) -> tuple[float, int]:
    try:
        import cv2
    except ImportError as error:
        raise FullMatchAnalysisError("OpenCV is not installed") from error
    capture = cv2.VideoCapture(str(video_path))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    capture.release()
    return fps, total_frames


def _load_yolo(model_path: Path):
    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise FullMatchAnalysisError(
            "Full-match analysis requires Ultralytics"
        ) from error
    return YOLO(str(model_path))


class FullMatchAnalyzer:
    model_name = "football_yolo_tracker"

    def __init__(
        self,
        model_path: Path,
        model_version: str = "football-yolo-1",
        sample_every_n_frames: int = 2,
        confidence_threshold: float = 0.35,
        image_size: int = 1280,
        tracker: str = "bytetrack.yaml",
        target_track_id: int | None = None,
        model_factory: Callable | None = None,
        video_metadata_reader: Callable | None = None,
    ):
        if sample_every_n_frames <= 0:
            raise ValueError("sample_every_n_frames must be greater than 0")
        if not 0 < confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if image_size <= 0:
            raise ValueError("image_size must be greater than 0")

        self.model_path = Path(model_path)
        self.model_version = model_version
        self.sample_every_n_frames = sample_every_n_frames
        self.confidence_threshold = confidence_threshold
        self.image_size = image_size
        self.tracker = tracker
        self.target_track_id = target_track_id
        self.model_factory = model_factory or _load_yolo
        self.video_metadata_reader = video_metadata_reader or _video_metadata

    def analyze(
        self,
        video_path: Path,
        progress_callback: ProgressCallback,
    ) -> dict:
        if not self.model_path.is_file():
            raise FullMatchAnalysisError(
                f"Football model not found: {self.model_path}"
            )
        if not Path(video_path).is_file():
            raise FullMatchAnalysisError(f"Video not found: {video_path}")

        fps, total_frames = self.video_metadata_reader(Path(video_path))

        if fps <= 0:
            raise FullMatchAnalysisError("Video frame rate is unavailable")

        model = self.model_factory(self.model_path)
        accumulator = MatchTrackAccumulator()
        sampled_index = 0

        try:
            results = model.track(
                source=str(video_path),
                stream=True,
                persist=True,
                tracker=self.tracker,
                conf=self.confidence_threshold,
                imgsz=self.image_size,
                vid_stride=self.sample_every_n_frames,
                verbose=False,
            )

            for result in results:
                frame_index = sampled_index * self.sample_every_n_frames
                sampled_index += 1
                height, width = result.orig_shape
                detections = []
                boxes = result.boxes

                if boxes is not None:
                    xyxy = boxes.xyxy.cpu().tolist()
                    classes = boxes.cls.cpu().tolist()
                    confidences = boxes.conf.cpu().tolist()
                    ids = (
                        boxes.id.int().cpu().tolist()
                        if boxes.id is not None else [None] * len(xyxy)
                    )

                    for bounds, class_id, confidence, track_id in zip(
                        xyxy, classes, confidences, ids
                    ):
                        raw_name = str(result.names[int(class_id)]).lower()
                        class_name = CLASS_ALIASES.get(raw_name)
                        if class_name is None:
                            continue
                        if class_name in {"player", "goalkeeper"} and track_id is None:
                            continue

                        x1, y1, x2, y2 = bounds
                        detections.append(
                            {
                                "frame_index": frame_index,
                                "timestamp_seconds": round(frame_index / fps, 4),
                                "track_id": int(track_id) if track_id is not None else -1,
                                "class_name": class_name,
                                "source_class_name": raw_name,
                                "confidence": round(float(confidence), 4),
                                "bbox": [
                                    round(x1 / width, 5), round(y1 / height, 5),
                                    round(x2 / width, 5), round(y2 / height, 5),
                                ],
                                "center": [
                                    round(((x1 + x2) / 2) / width, 5),
                                    round(((y1 + y2) / 2) / height, 5),
                                ],
                            }
                        )

                accumulator.add_frame(
                    {
                        "frame_index": frame_index,
                        "timestamp_seconds": round(frame_index / fps, 4),
                        "detections": detections,
                    }
                )
                if total_frames > 0:
                    progress_callback(min(frame_index / total_frames * 100, 99))
        except Exception as error:
            raise FullMatchAnalysisError(
                f"Football tracking failed: {error}"
            ) from error

        if sampled_index == 0:
            raise FullMatchAnalysisError("Video contains no analyzable frames")

        analytics = accumulator.finalize(
            target_track_id=self.target_track_id,
        )
        progress_callback(100.0)

        return {
            "schema_version": "1.0",
            "analysis_type": "full_match",
            "model_name": self.model_name,
            "model_version": self.model_version,
            "video": {
                "fps": fps,
                "total_frames": total_frames,
                "sampled_frames": sampled_index,
                "sample_every_n_frames": self.sample_every_n_frames,
            },
            **analytics,
        }
