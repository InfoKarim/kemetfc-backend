"""One-time-use reprocessing of existing pose_estimation analyses so they
pick up attributes (Speed, Acceleration, Agility) that didn't exist yet
when they were first analyzed.

Only touches the measurement fields (weaknesses, strengths, overall_score,
confidence_score, recommendations) — human review/approval status is left
exactly as it was, since a coach may have already acted on it.
"""

import json

from sqlalchemy.orm import Session

from app.analysis_result_storage import AnalysisResultStorage
from app.db_models import AnalysisDB, PlayerDB
from app.video_analysis_publication import project_analysis_result


def reprocess_pose_estimation_analyses(
    db: Session,
    storage: AnalysisResultStorage,
    dry_run: bool = True,
) -> list[dict]:
    results = []
    analyses = (
        db.query(AnalysisDB)
        .filter(AnalysisDB.analysis_type == "pose_estimation")
        .all()
    )

    for analysis in analyses:
        entry: dict = {"analysis_id": analysis.analysis_id}
        job_id = analysis.analysis_id.removeprefix("AN_")

        try:
            raw_bytes = storage.read(job_id, analysis.raw_output_path)
            raw_result = json.loads(raw_bytes)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
            entry["status"] = f"skipped: could not read raw result ({error})"
            results.append(entry)
            continue

        player = db.get(PlayerDB, analysis.player_id)
        player_height_cm = (
            (player.physical_profile or {}).get("height_cm")
            if player is not None
            else None
        )

        projected = project_analysis_result(raw_result, player_height_cm)

        old_attributes = {
            item["attribute"]
            for item in (analysis.weaknesses or []) + (analysis.strengths or [])
        }
        new_attributes = {
            item["attribute"]
            for item in projected["weaknesses"] + projected["strengths"]
        }
        entry["added_attributes"] = sorted(new_attributes - old_attributes)
        entry["status"] = "would update" if dry_run else "updated"

        if not dry_run:
            analysis.weaknesses = projected["weaknesses"]
            analysis.strengths = projected["strengths"]
            analysis.overall_score = projected["overall_score"]
            analysis.confidence_score = projected["confidence_score"]
            analysis.recommendations = projected["recommendations"]

        results.append(entry)

    if not dry_run:
        db.commit()

    return results
