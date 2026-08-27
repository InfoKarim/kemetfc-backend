from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.data_models import TrainingPlanData
from app.db_models import TrainingPlanDB


class TrainingPlanService:
    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, plan: TrainingPlanData) -> TrainingPlanDB:
        return TrainingPlanDB(
            plan_id=plan.plan_id,
            player_id=plan.player_id,
            analysis_id=plan.analysis_id,
            created_at=plan.created_at,
            status=plan.status,
            player_difficulty=plan.player_difficulty,
            target_duration=plan.target_duration,
            available_equipment=plan.available_equipment,
            recommendations=plan.recommendations,
        )

    def _to_domain(
        self,
        db_plan: TrainingPlanDB,
    ) -> TrainingPlanData:
        return TrainingPlanData(
            plan_id=db_plan.plan_id,
            player_id=db_plan.player_id,
            analysis_id=db_plan.analysis_id,
            created_at=db_plan.created_at,
            status=db_plan.status,
            player_difficulty=db_plan.player_difficulty,
            target_duration=db_plan.target_duration,
            available_equipment=db_plan.available_equipment,
            recommendations=db_plan.recommendations,
        )

    def add_plan(self, plan: TrainingPlanData) -> None:
        self.db.merge(self._to_db(plan))
        self.db.commit()

    def get_plan(
        self,
        plan_id: str,
    ) -> TrainingPlanData | None:
        db_plan = self.db.get(TrainingPlanDB, plan_id)

        if db_plan is None:
            return None

        return self._to_domain(db_plan)


    def get_all_plans(self) -> list[TrainingPlanData]:
        db_plans = self.db.query(TrainingPlanDB).all()

        return [
            self._to_domain(plan)
            for plan in db_plans
        ]

    def update_plan(
        self,
        plan: TrainingPlanData,
    ) -> bool:
        existing = self.db.get(TrainingPlanDB, plan.plan_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(plan))
        self.db.commit()
        return True

    def delete_plan(self, plan_id: str) -> bool:
        db_plan = self.db.get(TrainingPlanDB, plan_id)

        if db_plan is None:
            return False

        self.db.delete(db_plan)
        self.db.commit()
        return True

    def get_plans_by_player(
        self,
        player_id: str,
    ) -> list[TrainingPlanData]:
        db_plans = (
            self.db.query(TrainingPlanDB)
            .filter(TrainingPlanDB.player_id == player_id)
            .all()
        )

        return [
            self._to_domain(plan)
            for plan in db_plans
        ]

    def get_plans_by_analysis(
        self,
        analysis_id: str,
    ) -> list[TrainingPlanData]:
        db_plans = (
            self.db.query(TrainingPlanDB)
            .filter(TrainingPlanDB.analysis_id == analysis_id)
            .all()
        )

        return [
            self._to_domain(plan)
            for plan in db_plans
        ]
