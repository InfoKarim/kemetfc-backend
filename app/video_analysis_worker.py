import logging
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, Iterator, Protocol

from sqlalchemy.orm import Session

from app.analysis_result_storage import get_analysis_result_storage
from app.data_models import VideoAnalysisJobData, VideoData
from app.video_storage import get_video_storage
from app.services.video_analysis_job_service import (
    VideoAnalysisJobService,
)
from app.services.video_service import VideoService
from app.video_analysis_publication import VideoAnalysisPublisher


ProgressCallback = Callable[[float], None]

logger = logging.getLogger(__name__)
VideoPathResolver = Callable[[VideoData], Path]


class VideoAnalyzer(Protocol):
    model_name: str
    model_version: str

    def analyze(
        self,
        video_path: Path,
        progress_callback: ProgressCallback,
    ) -> dict:
        ...


def resolve_analysis_result_path(
    job_id: str,
    result_path: str | None,
) -> Path:
    if not result_path:
        raise FileNotFoundError("Analysis result is unavailable")

    output_dir = Path(
        os.getenv("VIDEO_ANALYSIS_OUTPUT_DIR", "analysis/results")
    ).resolve()
    target = Path(result_path).resolve()
    expected = (output_dir / f"{job_id}.json").resolve()

    if target != expected or not target.is_file():
        raise FileNotFoundError("Analysis result is unavailable")

    return target


@contextmanager
def materialize_video_path(video: VideoData) -> Iterator[Path]:
    direct_path = Path(video.file_path)

    if direct_path.is_file():
        yield direct_path
        return

    if video.file_path.startswith("/uploads/videos/"):
        with get_video_storage().materialize(direct_path.name) as path:
            yield path
        return

    raise FileNotFoundError(
        f"Video file is unavailable: {video.file_path}"
    )


class VideoAnalysisWorker:
    def __init__(
        self,
        db: Session,
        analyzer: VideoAnalyzer,
        output_dir: Path | None = None,
        video_path_resolver: VideoPathResolver | None = None,
    ):
        self.db = db
        self.analyzer = analyzer
        self.job_service = VideoAnalysisJobService(db=db)
        self.video_service = VideoService(db=db)
        self.publisher = VideoAnalysisPublisher(db=db)
        self.result_storage = get_analysis_result_storage(
            local_directory=output_dir
        )
        self.output_dir = output_dir or Path(
            os.getenv(
                "VIDEO_ANALYSIS_OUTPUT_DIR",
                "analysis/results",
            )
        )
        self.video_path_resolver = video_path_resolver

    def process_next_job(self) -> VideoAnalysisJobData | None:
        job = self.job_service.claim_next_queued_job()

        if job is None:
            return None

        return self.process_claimed_job(job)

    def process_job(self, job_id: str) -> VideoAnalysisJobData:
        job = self.job_service.claim_job(job_id)

        if job is None:
            existing = self.job_service.get_job(job_id)

            if existing is None:
                raise ValueError("Analysis job not found")
            raise ValueError("Only queued analysis jobs can be processed")

        return self.process_claimed_job(job)

    def process_claimed_job(
        self,
        job: VideoAnalysisJobData,
    ) -> VideoAnalysisJobData:
        if job.status != "processing":
            raise ValueError("Analysis job must be claimed before processing")

        video = self.video_service.get_video(job.video_id)

        if video is None:
            raise ValueError("Video not found")

        job_id = job.job_id
        self.job_service.transition_job(
            job_id=job_id,
            status="processing",
            progress_percent=0.0,
            model_name=self.analyzer.model_name,
            model_version=self.analyzer.model_version,
        )

        try:
            path_context = (
                nullcontext(self.video_path_resolver(video))
                if self.video_path_resolver is not None
                else materialize_video_path(video)
            )

            with path_context as video_path:
                def report_progress(value: float) -> None:
                    self.job_service.transition_job(
                        job_id=job_id,
                        status="processing",
                        progress_percent=min(max(value, 0.0), 99.0),
                    )

                result = self.analyzer.analyze(
                    video_path=video_path,
                    progress_callback=report_progress,
                )
            result_path = self._write_result(job_id, result)

            self.publisher.publish(
                job_id=job_id,
                video_id=job.video_id,
                analysis_type=job.analysis_type,
                model_name=self.analyzer.model_name,
                model_version=self.analyzer.model_version,
                result=result,
                result_path=result_path,
            )

            completed = self.job_service.transition_job(
                job_id=job_id,
                status="completed",
                result_path=result_path,
            )
        except Exception as error:
            logger.exception("analysis_job_processing_failed job_id=%s", job_id)
            self.db.rollback()
            self.publisher.mark_video_failed(job.video_id, str(error))
            failed = self.job_service.transition_job(
                job_id=job_id,
                status="failed",
                error_message=str(error),
            )
            if failed is None:
                raise RuntimeError("Could not mark analysis job as failed")
            return failed

        if completed is None:
            raise RuntimeError("Analysis job disappeared during processing")

        return completed

    def _write_result(self, job_id: str, result: dict) -> str:
        return self.result_storage.save(job_id, result)
