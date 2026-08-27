from datetime import datetime

import pytest

from app.data_models import VideoAnalysisJobData


def make_job(**overrides):
    values = {
        "job_id": "JOB001",
        "video_id": "VID001",
        "analysis_type": "pose_estimation",
        "status": "queued",
        "created_at": datetime.now(),
        "started_at": None,
        "completed_at": None,
        "progress_percent": 0.0,
        "attempt_count": 0,
        "max_attempts": 3,
        "model_name": None,
        "model_version": None,
        "result_path": None,
        "error_message": None,
    }
    values.update(overrides)
    return VideoAnalysisJobData(**values)


def test_valid_queued_video_analysis_job():
    job = make_job()

    assert job.status == "queued"
    assert job.progress_percent == 0.0
    assert job.attempt_count == 0


def test_completed_job_requires_result_path():
    now = datetime.now()

    with pytest.raises(ValueError, match="result_path"):
        make_job(
            status="completed",
            started_at=now,
            completed_at=now,
            progress_percent=100.0,
        )


def test_approved_job_requires_reviewer():
    with pytest.raises(ValueError, match="reviewed_by"):
        make_job(review_status="approved")


def test_valid_reviewed_job():
    now = datetime.now()
    job = make_job(
        review_status="approved",
        reviewed_by="Coach Ahmed",
        reviewed_at=now,
        review_notes="Confirmed from video.",
    )

    assert job.review_status == "approved"
    assert job.reviewed_by == "Coach Ahmed"
