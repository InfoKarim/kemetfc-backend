from datetime import date, datetime

from sqlalchemy.orm import Session

from app.data_models import AIAnalysisRecord, TrainingPlanData
from app.db_models import AnalysisDB, DataRecordDB, PlayerDB, VideoDB
from app.drill_recommendations import build_drill_recommendations
from app.services.analysis_service import AnalysisService
from app.services.drill_service import DrillService
from app.services.training_plan_service import TrainingPlanService


def _bounded_score(value: float) -> float:
    return round(min(max(float(value), 0.0), 100.0), 2)


JOINT_SYMMETRY_LABELS = {
    "hip": "Hip Symmetry",
    "knee": "Knee Symmetry",
    "ankle": "Ankle Symmetry",
    "elbow": "Elbow Symmetry",
}


def _joint_symmetry_attributes(
    feature_summary: dict,
) -> list[tuple[str, float]]:
    """Score how evenly each joint moved on the left vs. right side.

    A big gap between a player's left and right knee/hip/ankle/elbow
    angles (averaged across the video) is a sport-agnostic sign of a
    mobility or technique issue, so this doesn't require guessing at
    sport-specific "ideal" angle ranges.
    """
    attributes = []

    for joint, label in JOINT_SYMMETRY_LABELS.items():
        left = feature_summary.get(f"left_{joint}_angle_degrees", {}).get(
            "mean"
        )
        right = feature_summary.get(f"right_{joint}_angle_degrees", {}).get(
            "mean"
        )

        if left is None or right is None:
            continue

        asymmetry_degrees = abs(left - right)
        attributes.append(
            (label, _bounded_score(100 - asymmetry_degrees * 2))
        )

    return attributes


def _rated_attributes(result: dict) -> list[tuple[str, float]]:
    movement = result.get("movement_analysis") or {}
    analysis_type = movement.get("analysis_type") or result.get(
        "analysis_type"
    )
    summary = movement.get("summary") or result.get("summary") or {}

    if analysis_type == "full_match":
        target = result.get("target_player") or {}
        attributes = []
        pass_completion = target.get("pass_completion_rate")
        involvement = target.get("ball_involvement_rate")
        high_speed_runs = target.get("high_speed_run_count")

        if pass_completion is not None:
            attributes.append(
                ("Passing", _bounded_score(pass_completion * 100))
            )
        if involvement is not None:
            attributes.append(
                ("Ball Involvement", _bounded_score(involvement * 100))
            )
        if high_speed_runs is not None:
            attributes.append(
                ("Work Rate", _bounded_score(high_speed_runs * 12.5))
            )

        return attributes

    if analysis_type == "agility_ladder":
        cadence = summary.get("cadence_steps_per_minute")
        alternation = summary.get("alternation_rate")
        imbalance = summary.get("step_count_imbalance")
        attributes = []

        if cadence is not None:
            attributes.append(("Agility", _bounded_score(cadence / 2)))
        if alternation is not None:
            attributes.append(
                ("Coordination", _bounded_score(alternation * 100))
            )
        if imbalance is not None:
            attributes.append(
                ("Balance", _bounded_score((1 - imbalance) * 100))
            )

        return attributes

    if analysis_type == "squat_jump":
        repetitions = movement.get("repetitions") or []
        jump_count = summary.get("jump_count", 0)
        cycle_count = summary.get("movement_cycle_count", 0)
        attributes = []

        if cycle_count:
            attributes.append(
                (
                    "Explosiveness",
                    _bounded_score((jump_count / cycle_count) * 100),
                )
            )

        asymmetry = [
            repetition.get("measurements", {}).get(
                "mean_knee_asymmetry_degrees"
            )
            for repetition in repetitions
        ]
        asymmetry = [value for value in asymmetry if value is not None]

        if asymmetry:
            mean_asymmetry = sum(asymmetry) / len(asymmetry)
            attributes.append(
                ("Balance", _bounded_score(100 - mean_asymmetry * 5))
            )

        return attributes

    detection_rate = summary.get("detection_rate")
    feature_summary = (result.get("features") or {}).get("summary") or {}
    attributes = _joint_symmetry_attributes(feature_summary)

    if detection_rate is not None:
        attributes.append(
            ("Movement Visibility", _bounded_score(detection_rate * 100))
        )

    return attributes


def project_analysis_result(result: dict) -> dict:
    quality_control = result.get("quality_control") or {}
    if quality_control.get("abstained"):
        result_summary = result.get("summary") or {}
        confidence = result_summary.get("analysis_confidence")
        return {
            "overall_score": None,
            "confidence_score": (
                min(max(float(confidence), 0.0), 1.0)
                if confidence is not None
                else None
            ),
            "strengths": [],
            "weaknesses": [],
            "recommendations": [
                "Automated scoring was withheld because analysis quality did not "
                "meet the configured evidence thresholds. Review the source video."
            ],
        }

    attributes = _rated_attributes(result)
    strengths = [
        {"attribute": attribute, "score": score}
        for attribute, score in attributes
        if score >= 70
    ]
    weaknesses = [
        {"attribute": attribute, "score": score}
        for attribute, score in attributes
        if score < 70
    ]
    scores = [score for _, score in attributes]
    result_summary = result.get("summary") or {}
    detection_rate = result_summary.get("detection_rate")
    if result.get("analysis_type") == "full_match":
        detection_rate = result_summary.get("analysis_confidence")

    return {
        "overall_score": (
            round(sum(scores) / len(scores), 2) if scores else None
        ),
        "confidence_score": (
            min(max(float(detection_rate), 0.0), 1.0)
            if detection_rate is not None
            else None
        ),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendations": [
            f"Prioritize {item['attribute']} training."
            for item in weaknesses
        ],
    }


