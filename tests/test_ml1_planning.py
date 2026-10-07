"""ML-1: player-development comparison, explainable recommendations, and the safety rules of the whole package."""
import copy
import pathlib
import re
from datetime import date

from app.ml1 import development as dev
from app.ml1 import recommendations as rec

P = "AP-0123456789abcdef"


def m(value, when, **kw):
    base = {"anonymous_player_id": P, "drill_type": "20M_SPRINT_VIDEO", "protocol_id": "PROTO-1", "metric": "left_knee_angle_2d",
            "unit": "degrees", "definition_version": "angles_2d_v1", "analysis_version": "a1.0.0+tcn-v1", "value": value,
            "uncertainty": 2.0, "evidence_state": "MEASURED", "assessed_on": when,
            "capture": {"orientation": "portrait", "camera_moving_fraction": 0.1}}
    base.update(kw)
    return base


# ---- player development ---------------------------------------------------------------------------------------------------------
def test_a_change_bigger_than_the_uncertainty_is_reported_as_a_change_without_calling_it_better_unless_a_coach_agreed():
    r = dev.compare(m(120, date(2026, 9, 1)), m(140, date(2026, 10, 1)))
    assert r["result"] == dev.CHANGED and r["direction"] == "increased" and r["coach_agreed_judgement"] is None
    r2 = dev.compare(m(120, date(2026, 9, 1)), m(140, date(2026, 10, 1)), polarity="higher_is_better")
    assert r2["coach_agreed_judgement"] == "improved"
    r3 = dev.compare(m(140, date(2026, 9, 1)), m(120, date(2026, 10, 1)), polarity="higher_is_better")
    assert r3["coach_agreed_judgement"] == "declined" and r3["direction"] == "decreased"


def test_a_difference_inside_the_measurement_uncertainty_is_not_an_improvement():
    r = dev.compare(m(120, date(2026, 9, 1)), m(125, date(2026, 10, 1)))              # 5 < 2 * hypot(2, 2) = 5.66
    assert r["result"] == dev.NO_RELIABLE_CHANGE and r["reasons"] == ["DIFFERENCE_WITHIN_MEASUREMENT_UNCERTAINTY"]
    assert dev.compare(m(120, date(2026, 9, 1)), m(126, date(2026, 10, 1)))["result"] == dev.CHANGED


def test_unknown_uncertainty_means_insufficient_evidence_not_a_guess():
    r = dev.compare(m(120, date(2026, 9, 1), uncertainty=None), m(150, date(2026, 10, 1)))
    assert r["result"] == dev.INSUFFICIENT_EVIDENCE and r["reasons"] == ["MEASUREMENT_UNCERTAINTY_UNKNOWN"]


def test_a_real_change_smaller_than_what_matters_is_not_reported_as_one():
    r = dev.compare(m(100, date(2026, 9, 1), uncertainty=0.1), m(103, date(2026, 10, 1), uncertainty=0.1), minimum_meaningful=5.0)
    assert r["result"] == dev.NO_RELIABLE_CHANGE and r["reasons"] == ["CHANGE_SMALLER_THAN_THE_MINIMUM_THAT_MATTERS"]


def test_only_compatible_drills_protocols_definitions_players_and_models_are_compared():
    a = m(120, date(2026, 9, 1))
    for key, other in (("drill_type", "SHUTTLE"), ("protocol_id", "PROTO-2"), ("metric", "right_knee_angle_2d"), ("unit", "ratio"),
                       ("definition_version", "angles_2d_v2"), ("analysis_version", "a1.1.0+tcn-v2"), ("anonymous_player_id", "AP-ffffffffffffffff")):
        r = dev.compare(a, m(150, date(2026, 10, 1), **{key: other}))
        assert r["result"] == dev.NOT_COMPARABLE and f"DIFFERENT_{key.upper()}" in r["reasons"], key


def test_unmeasured_values_wrong_date_order_and_unsuitable_capture_are_not_comparable():
    a = m(120, date(2026, 9, 1))
    assert "NOT_A_MEASURED_VALUE" in dev.compare(a, m(None, date(2026, 10, 1), evidence_state="NOT_MEASURABLE"))["reasons"]
    assert "NOT_A_MEASURED_VALUE" in dev.compare(a, m(float("nan"), date(2026, 10, 1)))["reasons"]
    assert "DATES_NOT_IN_ORDER" in dev.compare(m(120, date(2026, 10, 1)), m(130, date(2026, 9, 1)))["reasons"]
    assert "DATES_NOT_IN_ORDER" in dev.compare(a, m(130, date(2026, 9, 1)))["reasons"]
    assert "DIFFERENT_CAMERA_ORIENTATION" in dev.compare(a, m(130, date(2026, 10, 1), capture={"orientation": "landscape", "camera_moving_fraction": 0.1}))["reasons"]
    assert "CAMERA_MOVING_TOO_MUCH_FOR_IMAGE_MOTION_METRICS" in dev.compare(a, m(130, date(2026, 10, 1), capture={"orientation": "portrait", "camera_moving_fraction": 0.9}))["reasons"]


