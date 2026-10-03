"""Privacy export / deletion coverage of assessment, derived-progress and
tracking data (Phase 6 / Slice S0).

Every row here is synthetic and lives in a throwaway in-memory database that
the test itself creates. Foreign keys are enforced (as on PostgreSQL) so a
wrong deletion order fails the test instead of passing silently on SQLite.
No video file, upload directory, production setting or real recording is
used; file-storage deletion is replaced by a recorder so the tests can prove
that no physical file was touched.
"""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import (
    AuditEventDB,
    CoachMessageDB,
    CoachValidationLabelDB,
    DataRecordDB,
    GuardianConsentDB,
    GuardianPlayerLinkDB,
    MLDatasetEntryDB,
    MLPredictionDB,
    MonteCarloSimulationDB,
    PlayerAssessmentDB,
    PlayerDB,
    PlayerMetricDB,
    PredictionOutcomeDB,
    PrivacyRequestDB,
    TrackingEventDB,
    TrackingFeatureSetDB,
    TrackingSampleDB,
    TrackingSessionDB,
    UserDB,
    VideoDB,
)
from app.services.auth_service import hash_password, utcnow
from main import CSRF_COOKIE_NAME, app

NOW = datetime(2026, 9, 1, 12, 0, 0)

PROFILE = dict(
    physical_profile={"height_cm": 140.0, "weight_kg": 35.0, "dominant_foot": "right",
                      "speed": 70.0, "acceleration": 72.0, "agility": 68.0,
                      "stamina": 75.0, "strength": 60.0},
    technical_profile={"ball_control": 70.0, "dribbling": 72.0, "passing": 68.0,
                       "shooting": 65.0, "finishing": 67.0},
    mental_profile={"decision_making": 70.0, "concentration": 72.0, "composure": 68.0,
                    "positioning": 71.0, "vision": 74.0},
    match_performance={"minutes_played": 0, "goals": 0, "assists": 0, "shots": 0,
                       "shots_on_target": 0, "passes_attempted": 0,
                       "passes_completed": 0, "tackles": 0, "interceptions": 0,
                       "rating": 0.0},
    tactical_profile={"positioning": 61.0},
    weak_foot_profile={"left": 40.0},
)

def make_user(db, user_id: str, username: str, role: str) -> None:
    now = utcnow()
    db.add(UserDB(
        user_id=user_id, username=username,
        password_hash=hash_password(f"{username}Password123!"),
        role=role, active=True, feature_permissions=None,
        created_at=now, updated_at=now,
    ))


