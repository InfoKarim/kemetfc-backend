import pytest
from datetime import datetime

from app.data_models import VideoData


def make_valid_video(**overrides):
    data = {
        "video_id": "VID_TEST_001",
        "record_id": "REC001",
        "video_type": "match",
        "duration_seconds": 120.0,
        "recorded_at": datetime.now(),
        "session_id": "SESSION001",
        "location_id": "LOC001",
        "capture_device": "camera",
        "resolution": "1920x1080",
        "frame_rate_fps": 30.0,
        "file_size_mb": 100.0,
        "file_format": "mp4",
        "file_path": "/videos/test.mp4",
        "checksum": "test-checksum",
        "original_preserved": True,
        "ai_processing_status": "pending",
        "ai_processed_at": None,
        "ai_model_version": None,
        "ai_confidence_score": None,
        "requires_human_review": False,
        "review_reason": "",
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "analysis_approved": False,
        "approved_by": None,
        "approved_at": None,
    }

    data.update(overrides)
    return VideoData(**data)


def test_valid_video_data():
    video = make_valid_video()

    assert video.video_id == "VID_TEST_001"
    assert video.duration_seconds == 120.0


def test_video_duration_must_be_positive():
    with pytest.raises(ValueError):
        make_valid_video(duration_seconds=0)


def test_video_frame_rate_must_be_positive():
    with pytest.raises(ValueError):
        make_valid_video(frame_rate_fps=0)


def test_video_file_size_must_be_positive():
    with pytest.raises(ValueError):
        make_valid_video(file_size_mb=0)


def test_video_ai_confidence_score_above_one():
    with pytest.raises(ValueError):
        make_valid_video(ai_confidence_score=1.1)


def test_video_ai_confidence_score_below_zero():
    with pytest.raises(ValueError):
        make_valid_video(ai_confidence_score=-0.1)


def test_video_invalid_processing_status():
    with pytest.raises(ValueError):
        make_valid_video(ai_processing_status="unknown")


def test_video_completed_processing_requires_processed_at():
    with pytest.raises(ValueError):
        make_valid_video(
            ai_processing_status="completed",
            ai_processed_at=None,
        )


def test_video_pending_processing_cannot_have_processed_at():
    with pytest.raises(ValueError):
        make_valid_video(
            ai_processing_status="pending",
            ai_processed_at=datetime.now(),
        )


def test_video_human_review_required_cannot_be_not_required():
    with pytest.raises(ValueError):
        make_valid_video(
            requires_human_review=True,
            human_review_status="not_required",
        )


def test_video_no_human_review_requires_not_required_status():
    with pytest.raises(ValueError):
        make_valid_video(
            requires_human_review=False,
            human_review_status="pending",
        )


def test_video_completed_review_requires_reviewer():
    with pytest.raises(ValueError):
        make_valid_video(
            requires_human_review=True,
            human_review_status="completed",
            reviewed_by=None,
            reviewed_at=datetime.now(),
        )


def test_video_completed_review_requires_reviewed_at():
    with pytest.raises(ValueError):
        make_valid_video(
            requires_human_review=True,
            human_review_status="completed",
            reviewed_by="coach_001",
            reviewed_at=None,
        )


def test_video_approval_requires_approved_by():
    with pytest.raises(ValueError):
        make_valid_video(
            analysis_approved=True,
            approved_by=None,
            approved_at=datetime.now(),
        )


def test_video_approval_requires_approved_at():
    with pytest.raises(ValueError):
        make_valid_video(
            analysis_approved=True,
            approved_by="coach_001",
            approved_at=None,
        )


def test_valid_completed_video_processing():
    video = make_valid_video(
        ai_processing_status="completed",
        ai_processed_at=datetime.now(),
        ai_model_version="1.0",
        ai_confidence_score=0.95,
    )

    assert video.ai_processing_status == "completed"
    assert video.ai_processed_at is not None


def test_valid_completed_video_review():
    video = make_valid_video(
        requires_human_review=True,
        review_reason="Low AI confidence",
        human_review_status="completed",
        reviewed_by="coach_001",
        reviewed_at=datetime.now(),
        review_notes="Video analysis verified.",
    )

    assert video.human_review_status == "completed"
    assert video.reviewed_by == "coach_001"


def test_valid_video_approval():
    video = make_valid_video(
        analysis_approved=True,
        approved_by="coach_001",
        approved_at=datetime.now(),
    )

    assert video.analysis_approved is True
    assert video.approved_by == "coach_001"


def test_video_invalid_human_review_status():
    with pytest.raises(ValueError):
        make_valid_video(
            human_review_status="unknown",
        )