def test_a_series_compares_each_session_only_with_the_previous_comparable_one():
    series = dev.series([m(120, date(2026, 8, 1)), m(121, date(2026, 9, 1)), m(150, date(2026, 10, 1)),
                         m(10, date(2026, 9, 15), drill_type="SHUTTLE")])
    assert [(s["from"], s["to"], s["result"]) for s in series] == [("2026-08-01", "2026-09-01", dev.NO_RELIABLE_CHANGE),
                                                                    ("2026-09-01", "2026-10-01", dev.CHANGED)]


def test_the_development_module_has_no_rating_overall_score_or_potential():
    text = pathlib.Path("app/ml1/development.py").read_text().lower()
    for word in ("rating", "overall", "potential", "talent", "fifa", "score"):
        assert word not in re.sub(r'"""[\s\S]*?"""', "", text), word


# ---- recommendations --------------------------------------------------------------------------------------------------------------
DRILLS = [
    {"drill_id": "D1", "name": "Cone weave", "category": "dribbling", "min_age": 8, "max_age": 12, "difficulty": "beginner", "duration_minutes": 10, "equipment": ["cones", "ball"], "active": True},
    {"drill_id": "D2", "name": "Wall passes", "category": "passing", "min_age": 8, "max_age": 14, "difficulty": "beginner", "duration_minutes": 15, "equipment": ["ball", "wall"], "active": True},
    {"drill_id": "D3", "name": "Ladder feet", "category": "agility", "min_age": 10, "max_age": 16, "difficulty": "intermediate", "duration_minutes": 10, "equipment": ["ladder"], "active": True},
    {"drill_id": "D4", "name": "Retired drill", "category": "dribbling", "min_age": 8, "max_age": 12, "difficulty": "beginner", "duration_minutes": 5, "equipment": [], "active": False},
    {"drill_id": "D5", "name": "Long dribble circuit", "category": "dribbling", "min_age": 8, "max_age": 12, "difficulty": "beginner", "duration_minutes": 40, "equipment": ["cones"], "active": True},
    {"drill_id": "D6", "name": "Weaving 2", "category": "dribbling", "min_age": 6, "max_age": 9, "difficulty": "beginner", "duration_minutes": 8, "equipment": ["cones"], "active": True},
]
WEAK = [{"category": "dribbling", "severity": "high", "coach_reviewed": True}, {"category": "passing", "severity": "medium", "coach_reviewed": True},
        {"category": "agility", "severity": "high", "coach_reviewed": False}]


def run(**kw):
    args = dict(age=10, weaknesses=WEAK, drills=DRILLS, history=[], equipment=["cones", "ball", "wall"], session_minutes=30, today=date(2026, 10, 8))
    args.update(kw)
    return rec.recommend(**args)


def excluded(result):
    return {e["drill_id"]: e["reason"] for e in result["excluded"]}


def test_every_recommendation_carries_its_reasons_and_says_it_is_unvalidated_and_not_neural():
    r = run()
    assert r["method"] == "EXPLAINABLE_RULES_V1" and r["validated"] is False and r["neural_network_used"] is False
    assert "not validated" in r["disclaimer"].lower() and "No neural network" in r["disclaimer"]
    assert [p["drill_id"] for p in r["plan"]] == ["D1", "D2"] and r["total_minutes"] == 25
    for item in r["plan"]:
        assert any("coach-reviewed" in why for why in item["reasons"]) and any("age" in why for why in item["reasons"])


def test_only_coach_reviewed_weaknesses_drive_recommendations():
    r = run()
    assert [i["category"] for i in r["ignored_weaknesses"]] == ["agility"] and r["ignored_weaknesses"][0]["reason"] == "NOT_COACH_REVIEWED"
    with_ladder = run(equipment=["cones", "ball", "wall", "ladder"])
    assert excluded(with_ladder)["D3"] == "NO_COACH_REVIEWED_WEAKNESS_IN_THIS_CATEGORY"        # the agility weakness is not coach-reviewed
    assert "D3" not in [p["drill_id"] for p in with_ladder["plan"]]
    assert run(weaknesses=[])["plan"] == []
    assert run(weaknesses=[{"category": "dribbling", "severity": "high", "coach_reviewed": "yes"}])["plan"] == []


