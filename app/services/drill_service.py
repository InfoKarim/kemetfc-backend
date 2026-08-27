from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import DrillDB
from app.data_models import DrillData


class DrillService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, drill: DrillData) -> DrillDB:
        return DrillDB(
            drill_id=drill.drill_id,
            name=drill.name,
            category=drill.category,
            description=drill.description,
            min_age=drill.min_age,
            max_age=drill.max_age,
            difficulty=drill.difficulty,
            duration_minutes=drill.duration_minutes,
            equipment=drill.equipment,
            video_url=drill.video_url,
            active=drill.active,
        )

    def _to_domain(self, db_drill: DrillDB) -> DrillData:
        return DrillData(
            drill_id=db_drill.drill_id,
            name=db_drill.name,
            category=db_drill.category,
            description=db_drill.description,
            min_age=db_drill.min_age,
            max_age=db_drill.max_age,
            difficulty=db_drill.difficulty,
            duration_minutes=db_drill.duration_minutes,
            equipment=db_drill.equipment,
            video_url=db_drill.video_url,
            active=db_drill.active,
        )

    def add_drill(self, drill: DrillData) -> None:
        self.db.merge(self._to_db(drill))
        self.db.commit()

    def get_drill(self, drill_id: str) -> DrillData | None:
        db_drill = self.db.get(DrillDB, drill_id)

        if db_drill is None:
            return None

        return self._to_domain(db_drill)

    def get_all_drills(self) -> list[DrillData]:
        db_drills = self.db.query(DrillDB).all()

        return [
            self._to_domain(drill)
            for drill in db_drills
        ]


    def delete_drill(self, drill_id: str) -> bool:
        db_drill = self.db.get(DrillDB, drill_id)

        if db_drill is None:
            return False

        self.db.delete(db_drill)
        self.db.commit()
        return True

    def update_drill(self, drill: DrillData) -> bool:
        existing = self.db.get(DrillDB, drill.drill_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(drill))
        self.db.commit()
        return True

    def get_drills_by_category(
        self,
        category: str,
    ) -> list[DrillData]:
        db_drills = (
            self.db.query(DrillDB)
            .filter(DrillDB.category == category)
            .all()
        )

        return [
            self._to_domain(drill)
            for drill in db_drills
        ]

    def get_drills_for_age(
        self,
        age: int,
    ) -> list[DrillData]:
        db_drills = (
            self.db.query(DrillDB)
            .filter(
                DrillDB.min_age <= age,
                DrillDB.max_age >= age,
            )
            .all()
        )

        return [
            self._to_domain(drill)
            for drill in db_drills
        ]

    def get_drills_by_difficulty(
        self,
        difficulty: str,
    ) -> list[DrillData]:
        db_drills = (
            self.db.query(DrillDB)
            .filter(DrillDB.difficulty == difficulty)
            .all()
        )

        return [
            self._to_domain(drill)
            for drill in db_drills
        ]


    def find_suitable_drills(
        self,
        category: str,
        age: int,
        difficulty: str,
    ) -> list[DrillData]:
        db_drills = (
            self.db.query(DrillDB)
            .filter(
                DrillDB.category == category,
                DrillDB.min_age <= age,
                DrillDB.max_age >= age,
                DrillDB.difficulty == difficulty,
                DrillDB.active.is_(True),
            )
            .all()
        )

        return [
            self._to_domain(drill)
            for drill in db_drills
        ]


