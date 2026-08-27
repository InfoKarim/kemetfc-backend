from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.data_models import DataRecord
from app.db_models import PlayerDB
from app.services.data_record_service import DataRecordService


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

    try:
        yield DataRecordService(db=db)
    finally:
        db.close()


def make_record():
    return DataRecord(
        record_id="REC_DATA_TEST_001",
	player_id="P001",
        source_type="video",
        created_at=datetime.now(),
        data_type="match_performance",
        status="pending",
        original_file_path="/data/original/video.mp4",
        analysis_id="AN001",
        schema_version="1.0",
        created_by="system",
    )


def test_add_and_get_data_record(service):
    record = make_record()

    service.add_record(record)

    saved_record = service.get_record("REC_DATA_TEST_001")

    assert saved_record is not None
    assert saved_record.record_id == "REC_DATA_TEST_001"
    assert saved_record.player_id == "P001"
    assert saved_record.source_type == "video"
def test_get_all_data_records(service):
    record = make_record()
    service.add_record(record)

    records = service.get_all_records()

    assert len(records) >= 1
    assert any(
        saved_record.record_id == "REC_DATA_TEST_001"
        for saved_record in records
    )


def test_delete_data_record(service):
    record = make_record()
    service.add_record(record)

    deleted = service.delete_record("REC_DATA_TEST_001")

    assert deleted is True
    assert service.get_record("REC_DATA_TEST_001") is None


def test_update_data_record(service):
    record = make_record()
    service.add_record(record)

    updated_record = make_record()
    updated_record.status = "processing"
    updated_record.data_type = "training_performance"

    updated = service.update_record(updated_record)

    saved_record = service.get_record("REC_DATA_TEST_001")

    assert updated is True
    assert saved_record is not None
    assert saved_record.status == "processing"
    assert saved_record.data_type == "training_performance"

def test_delete_missing_data_record_returns_false(service):
    deleted = service.delete_record("DOES_NOT_EXIST")

    assert deleted is False

def test_update_missing_data_record_returns_false(service):
    record = make_record()
    record.record_id = "DOES_NOT_EXIST"

    updated = service.update_record(record)

    assert updated is False