def seed_child(db, pid: str, coach_id: str, admin_id: str) -> None:
    """One player with a full set of synthetic assessment/tracking rows."""
    db.add(PlayerDB(
        player_id=pid, first_name_ar="لاعب", last_name_ar=pid,
        first_name_en="Player", last_name_en=pid,
        date_of_birth=date(2016, 1, 1), sex="male", **PROFILE,
    ))
    db.flush()
    db.add(DataRecordDB(
        record_id=f"REC_{pid}", player_id=pid, source_type="test", created_at=NOW,
        data_type="video", status="ok", original_file_path="synthetic/none.mp4",
        analysis_id="", schema_version="1", created_by=coach_id,
    ))
    db.flush()
    db.add(VideoDB(
        video_id=f"VID_{pid}", record_id=f"REC_{pid}", video_type="training",
        duration_seconds=1.0, recorded_at=NOW, session_id="S", location_id="L",
        capture_device="synthetic", resolution="1x1", frame_rate_fps=1.0,
        file_size_mb=0.0, file_format="mp4",
        # Not under /uploads/videos/, so the privacy service never asks the
        # storage layer to delete a physical file for this row.
        file_path=f"synthetic://none/{pid}.mp4", checksum="0",
        original_preserved=True, ai_processing_status="none",
        requires_human_review=False, review_reason="", human_review_status="none",
        analysis_approved=False,
    ))
    db.flush()
    db.add(GuardianConsentDB(
        consent_id=f"CON_{pid}", player_id=pid, guardian_name="G", guardian_email="g@example.test",
        verification_method="test", purposes=["video_analysis"], granted_at=NOW,
        expires_at=None, withdrawn_at=None, recorded_by_user_id=admin_id,
    ))
    db.add(TrackingSessionDB(
        session_id=f"TRK_{pid}", player_id=pid, coach_user_id=coach_id,
        client_recording_id=f"CLIENT_{pid}", video_id=f"VID_{pid}",
        tracking_mode="smart_soccer", status="completed", started_at=NOW, created_at=NOW,
    ))
    db.flush()
    for seq in (1, 2):
        db.add(TrackingSampleDB(
            session_id=f"TRK_{pid}", t_seconds=float(seq), gimbal_state="idle",
            tracking_mode="smart_soccer", tracking_status="tracking", sequence_number=seq,
        ))
    db.add(TrackingEventDB(
        event_id=f"EVT_{pid}", session_id=f"TRK_{pid}", occurred_at=NOW,
        event_type="player_lost", details={},
    ))
    db.add(TrackingFeatureSetDB(
        session_id=f"TRK_{pid}", feature_schema_version="1", features=[], computed_at=NOW,
    ))
    db.add(CoachValidationLabelDB(
        label_id=f"LBL_{pid}", session_id=f"TRK_{pid}", label_type="check",
        value={}, notes=None, coach_user_id=coach_id, created_at=NOW,
    ))
    db.add(PlayerAssessmentDB(
        assessment_id=f"ASM_{pid}", player_id=pid, pillar="physical",
        test_category="endurance", test_type="yoyo_kids", methodology_version="v1",
        test_date=date(2026, 9, 1), age_at_assessment_years=10, raw_data={"d": 400},
        calculated_metrics={}, ai_assisted=False, notes="synthetic note",
        recorded_by_user_id=coach_id, tracking_session_id=f"TRK_{pid}", created_at=NOW,
    ))
    db.flush()
    db.add(PlayerMetricDB(
        player_id=pid, assessment_id=f"ASM_{pid}", metric_name="distance",
        category="physical", metric_score=70.0, recorded_at=date(2026, 9, 1),
        source="test", created_at=NOW,
    ))
    db.add(MLPredictionDB(
        prediction_id=f"PRED_{pid}", player_id=pid, metric_name="distance",
        model_scope="player_specific", current_score=70.0, predicted_score=72.0,
        prediction_date=date(2026, 9, 1), target_date=date(2026, 10, 1), forecast_days=30,
        model_name="m", model_version="1", training_sample_count=3, created_at=NOW,
    ))
    db.flush()
    db.add(MonteCarloSimulationDB(
        simulation_id=f"SIM_{pid}", prediction_id=f"PRED_{pid}", player_id=pid,
        metric_name="distance", num_simulations=10, median=1, p05=1, p10=1, p25=1,
        p50=1, p75=1, p90=1, p95=1, simulation_method="m", uncertainty_level="low",
        residual_sample_count=3, created_at=NOW,
    ))
    db.add(PredictionOutcomeDB(
        outcome_id=f"OUT_{pid}", prediction_id=f"PRED_{pid}", player_id=pid,
        metric_name="distance", actual_score=71.0, actual_date=date(2026, 10, 1),
        prediction_error=-1.0, absolute_error=1.0, created_at=NOW,
    ))
    db.add(CoachMessageDB(
        player_id=pid, message="synthetic coach message", next_focus=["x"],
        updated_by_user_id=coach_id, updated_at=NOW,
    ))
    db.add(MLDatasetEntryDB(
        entry_id=f"DS_{pid}", video_id=f"VID_{pid}", age_band="U10", sex_cohort="male",
        camera_id="c", lighting="day", consent_id=f"CON_{pid}",
        flagged_by_user_id=coach_id, flagged_at=NOW,
    ))
    db.commit()


class Ctx:
    """A fresh in-memory database + logged-in clients for one test."""

    def __init__(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _fk_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)

    def db(self):
        return self.Session()

    def login(self, username: str) -> TestClient:
        c = TestClient(app)
        r = c.post("/auth/login",
                   json={"username": username, "password": f"{username}Password123!"})
        assert r.status_code == 200
        c.headers.update({"X-CSRF-Token": c.cookies.get(CSRF_COOKIE_NAME)})
        return c

    def count(self, model, **where) -> int:
        with self.Session() as db:
            q = select(func.count()).select_from(model)
            for key, value in where.items():
                q = q.where(getattr(model, key) == value)
            return db.execute(q).scalar_one()


