from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import DataRecordDB
from app.data_models import DataRecord


class DataRecordService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, record: DataRecord) -> DataRecordDB:
        return DataRecordDB(
            record_id=record.record_id,
            player_id=record.player_id,
            source_type=record.source_type,
            created_at=record.created_at,
            data_type=record.data_type,
            status=record.status,
            original_file_path=record.original_file_path,
            analysis_id=record.analysis_id,
            schema_version=record.schema_version,
            created_by=record.created_by,
        )

    def _to_domain(self, db_record: DataRecordDB) -> DataRecord:
        return DataRecord(
            record_id=db_record.record_id,
            player_id=db_record.player_id,
            source_type=db_record.source_type,
            created_at=db_record.created_at,
            data_type=db_record.data_type,
            status=db_record.status,
            original_file_path=db_record.original_file_path,
            analysis_id=db_record.analysis_id,
            schema_version=db_record.schema_version,
            created_by=db_record.created_by,
        )

    def add_record(self, record: DataRecord) -> None:
        self.db.merge(self._to_db(record))
        self.db.commit()

    def get_record(self, record_id: str) -> DataRecord | None:
        db_record = self.db.get(DataRecordDB, record_id)

        if db_record is None:
            return None

        return self._to_domain(db_record)

    def get_all_records(self) -> list[DataRecord]:
        db_records = self.db.query(DataRecordDB).all()
        return [
            self._to_domain(record)
            for record in db_records
        ]
    def delete_record(self, record_id: str) -> bool:
        db_record = self.db.get(DataRecordDB, record_id)

        if db_record is None:
            return False

        self.db.delete(db_record)
        self.db.commit()
        return True

    def update_record(self, record: DataRecord) -> bool:
        existing = self.db.get(DataRecordDB, record.record_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(record))
        self.db.commit()
        return True

