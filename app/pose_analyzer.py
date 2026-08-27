from pathlib import Path

from app.agility_ladder_analysis import analyze_agility_ladder
from app.pose_features import extract_pose_features
from app.squat_jump_analysis import analyze_squat_jumps
from app.video_analysis_worker import ProgressCallback


class PoseAnalysisError(RuntimeError):
    pass


def pose_detection_quality(detection_rate: float) -> str:
    if not 0 <= detection_rate <= 1:
        raise ValueError("detection_rate must be between 0 and 1")
    if detection_rate >= 0.8:
        return "high"
    if detection_rate >= 0.5:
        return "medium"
    return "low"


def landmark_to_dict(landmark) -> dict:
    return {
        "x": landmark.x,
        "y": landmark.y,
        "z": landmark.z,
        "visibility": landmark.visibility,
        "presence": landmark.presence,
    }


class MediaPipePoseAnalyzer:
    model_name = "mediapipe_pose_landmarker"

    def __init__(
        self,
        model_path: Path,
        model_version: str = "pose-landmarker-1",
        sample_every_n_frames: int = 3,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        movement_type: str | None = None,
    ):
        if sample_every_n_frames <= 0:
            raise ValueError("sample_every_n_frames must be greater than 0")

        if movement_type not in {None, "squat_jump", "agility_ladder"}:
            raise ValueError("Unsupported movement_type")

        self.model_path = Path(model_path)
        self.model_version = model_version
        self.sample_every_n_frames = sample_every_n_frames
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self.movement_type = movement_type

    def analyze(
        self,
        video_path: Path,
        progress_callback: ProgressCallback,
    ) -> dict:
        if not self.model_path.is_file():
            raise PoseAnalysisError(
                f"Pose model not found: {self.model_path}"
            )

        if not Path(video_path).is_file():
            raise PoseAnalysisError(f"Video not found: {video_path}")

        try:
            import cv2
            import mediapipe as mp
        except ImportError as error:
            raise PoseAnalysisError(
                "Computer vision dependencies are not installed"
            ) from error

        capture = cv2.VideoCapture(str(video_path))

        if not capture.isOpened():
            raise PoseAnalysisError("Could not open the video")

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        image_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        image_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if fps <= 0:
            capture.release()
            raise PoseAnalysisError("Video frame rate is unavailable")

        if image_width <= 0 or image_height <= 0:
            capture.release()
            raise PoseAnalysisError("Video dimensions are unavailable")

        vision = mp.tasks.vision
        options = vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(self.model_path),
                delegate=mp.tasks.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=(
                self.min_detection_confidence
            ),
            min_pose_presence_confidence=(
                self.min_detection_confidence
            ),
            min_tracking_confidence=self.min_tracking_confidence,
            output_segmentation_masks=False,
        )

        frames_decoded = 0
        frames_analyzed = 0
        frames_with_pose = 0
        landmark_frames = []

        try:
            with vision.PoseLandmarker.create_from_options(
                options
            ) as landmarker:
                while True:
                    success, frame = capture.read()

                    if not success:
                        break

                    frame_index = frames_decoded
                    frames_decoded += 1

                    if frame_index % self.sample_every_n_frames != 0:
                        continue

                    rgb_frame = cv2.cvtColor(
                        frame,
                        cv2.COLOR_BGR2RGB,
                    )
                    image = mp.Image(
                        image_format=mp.ImageFormat.SRGB,
                        data=rgb_frame,
                    )
                    timestamp_ms = int((frame_index / fps) * 1000)
                    detection = landmarker.detect_for_video(
                        image,
                        timestamp_ms,
                    )
                    frames_analyzed += 1

                    if detection.pose_landmarks:
                        frames_with_pose += 1
                        frame_entry = {
                            "frame_index": frame_index,
                            "timestamp_ms": timestamp_ms,
                            "landmarks": [
                                landmark_to_dict(landmark)
                                for landmark
                                in detection.pose_landmarks[0]
                            ],
                        }

                        if detection.pose_world_landmarks:
                            frame_entry["world_landmarks"] = [
                                landmark_to_dict(landmark)
                                for landmark
                                in detection.pose_world_landmarks[0]
                            ]

                        landmark_frames.append(frame_entry)

                    if total_frames > 0:
                        progress_callback(
                            (frames_decoded / total_frames) * 100.0
                        )
        finally:
            capture.release()

        if frames_analyzed == 0:
            raise PoseAnalysisError("Video contains no analyzable frames")

        pose_features = extract_pose_features(
            landmark_frames=landmark_frames,
            image_width=image_width,
            image_height=image_height,
        )
        result = {
            "schema_version": "1.0",
            "analysis_type": "pose_estimation",
            "model_name": self.model_name,
            "model_version": self.model_version,
            "video": {
                "fps": fps,
                "total_frames": total_frames,
                "frames_decoded": frames_decoded,
                "frames_analyzed": frames_analyzed,
                "sample_every_n_frames": self.sample_every_n_frames,
                "image_width": image_width,
                "image_height": image_height,
            },
            "summary": {
                "frames_with_pose": frames_with_pose,
                "detection_rate": (
                    frames_with_pose / frames_analyzed
                ),
                "quality": pose_detection_quality(
                    frames_with_pose / frames_analyzed
                ),
            },
            "landmark_frames": landmark_frames,
            "features": pose_features,
        }

        if self.movement_type == "squat_jump":
            result["movement_analysis"] = analyze_squat_jumps(
                pose_features
            )

        if self.movement_type == "agility_ladder":
            result["movement_analysis"] = analyze_agility_ladder(
                pose_features
            )

        return result
