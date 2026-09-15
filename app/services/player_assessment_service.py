from sqlalchemy.orm import Session

from app.api_schemas import RecordYoYoKidsSchema
from app.db_models import PhysicalAssessmentDB
from app.development_snapshot import calculate_player_age
from app.player import Player
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id


# Identifies exactly what this test type's raw_data/calculated_metrics shape
# means, so a future methodology change (e.g. adding a verified pediatric
# fitness conversion) never silently reinterprets a past result — it adds a
# new version instead of editing this one in place.
#
# There is no verified, published age-appropriate conversion from Yo-Yo
# Kids distance to a fitness score (the adult Yo-Yo IR1 formula does not
# apply to children and must not be used here — see product guidance on
# never comparing youth players to adult norms). Until KEMET FC adopts a
# validated pediatric methodology, this test only records the objective
# distance covered; calculated_metrics is intentionally left empty rather
# than populated with an invented figure.
YOYO_KIDS_METHODOLOGY_VERSION = "yoyo_kids_raw_distance_v1"


class PhysicalAssessmentService:
    def __init__(self, db: Session):
        self.db = db

    def record_yoyo_kids(
        self,
        player: Player,
        payload: RecordYoYoKidsSchema,
        recorded_by_user_id: str | None,
    ) -> PhysicalAssessmentDB:
        assessment = PhysicalAssessmentDB(
            assessment_id=next_entity_id(self.db, "physical_assessment"),
            player_id=player.player_id,
            test_category="endurance",
            test_type="yoyo_kids",
            methodology_version=YOYO_KIDS_METHODOLOGY_VERSION,
            test_date=payload.test_date,
            age_at_assessment_years=calculate_player_age(
                player.date_of_birth, payload.test_date
            ),
            raw_data={
                "level": payload.level,
                "shuttle": payload.shuttle,
                "total_distance_m": payload.total_distance_m,
            },
            calculated_metrics={},
            notes=payload.notes,
            recorded_by_user_id=recorded_by_user_id,
            created_at=utcnow(),
        )
        self.db.add(assessment)
        self.db.commit()
        self.db.refresh(assessment)
        return assessment

    def list_for_player(self, player_id: str) -> list[PhysicalAssessmentDB]:
        return (
            self.db.query(PhysicalAssessmentDB)
            .filter(PhysicalAssessmentDB.player_id == player_id)
            .order_by(PhysicalAssessmentDB.test_date.desc())
            .all()
        )

    def delete_assessment(self, assessment_id: str) -> bool:
        assessment = self.db.get(PhysicalAssessmentDB, assessment_id)

        if assessment is None:
            return False

        self.db.delete(assessment)
        self.db.commit()
        return True
