import pytest
from datetime import datetime

from app.data_models import AIAnalysisRecord


def make_valid_analysis(**overrides):
    data = {
        "analysis_id": "AN_TEST_001",
        "video_id": "VID001",
        "player_id": "P001",
        "created_at": datetime.now(),
        "analysis_type": "player_performance",
        "model_name": "soccer_player_analyzer",
        "model_version": "1.0",
        "processing_status": "completed",
        "processed_at": datetime.now(),
        "confidence_score": 0.95,
        "overall_score": 69.36,
        "strengths": [],
        "weaknesses": [],
        "recommendations": [],
        "raw_output_path": None,
        "requires_human_review": False,
        "human_review_status": "not_required",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "approved": False,
        "approved_by": None,
        "approved_at": None,
    }

    data.update(overrides)
    return AIAnalysisRecord(**data)


def test_valid_ai_analysis_record():
    analysis = make_valid_analysis()

    assert analysis.analysis_id == "AN_TEST_001"
    assert analysis.confidence_score == 0.95
    assert analysis.processing_status == "completed"


def test_confidence_score_above_one():
    with pytest.raises(ValueError):
        make_valid_analysis(confidence_score=1.1)


def test_confidence_score_below_zero():
    with pytest.raises(ValueError):
        make_valid_analysis(confidence_score=-0.1)


def test_processed_at_requires_completed_status():
    with pytest.raises(ValueError):
        make_valid_analysis(
            processing_status="pending",
            processed_at=datetime.now(),
        )


def test_approved_requires_approved_by():
    with pytest.raises(ValueError):
        make_valid_analysis(
            approved=True,
            approved_by=None,
            approved_at=datetime.now(),
        )


def test_approved_requires_approved_at():
    with pytest.raises(ValueError):
        make_valid_analysis(
            approved=True,
            approved_by="coach_001",
            approved_at=None,
        )


def test_human_review_required_cannot_be_not_required():
    with pytest.raises(ValueError):
        make_valid_analysis(
            requires_human_review=True,
            human_review_status="not_required",
        )


def test_completed_review_requires_reviewer():
    with pytest.raises(ValueError):
        make_valid_analysis(
            requires_human_review=True,
            human_review_status="completed",
            reviewed_by=None,
            reviewed_at=datetime.now(),
        )


def test_completed_review_requires_reviewed_at():
    with pytest.raises(ValueError):
        make_valid_analysis(
            requires_human_review=True,
            human_review_status="completed",
            reviewed_by="coach_001",
            reviewed_at=None,
        )


def test_valid_completed_human_review():
    analysis = make_valid_analysis(
        requires_human_review=True,
        human_review_status="completed",
        reviewed_by="coach_001",
        reviewed_at=datetime.now(),
        review_notes="Analysis reviewed and verified.",
    )

    assert analysis.requires_human_review is True
    assert analysis.human_review_status == "completed"
    assert analysis.reviewed_by == "coach_001"

def test_valid_approval():
    analysis = make_valid_analysis(
        approved=True,
        approved_by="coach_001",
        approved_at=datetime.now(),
    )

    assert analysis.approved is True
    assert analysis.approved_by == "coach_001"
    assert analysis.approved_at is not None

def test_invalid_processing_status():
    with pytest.raises(ValueError):
        make_valid_analysis(
            processing_status="unknown",
        )

def test_completed_status_requires_processed_at():
    with pytest.raises(ValueError):
        make_valid_analysis(
            processing_status="completed",
            processed_at=None,
        )

def test_invalid_human_review_status():
    with pytest.raises(ValueError):
        make_valid_analysis(
            human_review_status="unknown",
        )


def test_no_human_review_requires_not_required_status():
    with pytest.raises(ValueError):
        make_valid_analysis(
            requires_human_review=False,
            human_review_status="pending",
        )