@pytest.fixture()
def ctx(monkeypatch):
    c = Ctx()

    def override_get_db():
        db = c.Session()
        try:
            yield db
        finally:
            db.close()

    previous_override = app.dependency_overrides.get(get_db)
    previous_factory = app.state.auth_session_factory
    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = c.Session

    # Record any physical-file deletion the privacy service might request.
    deleted_files: list[str] = []

    class RecordingStorage:
        def delete(self, name):
            deleted_files.append(name)

    monkeypatch.setattr("app.video_storage.get_video_storage", lambda: RecordingStorage())
    c.deleted_files = deleted_files

    with c.db() as db:
        make_user(db, "ADMIN", "s0.admin", "admin")
        make_user(db, "COACH", "s0.coach", "coach")
        make_user(db, "GUARD_A", "s0.guard.a", "guardian")
        make_user(db, "GUARD_B", "s0.guard.b", "guardian")
        make_user(db, "GUARD_EMPTY", "s0.guard.empty", "guardian")
        db.commit()
        seed_child(db, "P_A", "COACH", "ADMIN")
        seed_child(db, "P_B", "COACH", "ADMIN")
        # A player with no assessment / tracking data at all.
        db.add(PlayerDB(
            player_id="P_EMPTY", first_name_ar="لاعب", last_name_ar="E",
            first_name_en="Player", last_name_en="E",
            date_of_birth=date(2016, 1, 1), sex="male",
            **{k: v for k, v in PROFILE.items() if k not in ("tactical_profile", "weak_foot_profile")},
        ))
        db.flush()
        db.add_all([
            GuardianPlayerLinkDB(guardian_user_id="GUARD_A", player_id="P_A",
                                 created_at=NOW, created_by_user_id="ADMIN"),
            GuardianPlayerLinkDB(guardian_user_id="GUARD_B", player_id="P_B",
                                 created_at=NOW, created_by_user_id="ADMIN"),
            GuardianPlayerLinkDB(guardian_user_id="GUARD_EMPTY", player_id="P_EMPTY",
                                 created_at=NOW, created_by_user_id="ADMIN"),
        ])
        db.commit()

    c.admin = c.login("s0.admin")
    c.coach = c.login("s0.coach")
    c.guard_a = c.login("s0.guard.a")
    c.guard_b = c.login("s0.guard.b")
    c.guard_empty = c.login("s0.guard.empty")
    yield c

    app.state.auth_session_factory = previous_factory
    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override
    c.engine.dispose()


def run_deletion(ctx: Ctx, guardian: TestClient, player_id: str, request_id: str):
    created = guardian.post(
        f"/guardian/children/{player_id}/deletion-requests",
        json={"request_id": request_id, "reason": "synthetic"},
    )
    assert created.status_code == 201
    review = ctx.admin.patch(f"/privacy-requests/{request_id}", json={"status": "in_review"})
    assert review.status_code == 200
    return ctx.admin.patch(f"/privacy-requests/{request_id}", json={"status": "completed"})


# ---- TEST 1 / 2 / 3 / 4: export ------------------------------------------

def test_export_contains_assessment_related_data(ctx):
    body = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    assert [a["assessment_id"] for a in body["player_assessments"]] == ["ASM_P_A"]
    assert [m["assessment_id"] for m in body["player_metrics"]] == ["ASM_P_A"]
    assert [p["prediction_id"] for p in body["ml_predictions"]] == ["PRED_P_A"]
    assert [s["simulation_id"] for s in body["monte_carlo_simulations"]] == ["SIM_P_A"]
    assert [o["outcome_id"] for o in body["prediction_outcomes"]] == ["OUT_P_A"]
    assert body["coach_message"]["message"] == "synthetic coach message"


def test_export_contains_tracking_data_without_raw_samples(ctx):
    body = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    (session,) = body["tracking_sessions"]
    assert session["session_id"] == "TRK_P_A"
    assert session["sample_count"] == 2
    assert [e["event_id"] for e in session["events"]] == ["EVT_P_A"]
    assert [l["label_id"] for l in session["coach_labels"]] == ["LBL_P_A"]
    assert session["feature_set"]["session_id"] == "TRK_P_A"
    # Raw telemetry rows stay staff-only: counted, never listed.
    assert "samples" not in session
    assert "pose_keypoints" not in str(body)


