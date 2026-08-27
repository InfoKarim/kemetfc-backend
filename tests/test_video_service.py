from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.data_models import VideoData
from app.db_models import DataRecordDB, PlayerDB, VideoAnalysisJobDB
from app.services.video_service import VideoDeletionError, VideoService


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def enable_test_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(bind=test_engine)


@pytest.fixture
def service():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    db.add(PlayerDB(
        player_id="P001",
        first_name_ar="Test",
        last_name_ar="Player",
        first_name_en="Test",
        last_name_en="Player",
        date_of_birth=datetime(2015, 1, 1).date(),
        sex="male",
        physical_profile={},
        technical_profile={},
        mental_profile={},
        match_performance={},
    ))
    db.commit()
    db.add(DataRecordDB(
        record_id="REC001",
        player_id="P001",
        source_type="video",
        created_at=datetime.now(),
        data_type="match_performance",
        status="completed",
        original_file_path="/videos/test.mp4",
        analysis_id="AN001",
        schema_version="1.0",
        created_by="test",
    ))
    db.commit()

    try:
        yield VideoService(db=db)
    finally:
        db.close()

def make_video():
    return VideoData(
        video_id="VID001",
        record_id="REC001",
        video_type="match",
        duration_seconds=120.0,
        recorded_at=datetime.now(),
        session_id="SESSION001",
        location_id="LOC001",
        capture_device="camera",
        resolution="1920x1080",
        frame_rate_fps=30.0,
        file_size_mb=100.0,
        file_format="mp4",
        file_path="/videos/test.mp4",
        checksum="test-checksum",
        original_preserved=True,
        ai_processing_status="pending",
        ai_processed_at=None,
        ai_model_version=None,
        ai_confidence_score=None,
        requires_human_review=False,
        review_reason="",
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        analysis_approved=False,
        approved_by=None,
        approved_at=None,
    )


def test_add_and_get_video(service):
    video = make_video()

    service.add_video(video)

    saved_video = service.get_video("VID001")

    assert saved_video is not None
    assert saved_video.video_id == "VID001"
    assert saved_video.video_type == "match"
    assert saved_video.file_path == "/videos/test.mp4"

def test_get_all_videos(service):
    video = make_video()

    service.add_video(video)

    videos = service.get_all_videos()

    assert len(videos) >= 1
    assert any(
        saved_video.video_id == "VID001"
        for saved_video in videos
    )

def test_delete_video(service):
    video = make_video()
    video.video_id = "VID_DELETE_TEST"

    service.add_video(video)

    deleted = service.delete_video("VID_DELETE_TEST")

    assert deleted is True
    assert service.get_video("VID_DELETE_TEST") is None


def test_delete_video_removes_analysis_jobs_and_orphan_record(service):
    video = make_video()
    video.video_id = "VID_DELETE_WITH_JOB"
    service.add_video(video)
    service.db.add(VideoAnalysisJobDB(
        job_id="JOB_DELETE",
        video_id=video.video_id,
        analysis_type="pose_estimation",
        status="queued",
        created_at=datetime.now(),
        started_at=None,
        completed_at=None,
        progress_percent=0.0,
        attempt_count=0,
        max_attempts=3,
        model_name=None,
        model_version=None,
        result_path=None,
        error_message=None,
        review_status="pending",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        target_track_id=None,
    ))
    service.db.commit()

    assert service.delete_video(video.video_id) is True
    assert service.db.get(VideoAnalysisJobDB, "JOB_DELETE") is None
    assert service.db.get(DataRecordDB, "REC001") is None


def test_delete_video_rejects_processing_job(service):
    video = make_video()
    service.add_video(video)
    service.db.add(VideoAnalysisJobDB(
        job_id="JOB_PROCESSING",
        video_id=video.video_id,
        analysis_type="pose_estimation",
        status="processing",
        created_at=datetime.now(),
        started_at=datetime.now(),
        completed_at=None,
        progress_percent=25.0,
        attempt_count=1,
        max_attempts=3,
        model_name=None,
        model_version=None,
        result_path=None,
        error_message=None,
        review_status="pending",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        target_track_id=None,
    ))
    service.db.commit()

    with pytest.raises(VideoDeletionError, match="while analysis is processing"):
        service.delete_video(video.video_id)

    assert service.get_video(video.video_id) is not None


def test_update_video(service):
    video = make_video()
    service.add_video(video)

    updated_video = make_video()
    updated_video.video_type = "training"
    updated_video.file_path = "/videos/updated.mp4"

    updated = service.update_video(updated_video)

    saved_video = service.get_video("VID001")

    assert updated is True
    assert saved_video is not None
    assert saved_video.video_type == "training"
    assert saved_video.file_path == "/videos/updated.mp4"


def test_delete_missing_video_returns_false(service):
    deleted = service.delete_video("DOES_NOT_EXIST")

    assert deleted is False

def test_update_missing_video_returns_false(service):
    video = make_video()
    video.video_id = "DOES_NOT_EXIST"

    updated = service.update_video(video)

    assert updated is False


from sqlalchemy.exc import IntegrityError


def test_video_requires_existing_data_record(service):
    video = make_video()
    video.video_id = "VID_INVALID_RECORD"
    video.record_id = "RECORD_DOES_NOT_EXIST"

    with pytest.raises(IntegrityError):
        service.add_video(video)

    service.db.rollback()