def _calculate_age(birth_date: date, today: date | None = None) -> int:
    current = today or date.today()
    return current.year - birth_date.year - (
        (current.month, current.day)
        < (birth_date.month, birth_date.day)
    )


class VideoAnalysisPublisher:
    def __init__(self, db: Session):
        self.db = db

    def publish(
        self,
        job_id: str,
        video_id: str,
        analysis_type: str,
        model_name: str,
        model_version: str,
        result: dict,
        result_path: str,
    ) -> AIAnalysisRecord:
        video = self.db.get(VideoDB, video_id)

        if video is None:
            raise ValueError("Video not found")

        record = self.db.get(DataRecordDB, video.record_id)

        if record is None:
            raise ValueError("Video data record not found")

        projected = project_analysis_result(result)
        now = datetime.now()
        analysis = AIAnalysisRecord(
            analysis_id=f"AN_{job_id}",
            video_id=video_id,
            player_id=record.player_id,
            created_at=now,
            analysis_type=analysis_type,
            model_name=model_name,
            model_version=model_version,
            processing_status="completed",
            processed_at=now,
            confidence_score=projected["confidence_score"],
            overall_score=projected["overall_score"],
            strengths=projected["strengths"],
            weaknesses=projected["weaknesses"],
            recommendations=projected["recommendations"],
            raw_output_path=result_path,
            requires_human_review=True,
            human_review_status="pending",
            reviewed_by=None,
            reviewed_at=None,
            review_notes=None,
            approved=False,
            approved_by=None,
            approved_at=None,
        )
        AnalysisService(db=self.db).add_analysis(analysis)

        record.analysis_id = analysis.analysis_id
        video.ai_processing_status = "completed"
        video.ai_processed_at = now
        video.ai_model_version = model_version
        video.ai_confidence_score = projected["confidence_score"]
        video.requires_human_review = True
        quality_control = result.get("quality_control") or {}
        if quality_control.get("abstained"):
            reasons = ", ".join(quality_control.get("reasons") or ["quality gate failed"])
            video.review_reason = (
                f"Automated scoring withheld: {reasons}"
            )[:500]
        else:
            video.review_reason = "AI-generated movement analysis"
        video.human_review_status = "pending"
        self.db.commit()

        return analysis

    def mark_video_failed(self, video_id: str, message: str) -> None:
        video = self.db.get(VideoDB, video_id)

        if video is None:
            return

        video.ai_processing_status = "failed"
        video.requires_human_review = True
        video.review_reason = message[:500]
        video.human_review_status = "pending"
        self.db.commit()

    def apply_human_review(
        self,
        job_id: str,
        video_id: str,
        review_status: str,
        reviewed_by: str,
        review_notes: str | None,
    ) -> AIAnalysisRecord | None:
        analysis_id = f"AN_{job_id}"
        analysis_row = self.db.get(AnalysisDB, analysis_id)

        if analysis_row is None:
            return None

        video = self.db.get(VideoDB, video_id)
        now = datetime.now()
        approved = review_status == "approved"

        analysis_row.human_review_status = "completed"
        analysis_row.reviewed_by = reviewed_by
        analysis_row.reviewed_at = now
        analysis_row.review_notes = review_notes
        analysis_row.approved = approved
        analysis_row.approved_by = reviewed_by if approved else None
        analysis_row.approved_at = now if approved else None

        if video is not None:
            video.human_review_status = "completed"
            video.reviewed_by = reviewed_by
            video.reviewed_at = now
            video.review_notes = review_notes
            video.analysis_approved = approved
            video.approved_by = reviewed_by if approved else None
            video.approved_at = now if approved else None

        self.db.commit()
        analysis = AnalysisService(db=self.db).get_analysis(analysis_id)

        if analysis is not None and approved:
            self._create_training_plan(analysis, analysis.player_id)

        return analysis

    def _create_training_plan(
        self,
        analysis: AIAnalysisRecord,
        player_id: str,
    ) -> None:
        if not analysis.weaknesses:
            return

        player = self.db.get(PlayerDB, player_id)

        if player is None:
            return

        recommendations = build_drill_recommendations(
            weaknesses=analysis.weaknesses,
            drills=DrillService(db=self.db).get_all_drills(),
            age=_calculate_age(player.date_of_birth),
            player_difficulty="beginner",
            target_duration=15,
            available_equipment=[],
        )

        if not any(item["drills"] for item in recommendations):
            return

        plan_service = TrainingPlanService(db=self.db)
        plan_id = f"PLAN_{analysis.analysis_id}"

        if plan_service.get_plan(plan_id) is not None:
            return

        plan_service.add_plan(
            TrainingPlanData(
                plan_id=plan_id,
                player_id=player_id,
                analysis_id=analysis.analysis_id,
                created_at=datetime.now(),
                status="draft",
                player_difficulty="beginner",
                target_duration=15,
                available_equipment=[],
                recommendations=recommendations,
            )
        )
