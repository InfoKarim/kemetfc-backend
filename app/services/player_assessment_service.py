from sqlalchemy.orm import Session

from app.api_schemas import RecordBallMasterySchema, RecordYoYoKidsSchema
from app.db_models import PlayerAssessmentDB, PlayerMetricDB
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

# Ball Mastery is a coach-observed 1-5 rating per skill (not a formula), so
# this version just documents which skills/scale were in use for a given
# assessment — a future rubric revision bumps the version instead of
# reinterpreting past 1-5 ratings under a new scale.
BALL_MASTERY_METHODOLOGY_VERSION = "ball_mastery_1to5_v1"
BALL_MASTERY_SKILLS = (
    "sole_rolls",
    "inside_outside_cuts",
    "l_turn",
    "drag_back",
)


class PlayerAssessmentService:
    def __init__(self, db: Session):
        self.db = db

    def _record(
        self,
        player: Player,
        recorded_by_user_id: str | None,
        pillar: str,
        test_category: str,
        test_type: str,
        methodology_version: str,
        test_date,
        raw_data: dict,
        calculated_metrics: dict,
        notes: str | None,
        ai_assisted: bool = False,
        metrics: list[dict] | None = None,
    ) -> PlayerAssessmentDB:
        """``metrics`` (optional) are real, already-measured numbers pulled
        straight out of ``raw_data`` by the caller — e.g. a Yo-Yo Kids
        distance in meters, or a coach's own 1-5 skill rating — projected
        into PlayerMetricDB's normalized, trend-queryable shape. Never a
        derived/converted score where no validated formula exists (see
        this module's YOYO_KIDS_METHODOLOGY_VERSION comment): a test_type
        with no defensible per-metric number simply passes no ``metrics``.
        Each dict: {"name", "category", "score", "confidence" (optional),
        "source"}.
        """
        assessment = PlayerAssessmentDB(
            assessment_id=next_entity_id(self.db, "player_assessment"),
            player_id=player.player_id,
            pillar=pillar,
            test_category=test_category,
            test_type=test_type,
            methodology_version=methodology_version,
            test_date=test_date,
            age_at_assessment_years=calculate_player_age(
                player.date_of_birth, test_date
            ),
            raw_data=raw_data,
            calculated_metrics=calculated_metrics,
            ai_assisted=ai_assisted,
            notes=notes,
            recorded_by_user_id=recorded_by_user_id,
            created_at=utcnow(),
        )
        self.db.add(assessment)
        self.db.flush()

        for metric in metrics or []:
            self.db.add(PlayerMetricDB(
                player_id=player.player_id,
                assessment_id=assessment.assessment_id,
                metric_name=metric["name"],
                category=metric["category"],
                metric_score=metric["score"],
                confidence_score=metric.get("confidence"),
                recorded_at=test_date,
                source=metric["source"],
                notes=None,
                created_at=utcnow(),
            ))

        self.db.commit()
        self.db.refresh(assessment)
        return assessment

    def record_yoyo_kids(
        self,
        player: Player,
        payload: RecordYoYoKidsSchema,
        recorded_by_user_id: str | None,
    ) -> PlayerAssessmentDB:
        return self._record(
            player=player,
            recorded_by_user_id=recorded_by_user_id,
            pillar="physical",
            test_category="endurance",
            test_type="yoyo_kids",
            methodology_version=YOYO_KIDS_METHODOLOGY_VERSION,
            test_date=payload.test_date,
            raw_data={
                "level": payload.level,
                "shuttle": payload.shuttle,
                "total_distance_m": payload.total_distance_m,
            },
            calculated_metrics={},
            notes=payload.notes,
            metrics=[{
                "name": "yoyo_kids_distance_m",
                "category": "physical",
                "score": payload.total_distance_m,
                "source": "field_test",
            }],
        )

    def record_ball_mastery(
        self,
        player: Player,
        payload: RecordBallMasterySchema,
        recorded_by_user_id: str | None,
    ) -> PlayerAssessmentDB:
        return self._record(
            player=player,
            recorded_by_user_id=recorded_by_user_id,
            pillar="technical",
            test_category="ball_mastery",
            test_type="ball_mastery",
            methodology_version=BALL_MASTERY_METHODOLOGY_VERSION,
            test_date=payload.test_date,
            raw_data={
                "sole_rolls": payload.sole_rolls,
                "inside_outside_cuts": payload.inside_outside_cuts,
                "l_turn": payload.l_turn,
                "drag_back": payload.drag_back,
            },
            calculated_metrics={},
            notes=payload.notes,
            ai_assisted=payload.ai_assisted,
            metrics=[
                {
                    "name": f"ball_mastery_{skill}",
                    "category": "technical",
                    "score": getattr(payload, skill),
                    "source": "ai_assisted" if payload.ai_assisted else "coach_rating",
                }
                for skill in BALL_MASTERY_SKILLS
            ],
        )

    def list_for_player(
        self,
        player_id: str,
        pillar: str | None = None,
    ) -> list[PlayerAssessmentDB]:
        query = self.db.query(PlayerAssessmentDB).filter(
            PlayerAssessmentDB.player_id == player_id
        )
        if pillar is not None:
            query = query.filter(PlayerAssessmentDB.pillar == pillar)
        return query.order_by(PlayerAssessmentDB.test_date.desc()).all()

    def delete_assessment(self, assessment_id: str) -> bool:
        assessment = self.db.get(PlayerAssessmentDB, assessment_id)

        if assessment is None:
            return False

        # PlayerMetricDB.assessment_id is a non-nullable FK — without this,
        # deleting an assessment with any extracted metric rows would hit
        # an unhandled IntegrityError (the same class of bug already fixed
        # this session for player/video deletion). Metrics have no
        # independent meaning once their source assessment is gone, so
        # deleting them here (not just unlinking) is correct.
        self.db.query(PlayerMetricDB).filter(
            PlayerMetricDB.assessment_id == assessment_id
        ).delete(synchronize_session=False)

        self.db.delete(assessment)
        self.db.commit()
        return True