def test_age_equipment_activity_and_session_length_are_hard_rules_with_stated_reasons():
    r = run()
    ex = excluded(r)
    assert ex["D4"] == "NOT_ACTIVE" and "LONGER_THAN_THE_SESSION" == ex["D5"] and ex["D6"].startswith("NOT_AGE_APPROPRIATE")
    r2 = run(equipment=["ball"])
    assert excluded(r2)["D1"].startswith("EQUIPMENT_NOT_AVAILABLE: cones") and excluded(r2)["D2"].startswith("EQUIPMENT_NOT_AVAILABLE: wall")
    by_id = {d["drill_id"]: d for d in DRILLS}
    assert [p["drill_id"] for p in run(age=7)["plan"]] == ["D6"]                                 # only the drill made for a 7-year-old
    assert all(by_id[p["drill_id"]]["min_age"] <= 10 <= by_id[p["drill_id"]]["max_age"] for p in r["plan"])


def test_the_plan_never_exceeds_the_session_and_gives_the_reason_for_a_drill_that_did_not_fit():
    r = run(session_minutes=12)
    assert r["total_minutes"] <= 12 and [p["drill_id"] for p in r["plan"]] == ["D1"]
    assert excluded(r)["D2"] == "LONGER_THAN_THE_SESSION"
    r = run(session_minutes=25)
    assert r["total_minutes"] == 25 and {p["drill_id"] for p in r["plan"]} == {"D1", "D2"}
    assert len(run(session_minutes=300)["plan"]) <= 4


def test_recent_practice_is_rested_and_old_practice_adds_variety():
    done = [{"drill_id": "D1", "date": date(2026, 10, 5)}]
    r = run(history=done)
    assert excluded(r)["D1"] == "DONE_3_DAYS_AGO_WITHIN_7_DAY_REST" and "D1" not in [p["drill_id"] for p in r["plan"]]
    old = run(history=[{"drill_id": "D1", "date": date(2026, 8, 1)}])
    assert "D1" in [p["drill_id"] for p in old["plan"]]
    fresh = {p["drill_id"]: p for p in run(history=[{"drill_id": "D2", "date": date(2026, 10, 1)}, {"drill_id": "D2", "date": date(2026, 8, 1)}])["plan"]}
    assert "D1" in fresh
    d1 = next(p for p in run()["plan"] if p["drill_id"] == "D1")
    assert any("variety" in why for why in d1["reasons"])


def test_higher_severity_is_ordered_first_and_the_order_is_deterministic():
    r = run()
    assert [p["drill_id"] for p in r["plan"]][0] == "D1"                      # the high-severity weakness first
    assert run() == run()
    gear = ["cones", "ball", "wall", "ladder"]
    harder = [{**DRILLS[2], "category": "dribbling"}]                                           # an intermediate drill that fits everything else
    assert [p["drill_id"] for p in run(drills=harder, equipment=gear)["plan"]] == ["D3"]
    again = run(level="beginner", drills=harder, equipment=gear)
    assert again["plan"] == [] and excluded(again)["D3"] == "ABOVE_THE_PLAYER_LEVEL_SET_BY_THE_COACH"


def test_a_recommendation_never_contains_a_diagnosis_rating_or_prediction():
    text = str(run()).lower()
    for word in ("diagnos", "injury", "talent", "potential", "fifa", "rating", "predict", "disorder", "anxiety", "adhd"):
        assert word not in text, word


# ---- safety across the package ---------------------------------------------------------------------------------------------------------
def test_the_ml_package_has_no_ratings_scores_diagnoses_or_fifa_style_output():
    for path in pathlib.Path("app/ml1").glob("*.py"):
        code = re.sub(r'"""[\s\S]*?"""|#.*', "", path.read_text()).lower()
        for word in ("fifa", "overall_rating", "potential_score", "talent", "diagnos", "injury_risk"):
            assert word not in code, (path.name, word)


def test_the_ml_package_does_not_touch_camera_upload_telemetry_dockkit_or_the_database():
    for path in pathlib.Path("app/ml1").glob("*.py"):
        if path.name == "registry_adapter.py":
            continue                                          # read-only consent lookup, tested in test_ml1_registry_gate.py
        text = path.read_text()
        for word in ("DockKit", "CameraCapture", "AVCapture", "telemetry", "alembic", "SessionLocal", "db_models", "get_db"):
            assert word not in text.replace("telemetry or", ""), (path.name, word)


def test_private_data_and_models_are_ignored_by_git_and_no_media_is_tracked():
    ignore = pathlib.Path(".gitignore").read_text()
    for needed in ("datasets/", "models_ml1/", "*.npz"):
        assert needed in ignore, needed
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "app/ml1", "tests", "ml"], capture_output=True, text=True).stdout.split()
    assert not [t for t in tracked if t.endswith((".mov", ".mp4", ".jpg", ".png", ".npz", ".pt"))]
