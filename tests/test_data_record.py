import pytest
from datetime import datetime

from app.data_models import DataRecord


def make_valid_record(**overrides):
    data = {
        "record_id": "REC_TEST_001",
        "player_id": "P001",
        "source_type": "video",
        "created_at": datetime.now(),
        "data_type": "match_performance",
        "status": "pending",
        "original_file_path": "/data/original/video.mp4",
        "analysis_id": "AN_TEST_001",
        "schema_version": "1.0",
        "created_by": "system",
    }

    data.update(overrides)
    return DataRecord(**data)


def test_valid_data_record():
    record = make_valid_record()

    assert record.record_id == "REC_TEST_001"
    assert record.player_id == "P001"
    assert record.status == "pending"


def test_data_record_invalid_source_type():
    with pytest.raises(ValueError):
        make_valid_record(source_type="unknown")


def test_data_record_invalid_status():
    with pytest.raises(ValueError):
        make_valid_record(status="unknown")


def test_data_record_record_id_cannot_be_empty():
    with pytest.raises(ValueError):
        make_valid_record(record_id="")


def test_data_record_player_id_cannot_be_empty():
    with pytest.raises(ValueError):
        make_valid_record(player_id="")


def test_data_record_original_file_path_cannot_be_empty():
    with pytest.raises(ValueError):
        make_valid_record(original_file_path="")


def test_data_record_schema_version_cannot_be_empty():
    with pytest.raises(ValueError):
        make_valid_record(schema_version="")


def test_data_record_created_by_cannot_be_empty():
    with pytest.raises(ValueError):
        make_valid_record(created_by="")
