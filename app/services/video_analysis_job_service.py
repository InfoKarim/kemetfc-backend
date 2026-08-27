from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.data_models import VideoAnalysisJobData
from app.db_models import VideoAnalysisJobDB


class VideoAnalysisJobService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, job: VideoAnalysisJobData) -> VideoAnalysisJobDB:
        return VideoAnalysisJobDB(**job.__dict__)

    def _to_domain(self, job: VideoAnalysisJobDB) -> VideoAnalysisJobData:
        return VideoAnalysisJobData(
            job_id=job.job_id,
            video_id=job.video_id,
            analysis_type=job.analysis_type,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            progress_percent=job.progress_percent,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            model_name=job.model_name,
            model_version=job.model_version,
            result_path=job.result_path,
            error_message=job.error_message,
            review_status=job.review_status,
            reviewed_by=job.reviewed_by,
            reviewed_at=job.reviewed_at,
            review_notes=job.review_notes,
            target_track_id=job.target_track_id,
        )

    def add_job(self, job: VideoAnalysisJobData) -> None:
        self.db.add(self._to_db(job))
        self.db.commit()

    def get_job(self, job_id: str) -> VideoAnalysisJobData | None:
        job = self.db.get(VideoAnalysisJobDB, job_id)
        return None if job is None else self._to_domain(job)

    def get_video_jobs(self, video_id: str) -> list[VideoAnalysisJobData]:
        jobs = (
            self.db.query(VideoAnalysisJobDB)
            .filter(VideoAnalysisJobDB.video_id == video_id)
            .order_by(VideoAnalysisJobDB.created_at.desc())
            .all()
        )
        return [self._to_domain(job) for job in jobs]

    def claim_next_queued_job(self) -> VideoAnalysisJobData | None:
        candidate_id = (
            select(VideoAnalysisJobDB.job_id)
            .where(
                VideoAnalysisJobDB.status == "queued",
                VideoAnalysisJobDB.attempt_count
                < VideoAnalysisJobDB.max_attempts,
            )
            .order_by(VideoAnalysisJobDB.created_at.asc())
            .limit(1)
            .scalar_subquery()
        )
        return self._claim_where(
            VideoAnalysisJobDB.job_id == candidate_id
        )

    def claim_job(self, job_id: str) -> VideoAnalysisJobData | None:
        return self._claim_where(VideoAnalysisJobDB.job_id == job_id)

    def _claim_where(self, criterion) -> VideoAnalysisJobData | None:
        claimed_id = self.db.execute(
            update(VideoAnalysisJobDB)
            .where(
                criterion,
                VideoAnalysisJobDB.status == "queued",
                VideoAnalysisJobDB.attempt_count
                < VideoAnalysisJobDB.max_attempts,
            )
            .values(
                status="processing",
                started_at=datetime.now(),
                completed_at=None,
                progress_percent=0.0,
                attempt_count=VideoAnalysisJobDB.attempt_count + 1,
                error_message=None,
            )
            .returning(VideoAnalysisJobDB.job_id)
        ).scalar_one_or_none()
        self.db.commit()

        if claimed_id is None:
            return None

        return self.get_job(claimed_id)

    def transition_job(
        self,
        job_id: str,
        status: str,
        progress_percent: float | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        result_path: str | None = None,
        error_message: str | None = None,
    ) -> VideoAnalysisJobData | None:
        job = self.db.get(VideoAnalysisJobDB, job_id)

        if job is None:
            return None

        allowed_transitions = {
            "queued": {"processing", "cancelled"},
            "processing": {
                "processing",
                "completed",
                "failed",
                "cancelled",
            },
            "failed": {"queued"},
            "completed": set(),
            "cancelled": set(),
        }

        if status not in allowed_transitions[job.status]:
            raise ValueError(
                f"Cannot transition job from {job.status} to {status}"
            )

        now = datetime.now()

        if status == "processing" and job.status == "queued":
            if job.attempt_count >= job.max_attempts:
                raise ValueError("Analysis job has no attempts remaining")
            job.started_at = now
            job.attempt_count += 1
            job.error_message = None

        if status == "queued":
            if job.attempt_count >= job.max_attempts:
                raise ValueError("Analysis job has no attempts remaining")
            job.started_at = None
            job.completed_at = None
            job.progress_percent = 0.0
            job.error_message = None
            job.result_path = None

        if progress_percent is not None:
            job.progress_percent = progress_percent

        if model_name is not None:
            job.model_name = model_name

        if model_version is not None:
            job.model_version = model_version

        if status == "completed":
            job.completed_at = now
            job.progress_percent = 100.0
            job.result_path = result_path

        if status == "failed":
            if not error_message:
                raise ValueError("error_message is required when failed")
            job.completed_at = now
            job.error_message = error_message

        if status == "cancelled":
            job.completed_at = now

        job.status = status
        domain_job = self._to_domain(job)
        self.db.commit()
        return domain_job

    def review_job(
        self,
        job_id: str,
        review_status: str,
        reviewed_by: str,
        review_notes: str | None = None,
    ) -> VideoAnalysisJobData | None:
        job = self.db.get(VideoAnalysisJobDB, job_id)

        if job is None:
            return None

        if job.status != "completed":
            raise ValueError("Only completed analysis jobs can be reviewed")

        if review_status not in {"approved", "rejected"}:
            raise ValueError("Invalid analysis review status")

        if not reviewed_by or not reviewed_by.strip():
            raise ValueError("reviewed_by is required")

        job.review_status = review_status
        job.reviewed_by = reviewed_by.strip()
        job.reviewed_at = datetime.now()
        job.review_notes = review_notes
        domain_job = self._to_domain(job)
        self.db.commit()
        return domain_job
