from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.analysis_result_storage import get_analysis_result_storage
from app.db_models import (
    AnalysisDB,
    DataRecordDB,
    TrainingPlanDB,
    VideoAnalysisJobDB,
    VideoDB,
)
from app.data_models import VideoData
from app.video_storage import get_video_storage


class VideoDeletionError(ValueError):
    pass


class VideoService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, video: VideoData) -> VideoDB:
        return VideoDB(
            video_id=video.video_id,
            record_id=video.record_id,
            video_type=video.video_type,
            duration_seconds=video.duration_seconds,
            recorded_at=video.recorded_at,
            session_id=video.session_id,
            location_id=video.location_id,
            capture_device=video.capture_device,
            resolution=video.resolution,
            frame_rate_fps=video.frame_rate_fps,
            file_size_mb=video.file_size_mb,
            file_format=video.file_format,
            file_path=video.file_path,
            checksum=video.checksum,
            original_preserved=video.original_preserved,
            ai_processing_status=video.ai_processing_status,
            ai_processed_at=video.ai_processed_at,
            ai_model_version=video.ai_model_version,
            ai_confidence_score=video.ai_confidence_score,
            requires_human_review=video.requires_human_review,
            review_reason=video.review_reason,
            human_review_status=video.human_review_status,
            reviewed_by=video.reviewed_by,
            reviewed_at=video.reviewed_at,
            review_notes=video.review_notes,
            analysis_approved=video.analysis_approved,
            approved_by=video.approved_by,
            approved_at=video.approved_at,
        )

    def _to_domain(self, db_video: VideoDB) -> VideoData:
        return VideoData(
            video_id=db_video.video_id,
            record_id=db_video.record_id,
            video_type=db_video.video_type,
            duration_seconds=db_video.duration_seconds,
            recorded_at=db_video.recorded_at,
            session_id=db_video.session_id,
            location_id=db_video.location_id,
            capture_device=db_video.capture_device,
            resolution=db_video.resolution,
            frame_rate_fps=db_video.frame_rate_fps,
            file_size_mb=db_video.file_size_mb,
            file_format=db_video.file_format,
            file_path=db_video.file_path,
            checksum=db_video.checksum,
            original_preserved=db_video.original_preserved,
            ai_processing_status=db_video.ai_processing_status,
            ai_processed_at=db_video.ai_processed_at,
            ai_model_version=db_video.ai_model_version,
            ai_confidence_score=db_video.ai_confidence_score,
            requires_human_review=db_video.requires_human_review,
            review_reason=db_video.review_reason,
            human_review_status=db_video.human_review_status,
            reviewed_by=db_video.reviewed_by,
            reviewed_at=db_video.reviewed_at,
            review_notes=db_video.review_notes,
            analysis_approved=db_video.analysis_approved,
            approved_by=db_video.approved_by,
            approved_at=db_video.approved_at,
        )

    def add_video(self, video: VideoData) -> None:
        self.db.merge(self._to_db(video))
        self.db.commit()

    def get_video(self, video_id: str) -> VideoData | None:
        db_video = self.db.get(VideoDB, video_id)

        if db_video is None:
            return None

        return self._to_domain(db_video)

    def get_all_videos(self) -> list[VideoData]:
        db_videos = self.db.query(VideoDB).all()
        return [
            self._to_domain(video)
            for video in db_videos
        ]

    def delete_video(self, video_id: str) -> bool:
        db_video = self.db.get(VideoDB, video_id)

        if db_video is None:
            return False

        jobs = (
            self.db.query(VideoAnalysisJobDB)
            .filter(VideoAnalysisJobDB.video_id == video_id)
            .all()
        )
        if any(job.status == "processing" for job in jobs):
            raise VideoDeletionError(
                "Video cannot be deleted while analysis is processing"
            )

        analysis_ids = [
            analysis_id for (analysis_id,) in (
                self.db.query(AnalysisDB.analysis_id)
                .filter(AnalysisDB.video_id == video_id)
                .all()
            )
        ]

        try:
            jobs_with_results = [job for job in jobs if job.result_path]
            if jobs_with_results:
                result_storage = get_analysis_result_storage()
                for job in jobs_with_results:
                    result_storage.delete(job.job_id, job.result_path)

            if db_video.file_path.startswith("/uploads/videos/"):
                get_video_storage().delete(
                    db_video.file_path.rsplit("/", 1)[-1]
                )

            if analysis_ids:
                self.db.query(TrainingPlanDB).filter(
                    TrainingPlanDB.analysis_id.in_(analysis_ids)
                ).delete(synchronize_session=False)
                self.db.query(AnalysisDB).filter(
                    AnalysisDB.analysis_id.in_(analysis_ids)
                ).delete(synchronize_session=False)

            self.db.query(VideoAnalysisJobDB).filter(
                VideoAnalysisJobDB.video_id == video_id
            ).delete(synchronize_session=False)

            record_id = db_video.record_id
            self.db.delete(db_video)
            self.db.flush()
            remaining_videos = (
                self.db.query(VideoDB)
                .filter(VideoDB.record_id == record_id)
                .count()
            )
            if remaining_videos == 0:
                record = self.db.get(DataRecordDB, record_id)
                if record is not None:
                    self.db.delete(record)
            self.db.commit()
        except VideoDeletionError:
            self.db.rollback()
            raise
        except Exception as error:
            self.db.rollback()
            raise VideoDeletionError(
                "Video deletion failed; no database records were removed"
            ) from error
        return True

    def update_video(self, video: VideoData) -> bool:
        existing = self.db.get(VideoDB, video.video_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(video))
        self.db.commit()
        return True
