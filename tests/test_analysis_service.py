from datetime import datetime

import pytest

from app.data_models import AIAnalysisRecord
from app.services.analysis_service import AnalysisService

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from app.database import Base
from app.db_models import PlayerDB, DataRecordDB, VideoDB


TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base.metadata.create_all(bind=engine)


def make_service():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()

    player = PlayerDB(
        player_id="P001",
        first_name_ar="كريم",
        last_name_ar="السيد",
        first_name_en="Karim",
        last_name_en="Elsayed",
        date_of_birth=datetime(2000, 1, 1).date(),
        sex="male",
        physical_profile={},
        technical_profile={},
        mental_profile={},
        match_performance={},
    )

    db.add(player)
    db.commit()
    data_record = DataRecordDB(
        record_id="REC001",
        player_id="P001",
        source_type="video",
        created_at=datetime.now(),
        data_type="match_performance",
        status="completed",
        original_file_path="/data/original/video.mp4",
        analysis_id="AN001",
        schema_version="1.0",
        created_by="system",
    )

    db.add(data_record)
    db.commit()

    video = VideoDB(
        video_id="VID001",
        record_id="REC001",
        video_type="match",
        duration_seconds=60.0,
        recorded_at=datetime.now(),
        session_id="SESSION001",
        location_id="LOCATION001",
        capture_device="iPhone",
        resolution="1920x1080",
        frame_rate_fps=30.0,
        file_size_mb=10.0,
        file_format="mp4",
        file_path="/data/original/video.mp4",
        checksum="test_checksum",
        original_preserved=True,
        ai_processing_status="completed",
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

    db.add(video)
    db.commit()

    return AnalysisService(db=db)

def make_analysis(analysis_id="AN001"):
    return AIAnalysisRecord(
        analysis_id=analysis_id,
        video_id="VID001",
        player_id="P001",
        created_at=datetime.now(),
        analysis_type="player_performance",
        model_name="soccer_player_analyzer",
        model_version="1.0",
        processing_status="completed",
        processed_at=datetime.now(),
        confidence_score=0.95,
        overall_score=69.36,
        strengths=[],
        weaknesses=[],
        recommendations=[],
        raw_output_path=None,
        requires_human_review=False,
        human_review_status="not_required",
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        approved=False,
        approved_by=None,
        approved_at=None,
    )


def test_add_and_get_analysis():
    service = make_service()
    analysis = make_analysis()

    service.add_analysis(analysis)

    result = service.get_analysis("AN001")

    assert result == analysis


def test_get_unknown_analysis_returns_none():
    service = make_service()

    result = service.get_analysis("DOES_NOT_EXIST")

    assert result is None

def test_get_all_analyses():
    service = make_service()

    analysis_1 = make_analysis("AN001")
    analysis_2 = make_analysis("AN002")

    service.add_analysis(analysis_1)
    service.add_analysis(analysis_2)

    results = service.get_all_analyses()

    assert len(results) == 2
    assert analysis_1 in results
    assert analysis_2 in results

def test_delete_analysis():
    service = make_service()
    analysis = make_analysis("AN001")

    service.add_analysis(analysis)

    deleted = service.delete_analysis("AN001")

    assert deleted is True
    assert service.get_analysis("AN001") is None

def test_update_analysis():
    service = make_service()
    analysis = make_analysis("AN001")
    service.add_analysis(analysis)

    updated = make_analysis("AN001")
    updated.overall_score = 80.0

    result = service.update_analysis(updated)

    assert result is True
    assert service.get_analysis("AN001").overall_score == 80.0

def test_update_analysis_with_replacement_record():
    service = make_service()
    analysis = make_analysis("AN001")

    service.add_analysis(analysis)

    analysis.overall_score = 85.0
    updated = service.update_analysis(analysis)

    assert updated is True

    saved_analysis = service.get_analysis("AN001")
    assert saved_analysis is not None
    assert saved_analysis.overall_score == 85.0
    
def test_update_unknown_analysis_returns_false():
    service = make_service()
    analysis = make_analysis("AN999")

    updated = service.update_analysis(analysis)

    assert updated is False
    assert service.get_analysis("AN999") is None

def test_get_analyses_by_player():
    service = make_service()

    analysis1 = make_analysis("AN001")
    analysis2 = make_analysis("AN002")

    service.add_analysis(analysis1)
    service.add_analysis(analysis2)

    results = service.get_analyses_by_player("P001")

    assert len(results) == 2
    assert analysis1 in results
    assert analysis2 in results
    
def test_delete_unknown_analysis_returns_false():
    service = make_service()

    deleted = service.delete_analysis("DOES_NOT_EXIST")

    assert deleted is False

def test_add_analysis_with_unknown_player_fails():
    service = make_service()
    analysis = make_analysis("AN999")
    analysis.player_id = "DOES_NOT_EXIST"

    with pytest.raises(IntegrityError):
        service.add_analysis(analysis)
def test_analysis_requires_existing_video():
    service = make_service()

    analysis = make_analysis(
        analysis_id="AN_INVALID_VIDEO"
    )
    analysis.video_id = "VIDEO_DOES_NOT_EXIST"

    with pytest.raises(IntegrityError):
        service.add_analysis(analysis)



def test_analysis_preserves_structured_weaknesses():
    service = make_service()

    analysis = make_analysis()
    analysis.weaknesses = [
        {
            "attribute": "Vision",
            "score": 50,
        },
        {
            "attribute": "Passing",
            "score": 60,
        },
    ]

    service.add_analysis(analysis)

    saved = service.get_analysis("AN001")

    assert saved is not None
    assert saved.weaknesses == [
        {
            "attribute": "Vision",
            "score": 50,
        },
        {
            "attribute": "Passing",
            "score": 60,
        },
    ]


def test_analysis_rejects_invalid_weakness_score():
    analysis = make_analysis()
    analysis.weaknesses = [
        {
            "attribute": "Vision",
            "score": 150,
        }
    ]

    with pytest.raises(ValueError):
        analysis.__post_init__()
