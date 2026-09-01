import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.analysis_reprocessing import reprocess_pose_estimation_analyses
from app.database import Base
from app.db_models import AnalysisDB, PlayerDB
from app.pose_features import LANDMARK_INDEX

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


def _landmark(x_px, y_px, image_width, image_height, visibility=1.0):
    return {
        "x": x_px / image_width,
        "y": y_px / image_height,
        "z": 0.0,
        "visibility": visibility,
        "presence": visibility,
    }


def _raw_result_with_only_landmark_frames(
    image_width=100, image_height=100, num_frames=6, step_px=8.0
):
    """Mimics an analysis stored *before* body_height_pixels/hip_center_x
    existed in pose_features.py: the raw landmark_frames are present (as
    they always have been), but the pre-computed "features" only has the
    old, narrower measurement set — reprocessing must regenerate features
    from landmark_frames to pick up Speed/Acceleration/Agility."""
    frames = []

    for index in range(num_frames):
        landmarks = [
            {"x": 0.0, "y": 0.0, "z": 0.0, "visibility": 0.0, "presence": 0.0}
            for _ in range(33)
        ]
        hip_x = 20.0 + step_px * index
        landmarks[LANDMARK_INDEX["nose"]] = _landmark(
            hip_x, 10.0, image_width, image_height
        )
        landmarks[LANDMARK_INDEX["left_hip"]] = _landmark(
            hip_x - 5, 50.0, image_width, image_height
        )
        landmarks[LANDMARK_INDEX["right_hip"]] = _landmark(
            hip_x + 5, 50.0, image_width, image_height
        )
        landmarks[LANDMARK_INDEX["left_ankle"]] = _landmark(
            hip_x - 5, 90.0, image_width, image_height
        )
        landmarks[LANDMARK_INDEX["right_ankle"]] = _landmark(
            hip_x + 5, 90.0, image_width, image_height
        )
        frames.append({
            "frame_index": index,
            "timestamp_ms": index * 100,
            "landmarks": landmarks,
        })

    return {
        "analysis_type": "pose_estimation",
        "summary": {"detection_rate": 0.9},
        "video": {"image_width": image_width, "image_height": image_height},
        "landmark_frames": frames,
        # Old, frozen feature set — no body_height_pixels/hip_center_x at all.
        "features": {"summary": {}, "frames": []},
    }


def test_reprocessing_regenerates_features_from_raw_landmarks():
    db = _make_db()
    _make_player(db, height_cm=150.0)
    _make_analysis(db)
    storage = FakeStorage(
        {"JOB1": _raw_result_with_only_landmark_frames()}
    )

    results = reprocess_pose_estimation_analyses(db, storage, dry_run=False)

    assert results[0]["status"] == "updated"
    assert "Speed" in results[0]["added_attributes"]

    updated = db.get(AnalysisDB, "AN_JOB1")
    all_attributes = {
        item["attribute"]: item["score"]
        for item in updated.weaknesses + updated.strengths
    }
    # body_height_pixels=80 with a real height of 150cm; hip moves 8px
    # every 100ms -> 1.5 m/s -> 25.0 on a 6.0 m/s ceiling.
    assert all_attributes["Speed"] == pytest.approx(25.0, abs=0.5)


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
