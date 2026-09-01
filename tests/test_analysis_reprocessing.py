import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analysis_reprocessing import reprocess_pose_estimation_analyses
from app.database import Base
from app.db_models import AnalysisDB, PlayerDB

TestingSessionLocal = sessionmaker(
    bind=create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    ),
    autoflush=False,
    autocommit=False,
)


class FakeStorage:
    def __init__(self, contents: dict[str, dict]):
        self.contents = contents

    def read(self, job_id, reference):
        if job_id not in self.contents:
            raise FileNotFoundError("no such job")
        return json.dumps(self.contents[job_id]).encode("utf-8")


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _make_player(db, player_id="P100", height_cm=150.0):
    player = PlayerDB(
        player_id=player_id,
        first_name_ar="ك",
        last_name_ar="س",
        first_name_en="Test",
        last_name_en="Player",
        date_of_birth=datetime(2015, 1, 1).date(),
        sex="male",
        team_id=None,
        physical_profile={
            "height_cm": height_cm, "weight_kg": 35.0, "dominant_foot": "right",
            "speed": 70.0, "acceleration": 70.0, "agility": 70.0,
            "stamina": 70.0, "strength": 70.0,
        },
        technical_profile={
            "ball_control": 70.0, "dribbling": 70.0, "passing": 70.0,
            "shooting": 70.0, "finishing": 70.0,
        },
        mental_profile={
            "decision_making": 70.0, "concentration": 70.0, "composure": 70.0,
            "positioning": 70.0, "vision": 70.0,
        },
        match_performance={
            "minutes_played": 0, "goals": 0, "assists": 0, "shots": 0,
            "shots_on_target": 0, "passes_attempted": 0, "passes_completed": 0,
            "tackles": 0, "interceptions": 0, "rating": 0.0,
        },
        tactical_profile=None,
        created_at=datetime.now(),
        photo_filename=None,
    )
    db.add(player)
    db.commit()
    return player


def _make_analysis(db, analysis_id="AN_JOB1", player_id="P100"):
    analysis = AnalysisDB(
        analysis_id=analysis_id,
        video_id="VID1",
        player_id=player_id,
        created_at=datetime.now(),
        analysis_type="pose_estimation",
        model_name="test",
        model_version="1.0",
        processing_status="completed",
        processed_at=datetime.now(),
        confidence_score=0.9,
        overall_score=90.0,
        strengths=[{"attribute": "Movement Visibility", "score": 90.0}],
        weaknesses=[],
        recommendations=[],
        raw_output_path="/analysis/JOB1.json",
        requires_human_review=True,
        human_review_status="completed",
        reviewed_by="coach1",
        reviewed_at=datetime.now(),
        review_notes="looked fine",
        approved=True,
        approved_by="coach1",
        approved_at=datetime.now(),
    )
    db.add(analysis)
    db.commit()
    return analysis


def _raw_result_with_movement(height_px=150.0, step_px=8.0):
    frames = [
        {
            "timestamp_ms": index * 100,
            "measurements": {
                "body_height_pixels": height_px,
                "hip_center_x_normalized": (step_px * index) / 100.0,
                "hip_center_y_normalized": 0.5,
            },
        }
        for index in range(6)
    ]
    return {
        "analysis_type": "pose_estimation",
        "summary": {"detection_rate": 0.9},
        "video": {"image_width": 100, "image_height": 100},
        "features": {"summary": {}, "frames": frames},
    }


def test_dry_run_reports_changes_without_writing_to_the_database():
    db = _make_db()
    _make_player(db, height_cm=150.0)
    _make_analysis(db)
    storage = FakeStorage({"JOB1": _raw_result_with_movement()})

    results = reprocess_pose_estimation_analyses(db, storage, dry_run=True)

    assert results[0]["status"] == "would update"
    assert "Speed" in results[0]["added_attributes"]

    unchanged = db.get(AnalysisDB, "AN_JOB1")
    assert unchanged.weaknesses == []
    assert unchanged.strengths == [
        {"attribute": "Movement Visibility", "score": 90.0}
    ]


def test_real_run_updates_measurements_but_preserves_review_status():
    db = _make_db()
    _make_player(db, height_cm=150.0)
    _make_analysis(db)
    storage = FakeStorage({"JOB1": _raw_result_with_movement()})

    results = reprocess_pose_estimation_analyses(db, storage, dry_run=False)

    assert results[0]["status"] == "updated"

    updated = db.get(AnalysisDB, "AN_JOB1")
    all_attributes = {
        item["attribute"]
        for item in updated.weaknesses + updated.strengths
    }
    assert "Speed" in all_attributes
    assert "Agility" in all_attributes

    # Review/approval metadata must be untouched.
    assert updated.human_review_status == "completed"
    assert updated.reviewed_by == "coach1"
    assert updated.approved is True
    assert updated.approved_by == "coach1"


def test_skips_analyses_whose_raw_result_is_missing():
    db = _make_db()
    _make_player(db)
    _make_analysis(db)
    storage = FakeStorage({})

    results = reprocess_pose_estimation_analyses(db, storage, dry_run=True)

    assert results[0]["status"].startswith("skipped")

    unchanged = db.get(AnalysisDB, "AN_JOB1")
    assert unchanged.strengths == [
        {"attribute": "Movement Visibility", "score": 90.0}
    ]