def test_export_excludes_other_players_data(ctx):
    text = str(ctx.guard_a.get("/guardian/children/P_A/data-export").json())
    assert "P_B" not in text
    assert "ASM_P_B" not in text and "TRK_P_B" not in text and "PRED_P_B" not in text


def test_export_for_player_without_assessment_or_tracking_data(ctx):
    body = ctx.guard_empty.get("/guardian/children/P_EMPTY/data-export").json()
    assert body["player_assessments"] == []
    assert body["player_metrics"] == []
    assert body["ml_predictions"] == []
    assert body["monte_carlo_simulations"] == []
    assert body["prediction_outcomes"] == []
    assert body["tracking_sessions"] == []
    assert body["coach_message"] is None


def test_export_has_no_file_paths_or_secrets(ctx):
    text = str(ctx.guard_a.get("/guardian/children/P_A/data-export").json())
    for forbidden in ("file_path", "original_file_path", "raw_output_path",
                      "password", "token_hash", "synthetic://"):
        assert forbidden not in text


def test_export_is_deterministic_apart_from_timestamp(ctx):
    first = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    second = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


# ---- TEST 9 / 10: authorization ------------------------------------------

def test_unauthorized_export_is_rejected(ctx):
    # Linked to another child, linked to none, and not signed in at all.
    assert ctx.guard_b.get("/guardian/children/P_A/data-export").status_code == 404
    assert ctx.guard_empty.get("/guardian/children/P_A/data-export").status_code == 404
    assert TestClient(app).get("/guardian/children/P_A/data-export").status_code == 401


def test_unauthorized_deletion_is_rejected(ctx):
    # A guardian cannot request deletion of a child they are not linked to.
    other = ctx.guard_b.post("/guardian/children/P_A/deletion-requests",
                             json={"request_id": "REQ_X"})
    assert other.status_code == 404
    # A guardian cannot complete or review a request; staff other than admin cannot either.
    ok = ctx.guard_a.post("/guardian/children/P_A/deletion-requests",
                          json={"request_id": "REQ_A"})
    assert ok.status_code == 201
    assert ctx.guard_a.patch("/privacy-requests/REQ_A", json={"status": "in_review"}).status_code == 403
    assert ctx.coach.patch("/privacy-requests/REQ_A", json={"status": "in_review"}).status_code == 403
    assert ctx.guard_a.get("/privacy-requests").status_code == 403
    # Nothing was deleted by any of the rejected calls.
    assert ctx.count(PlayerAssessmentDB, player_id="P_A") == 1


# ---- TEST 5 / 6 / 7 / 8: deletion -----------------------------------------

def test_deletion_removes_assessment_and_tracking_rows_only_for_that_player(ctx):
    result = run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"

    assert ctx.count(TrackingSessionDB, session_id="TRK_P_A") == 0
    assert ctx.count(TrackingSampleDB, session_id="TRK_P_A") == 0
    assert ctx.count(TrackingEventDB, session_id="TRK_P_A") == 0
    assert ctx.count(TrackingFeatureSetDB, session_id="TRK_P_A") == 0
    assert ctx.count(CoachValidationLabelDB, session_id="TRK_P_A") == 0
    assert ctx.count(PlayerAssessmentDB, player_id="P_A") == 0
    assert ctx.count(PlayerMetricDB, player_id="P_A") == 0
    assert ctx.count(MLPredictionDB, player_id="P_A") == 0
    assert ctx.count(MonteCarloSimulationDB, player_id="P_A") == 0
    assert ctx.count(PredictionOutcomeDB, player_id="P_A") == 0
    assert ctx.count(CoachMessageDB, player_id="P_A") == 0
    assert ctx.count(MLDatasetEntryDB, entry_id="DS_P_A") == 0
    assert ctx.count(VideoDB, video_id="VID_P_A") == 0
    assert ctx.count(GuardianConsentDB, player_id="P_A") == 0

    # Player B is untouched, row for row.
    assert ctx.count(TrackingSessionDB, session_id="TRK_P_B") == 1
    assert ctx.count(TrackingSampleDB, session_id="TRK_P_B") == 2
    assert ctx.count(TrackingEventDB, session_id="TRK_P_B") == 1
    assert ctx.count(TrackingFeatureSetDB, session_id="TRK_P_B") == 1
    assert ctx.count(CoachValidationLabelDB, session_id="TRK_P_B") == 1
    assert ctx.count(PlayerAssessmentDB, player_id="P_B") == 1
    assert ctx.count(PlayerMetricDB, player_id="P_B") == 1
    assert ctx.count(MLPredictionDB, player_id="P_B") == 1
    assert ctx.count(MonteCarloSimulationDB, player_id="P_B") == 1
    assert ctx.count(PredictionOutcomeDB, player_id="P_B") == 1
    assert ctx.count(CoachMessageDB, player_id="P_B") == 1
    assert ctx.count(MLDatasetEntryDB, entry_id="DS_P_B") == 1
    assert ctx.count(VideoDB, video_id="VID_P_B") == 1
    assert ctx.count(GuardianConsentDB, player_id="P_B") == 1


