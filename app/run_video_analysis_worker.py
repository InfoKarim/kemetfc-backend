import argparse
import logging
import os
import time
from pathlib import Path

from app.database import SessionLocal
from app.db_models import DataRecordDB, UserDB, VideoDB
from app.full_match_analyzer import FullMatchAnalyzer
from app.pose_analyzer import MediaPipePoseAnalyzer
from app.services.notification_service import NotificationService
from app.services.video_analysis_job_service import VideoAnalysisJobService
from app.video_analysis_worker import VideoAnalysisWorker


def notify_job_owner(db, job_id: str, video_id: str, status: str) -> None:
    """Best-effort notification to whoever uploaded the source video."""
    video = db.get(VideoDB, video_id)
    if video is None:
        return

    record = db.get(DataRecordDB, video.record_id)
    if record is None or not record.created_by:
        return

    user = db.get(UserDB, record.created_by)
    if user is None:
        return

    NotificationService(db=db).create_notification(
        user_id=user.user_id,
        type="analysis_job",
        title=(
            "Video analysis completed"
            if status == "completed"
            else "Video analysis failed"
        ),
        body=f"Job {job_id} for video {video_id} finished with status: {status}.",
        link=f"/video-analysis-details?job_id={job_id}",
    )


MOVEMENT_ANALYSIS_TYPES = {"squat_jump", "agility_ladder"}


def select_movement_type(
    analysis_type: str,
    override: str | None = None,
) -> str | None:
    if override:
        return override

    return analysis_type if analysis_type in MOVEMENT_ANALYSIS_TYPES else None


def build_analyzer(job):
    if job.analysis_type == "full_match":
        model_path = os.getenv("FOOTBALL_DETECTION_MODEL_PATH")
        if not model_path:
            raise RuntimeError("FOOTBALL_DETECTION_MODEL_PATH is required")
        return FullMatchAnalyzer(
            model_path=Path(model_path),
            model_version=os.getenv(
                "FOOTBALL_MODEL_VERSION", "football-yolo-1"
            ),
            sample_every_n_frames=int(
                os.getenv("MATCH_SAMPLE_EVERY_N_FRAMES", "2")
            ),
            confidence_threshold=float(
                os.getenv("MATCH_DETECTION_CONFIDENCE", "0.35")
            ),
            image_size=int(os.getenv("MATCH_IMAGE_SIZE", "1280")),
            tracker=os.getenv("MATCH_TRACKER", "bytetrack.yaml"),
            target_track_id=job.target_track_id,
        )

    model_path = os.getenv("POSE_LANDMARKER_MODEL_PATH")
    if not model_path:
        raise RuntimeError("POSE_LANDMARKER_MODEL_PATH is required")
    movement_type = select_movement_type(
        analysis_type=job.analysis_type,
        override=os.getenv("POSE_MOVEMENT_TYPE"),
    )
    return MediaPipePoseAnalyzer(
        model_path=Path(model_path),
        model_version=os.getenv(
            "POSE_LANDMARKER_MODEL_VERSION", "pose-landmarker-1"
        ),
        sample_every_n_frames=int(
            os.getenv("POSE_SAMPLE_EVERY_N_FRAMES", "3")
        ),
        movement_type=movement_type,
    )


def process_one() -> bool:
    db = SessionLocal()

    try:
        job = VideoAnalysisJobService(db=db).claim_next_queued_job()

        if job is None:
            return False

        analyzer = build_analyzer(job)
        result = VideoAnalysisWorker(
            db=db,
            analyzer=analyzer,
        ).process_claimed_job(job)

        logging.getLogger(__name__).info(
            "analysis_job_finished job_id=%s status=%s",
            result.job_id,
            result.status,
        )
        notify_job_owner(db, result.job_id, result.video_id, result.status)
        return True
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Kemet FC video-analysis worker.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one queued job and exit.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("ANALYSIS_WORKER_POLL_SECONDS", "2")),
    )
    args = parser.parse_args()

    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be greater than zero")

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ),
    )

    if not (
        os.getenv("POSE_LANDMARKER_MODEL_PATH")
        or os.getenv("FOOTBALL_DETECTION_MODEL_PATH")
    ):
        logging.getLogger(__name__).error(
            "A pose or football model path is required"
        )
        return 2

    if args.once:
        processed = process_one()
        if not processed:
            logging.getLogger(__name__).info("no_queued_analysis_jobs")
        return 0

    logger = logging.getLogger(__name__)
    logger.info("analysis_worker_started poll_seconds=%s", args.poll_seconds)

    while True:
        try:
            if not process_one():
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            logger.info("analysis_worker_stopped")
            return 0
        except Exception:
            logger.exception("analysis_worker_iteration_failed")
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
