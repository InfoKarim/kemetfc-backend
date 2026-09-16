from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import PlayerAssessmentDB
from app.services import development_report_service as service

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def add_ball_mastery(db, player_id, test_date, sole_rolls, cuts, l_turn, drag_back):
    db.add(PlayerAssessmentDB(
        assessment_id=f"ASSESS_{test_date}_{player_id}",
        player_id=player_id,
        pillar="technical",
        test_category="ball_mastery",
        test_type="ball_mastery",
        methodology_version="ball_mastery_1to5_v1",
        test_date=test_date,
        age_at_assessment_years=10,
        raw_data={
            "sole_rolls": sole_rolls,
            "inside_outside_cuts": cuts,
            "l_turn": l_turn,
            "drag_back": drag_back,
        },
        calculated_metrics={},
        ai_assisted=False,
        notes=None,
        recorded_by_user_id=None,
        created_at=service.utcnow(),
    ))
    db.commit()


def add_yoyo(db, player_id, test_date, distance):
    db.add(PlayerAssessmentDB(
        assessment_id=f"ASSESS_YOYO_{test_date}_{player_id}",
        player_id=player_id,
        pillar="physical",
        test_category="endurance",
        test_type="yoyo_kids",
        methodology_version="yoyo_kids_raw_distance_v1",
        test_date=test_date,
        age_at_assessment_years=10,
        raw_data={"level": 10, "shuttle": 2, "total_distance_m": distance},
        calculated_metrics={},
        ai_assisted=False,
        notes=None,
        recorded_by_user_id=None,
        created_at=service.utcnow(),
    ))
    db.commit()


def test_coverage_reports_zero_of_four_with_no_assessments():
    db = make_session()
    coverage = service.get_assessment_coverage(db, "P1")
    assert coverage == {
        "covered_pillars": [],
        "pillars": ["physical", "technical", "tactical", "coach"],
        "coverage_percent": 0,
    }


def test_coverage_counts_distinct_pillars_only_once():
    db = make_session()
    add_ball_mastery(db, "P1", date(2026, 9, 1), 4, 3, 3, 2)
    add_ball_mastery(db, "P1", date(2026, 9, 8), 5, 3, 3, 2)
    add_yoyo(db, "P1", date(2026, 9, 1), 400.0)

    coverage = service.get_assessment_coverage(db, "P1")
    assert coverage["covered_pillars"] == ["physical", "technical"]
    assert coverage["coverage_percent"] == 50


def test_strengths_from_high_ball_mastery_ratings():
    db = make_session()
    add_ball_mastery(db, "P1", date(2026, 9, 1), 5, 2, 4, 1)

    strengths, priorities = service.get_strengths_and_priorities(db, "P1")

    strength_titles = {s["title"] for s in strengths}
    assert "Sole Rolls" in strength_titles
    assert "L-Turn" in strength_titles

    priority_titles = {p["title"] for p in priorities}
    assert "Inside/Outside Cuts" in priority_titles
    assert "Drag-Back" in priority_titles


def test_strengths_and_priorities_cap_at_three():
    db = make_session()
    add_ball_mastery(db, "P1", date(2026, 9, 1), 5, 5, 5, 5)
    add_yoyo(db, "P1", date(2026, 8, 1), 300.0)
    add_yoyo(db, "P1", date(2026, 9, 1), 400.0)

    strengths, priorities = service.get_strengths_and_priorities(db, "P1")

    assert len(strengths) == 3
    assert priorities == []


def test_yoyo_improvement_is_a_strength_but_never_a_priority():
    db = make_session()
    add_yoyo(db, "P1", date(2026, 8, 1), 300.0)
    add_yoyo(db, "P1", date(2026, 9, 1), 250.0)  # regression, not improvement

    strengths, priorities = service.get_strengths_and_priorities(db, "P1")

    assert strengths == []
    assert priorities == []  # no fabricated "weak" judgment from a single number


def test_single_assessment_never_yields_a_progress_entry():
    db = make_session()
    add_yoyo(db, "P1", date(2026, 9, 1), 400.0)

    assert service.get_progress(db, "P1") == []


def test_progress_reports_latest_vs_previous_for_each_test_type():
    db = make_session()
    add_yoyo(db, "P1", date(2026, 8, 1), 300.0)
    add_yoyo(db, "P1", date(2026, 9, 1), 400.0)
    add_ball_mastery(db, "P1", date(2026, 8, 1), 2, 2, 2, 2)
    add_ball_mastery(db, "P1", date(2026, 9, 1), 4, 4, 4, 4)

    progress = service.get_progress(db, "P1")
    by_type = {p["test_type"]: p for p in progress}

    assert by_type["yoyo_kids"]["latest_value"] == 400.0
    assert by_type["yoyo_kids"]["previous_value"] == 300.0
    assert by_type["yoyo_kids"]["change"] == 100.0

    assert by_type["ball_mastery"]["latest_value"] == 4.0
    assert by_type["ball_mastery"]["previous_value"] == 2.0
    assert by_type["ball_mastery"]["change"] == 2.0


def test_coach_message_defaults_to_empty():
    db = make_session()
    assert service.get_coach_message(db, "P1") == {
        "message": None,
        "next_focus": [],
        "updated_at": None,
    }


def test_coach_message_can_be_set_and_overwritten():
    db = make_session()
    service.set_coach_message(
        db, "P1", "Great progress!", ["Scanning", "First touch"], "STAFF_1"
    )
    stored = service.get_coach_message(db, "P1")
    assert stored["message"] == "Great progress!"
    assert stored["next_focus"] == ["Scanning", "First touch"]

    service.set_coach_message(db, "P1", "Updated note", ["Acceleration"], "STAFF_1")
    updated = service.get_coach_message(db, "P1")
    assert updated["message"] == "Updated note"
    assert updated["next_focus"] == ["Acceleration"]


def test_build_development_report_never_includes_an_overall_score():
    db = make_session()
    add_ball_mastery(db, "P1", date(2026, 9, 1), 5, 3, 3, 2)

    report = service.build_development_report(db, "P1")

    assert "score" not in report
    assert "overall_score" not in report
    assert "player_development_score" not in report
    assert report["coverage"]["coverage_percent"] == 25
