from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import AnalysisDB
from app.data_models import AIAnalysisRecord


class AnalysisService:
    """Service layer for handling AI analysis records."""

    def __init__(self, db: Session | None = None):
        self.db = db or SessionLocal()

    def _to_db(self, analysis: AIAnalysisRecord) -> AnalysisDB:
        return AnalysisDB(
            analysis_id=analysis.analysis_id,
            video_id=analysis.video_id,
            player_id=analysis.player_id,
            created_at=analysis.created_at,
            analysis_type=analysis.analysis_type,
            model_name=analysis.model_name,
            model_version=analysis.model_version,
            processing_status=analysis.processing_status,
            processed_at=analysis.processed_at,
            confidence_score=analysis.confidence_score,
            overall_score=analysis.overall_score,
            strengths=analysis.strengths,
            weaknesses=analysis.weaknesses,
            recommendations=analysis.recommendations,
            raw_output_path=analysis.raw_output_path,
            requires_human_review=analysis.requires_human_review,
            human_review_status=analysis.human_review_status,
            reviewed_by=analysis.reviewed_by,
            reviewed_at=analysis.reviewed_at,
            review_notes=analysis.review_notes,
            approved=analysis.approved,
            approved_by=analysis.approved_by,
            approved_at=analysis.approved_at,
        )

    def _to_domain(self, db_analysis: AnalysisDB) -> AIAnalysisRecord:
        return AIAnalysisRecord(
            analysis_id=db_analysis.analysis_id,
            video_id=db_analysis.video_id,
            player_id=db_analysis.player_id,
            created_at=db_analysis.created_at,
            analysis_type=db_analysis.analysis_type,
            model_name=db_analysis.model_name,
            model_version=db_analysis.model_version,
            processing_status=db_analysis.processing_status,
            processed_at=db_analysis.processed_at,
            confidence_score=db_analysis.confidence_score,
            overall_score=db_analysis.overall_score,
            strengths=db_analysis.strengths,
            weaknesses=db_analysis.weaknesses,
            recommendations=db_analysis.recommendations,
            raw_output_path=db_analysis.raw_output_path,
            requires_human_review=db_analysis.requires_human_review,
            human_review_status=db_analysis.human_review_status,
            reviewed_by=db_analysis.reviewed_by,
            reviewed_at=db_analysis.reviewed_at,
            review_notes=db_analysis.review_notes,
            approved=db_analysis.approved,
            approved_by=db_analysis.approved_by,
            approved_at=db_analysis.approved_at,
        )

    def add_analysis(self, analysis: AIAnalysisRecord) -> None:
        self.db.merge(self._to_db(analysis))
        self.db.commit()

    def get_analysis(self, analysis_id: str) -> AIAnalysisRecord | None:
        db_analysis = self.db.get(AnalysisDB, analysis_id)

        if db_analysis is None:
            return None

        return self._to_domain(db_analysis)

    def get_all_analyses(self) -> list[AIAnalysisRecord]:
        db_analyses = self.db.query(AnalysisDB).all()
        return [
            self._to_domain(analysis)
            for analysis in db_analyses
        ]

    def delete_analysis(self, analysis_id: str) -> bool:
        db_analysis = self.db.get(AnalysisDB, analysis_id)

        if db_analysis is None:
            return False

        self.db.delete(db_analysis)
        self.db.commit()
        return True

    def update_analysis(self, analysis: AIAnalysisRecord) -> bool:
        existing = self.db.get(AnalysisDB, analysis.analysis_id)

        if existing is None:
            return False

        self.db.merge(self._to_db(analysis))
        self.db.commit()
        return True

    def get_analyses_by_player(
        self,
        player_id: str,
    ) -> list[AIAnalysisRecord]:
        db_analyses = (
            self.db.query(AnalysisDB)
            .filter(AnalysisDB.player_id == player_id)
            .all()
        )

        return [
            self._to_domain(analysis)
            for analysis in db_analyses
        ]