def test_deletion_keeps_anonymised_player_and_request_record(ctx):
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    with ctx.db() as db:
        player = db.get(PlayerDB, "P_A")
        assert (player.first_name_en, player.last_name_en) == ("Deleted", "Player")
        assert player.date_of_birth == date(1900, 1, 1)
        assert player.tactical_profile is None
        assert player.weak_foot_profile is None
        assert db.get(PrivacyRequestDB, "REQ_A").status == "completed"
        # The unrelated player keeps identity and optional profiles.
        other = db.get(PlayerDB, "P_B")
        assert other.first_name_en == "Player"
        assert other.tactical_profile == {"positioning": 61.0}


def test_shared_rows_are_unlinked_not_destroyed(ctx):
    """Another player's rows that point at the erased child's video or
    tracking session survive; only the dangling reference is cleared."""
    with ctx.db() as db:
        # P_B's tracking session was recorded against P_A's video, and P_B's
        # assessment is linked to P_A's tracking session.
        b_session = db.get(TrackingSessionDB, "TRK_P_B")
        b_session.video_id = "VID_P_A"
        db.get(PlayerAssessmentDB, "ASM_P_B").tracking_session_id = "TRK_P_A"
        db.commit()

    assert run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A").status_code == 200

    with ctx.db() as db:
        assert db.get(TrackingSessionDB, "TRK_P_B").video_id is None
        assert db.get(PlayerAssessmentDB, "ASM_P_B").tracking_session_id is None
    assert ctx.count(PlayerAssessmentDB, player_id="P_B") == 1
    assert ctx.count(TrackingSessionDB, player_id="P_B") == 1


def test_deletion_never_touches_physical_files(ctx):
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    assert ctx.deleted_files == []


def test_deletion_audit_event_has_counts_not_content(ctx):
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    with ctx.db() as db:
        event_row = db.query(AuditEventDB).filter(
            AuditEventDB.action == "child_deletion_completed"
        ).one()
    assert event_row.actor_user_id == "ADMIN"
    assert event_row.details["removed_counts"]["player_assessments"] == 1
    assert event_row.details["removed_counts"]["tracking_samples"] == 2
    assert "synthetic note" not in str(event_row.details)
    assert "synthetic coach message" not in str(event_row.details)


# ---- TEST 11: repeat safety -------------------------------------------------

def test_repeated_privacy_operations_are_safe(ctx):
    assert run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A").status_code == 200
    # Completing the same request again is rejected, not re-run.
    again = ctx.admin.patch("/privacy-requests/REQ_A", json={"status": "completed"})
    assert again.status_code == 409
    # The guardian link was removed with the data, so the child can no longer
    # be exported or have another deletion requested.
    assert ctx.guard_a.get("/guardian/children/P_A/data-export").status_code == 404
    assert ctx.guard_a.post("/guardian/children/P_A/deletion-requests",
                            json={"request_id": "REQ_A2"}).status_code == 404


def test_deleting_a_player_with_no_assessment_or_tracking_data(ctx):
    result = run_deletion(ctx, ctx.guard_empty, "P_EMPTY", "REQ_E")
    assert result.status_code == 200
    assert ctx.count(PlayerAssessmentDB, player_id="P_B") == 1


# ---- TEST 14: unrelated behaviour unchanged ---------------------------------

def test_ordinary_assessment_listing_is_unchanged(ctx):
    listing = ctx.coach.get("/players/P_A/assessments")
    assert listing.status_code == 200
    assert [a["assessment_id"] for a in listing.json()["assessments"]] == ["ASM_P_A"]
