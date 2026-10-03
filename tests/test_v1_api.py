"""Assessment V1 manual-measurement API (S1B1).

Synthetic data, scratch databases, foreign keys enforced. See tests/conftest.py.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.db_models import AuditEventDB, PlayerAssessmentDB, PlayerDB, UserDB
from app.services.auth_service import utcnow
from app.services.privacy_service import PrivacyService
from app.v1.definitions import ensure_metric_definitions
from app.v1.models import (
    V1AssessmentDB,
    V1ManualMeasurementCorrectionDB,
    V1ManualMeasurementDB,
    V1StationRunDB,
    V1TrialDB,
)
from main import CSRF_COOKIE_NAME, app
from tests.test_privacy_s0 import ctx, make_user, run_deletion  # noqa: F401  (ctx is a fixture)

SPRINT = "PHYS-SPR-020M-MAN-001"
_counter = iter(range(1, 10_000))


def key(prefix="k"):
    return f"{prefix}-{next(_counter):06d}-client"


@pytest.fixture()
def api(ctx):  # noqa: F811
    with ctx.db() as db:
        ensure_metric_definitions(db)
        make_user(db, "REVIEWER", "s1b.reviewer", "reviewer")
        make_user(db, "COACH2", "s1b.coach2", "coach")
        db.commit()
        service = PrivacyService(db)
        for pid in ("P_A", "P_B"):
            service.grant_consent(f"PT_{pid}", pid, "G", "g@example.test", "in_person",
                                  ["performance_tracking"], "ADMIN")
    ctx.reviewer = ctx.login("s1b.reviewer")
    ctx.coach2 = ctx.login("s1b.coach2")
    return ctx


def create_assessment(client, player="P_A", **extra):
    body = {"player_id": player, "assessment_date": "2026-09-12", "client_entry_id": key("asm"),
            "title": "Fall Assessment", **extra}
    return client.post("/v1/assessments", json=body)


def setup_trial(api, player="P_A", attempt=1, client=None):
    client = client or api.coach
    assessment = create_assessment(client, player).json()
    run = client.post(f"/v1/assessments/{assessment['assessment_id']}/station-runs",
                      json={"station_id": "S1"}).json()
    trial = client.post(f"/v1/station-runs/{run['station_run_id']}/trials",
                        json={"attempt_number": attempt}).json()
    return assessment, run, trial


def enter(client, trial_id, value="4.25", **extra):
    body = {"metric_id": SPRINT, "displayed_value": value, "client_entry_id": key("mm"), **extra}
    return client.post(f"/v1/trials/{trial_id}/manual-measurements", json=body)


def correct(client, mid, new, expected, confirmer="COACH2", reason="TYPING_ERROR", **extra):
    return client.post(f"/v1/manual-measurements/{mid}/corrections", json={
        "corrected_value": new, "expected_effective_value": expected, "reason_code": reason,
        "confirmed_by_user_id": confirmer, **extra})


def count(api, model, **where):
    with api.db() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalar_one()


# ---- 1-6 assessment creation, authorization, consent ----------------------------------

def test_authorized_staff_create_assessment_with_kemet_v1(api):
    for client in (api.coach, api.admin):
        response = create_assessment(client)
        assert response.status_code == 201
        body = response.json()
        assert body["assessment_id"].startswith("V1A") and body["protocol_code"] == "KEMET_V1"
        assert body["player_id"] == "P_A" and body["status"] == "IN_PROGRESS"


def test_unauthenticated_user_cannot_create_assessment(api):
    response = TestClient(app).post("/v1/assessments", json={
        "player_id": "P_A", "assessment_date": "2026-09-12", "client_entry_id": key()})
    assert response.status_code == 401


@pytest.mark.parametrize("who", ["guard_a", "reviewer"])
def test_wrong_role_cannot_create_assessment(api, who):
    response = create_assessment(getattr(api, who))
    assert response.status_code == 403
    assert count(api, V1AssessmentDB) == 0


def test_missing_consent_fails_closed_and_valid_consent_allows(api):
    # P_EMPTY has no consent at all; P_A has a video-only consent that must not count.
    assert create_assessment(api.coach, "P_EMPTY").status_code == 403
    with api.db() as db:
        db.query(UserDB)  # noqa: B018
        service = PrivacyService(db)
        service.withdraw_consent("PT_P_A", "ADMIN")
    assert create_assessment(api.coach, "P_A").status_code == 403
    with api.db() as db:
        PrivacyService(db).grant_consent("PT_P_A_NEW", "P_A", "G", "g@example.test", "in_person",
                                         ["performance_tracking"], "ADMIN")
    assert create_assessment(api.coach, "P_A").status_code == 201
    assert count(api, V1AssessmentDB) == 1


def test_expired_consent_does_not_count(api):
    with api.db() as db:
        PrivacyService(db).withdraw_consent("PT_P_A", "ADMIN")
        row = PrivacyService(db).grant_consent("PT_X", "P_A", "G", "g@example.test", "in_person",
                                               ["performance_tracking"], "ADMIN",
                                               expires_at=utcnow().replace(year=2099))
        row.expires_at = datetime(2020, 1, 1)
        db.commit()
    assert create_assessment(api.coach, "P_A").status_code == 403


def test_unknown_player_is_404_and_extra_fields_are_rejected(api):
    assert create_assessment(api.coach, "NOPE").status_code == 404
    for extra in ({"overall_score": 80}, {"age_band": "U10"}, {"talent_score": 1}, {"ai_confidence": 0.9},
                  {"protocol_code": "LEGACY"}, {"status": "COMPLETE"}):
        assert create_assessment(api.coach, **extra).status_code == 422, extra
    assert count(api, V1AssessmentDB) == 0


# ---- 7-9 station run and trial -----------------------------------------------------------

def test_station_run_and_trial_creation_and_retry(api):
    assessment = create_assessment(api.coach).json()
    path = f"/v1/assessments/{assessment['assessment_id']}/station-runs"
    first = api.coach.post(path, json={"station_id": "S1"})
    again = api.coach.post(path, json={"station_id": "S1"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["station_run_id"] == again.json()["station_run_id"]
    assert count(api, V1StationRunDB) == 1
    run_id = first.json()["station_run_id"]
    t1 = api.coach.post(f"/v1/station-runs/{run_id}/trials", json={"attempt_number": 1})
    retry = api.coach.post(f"/v1/station-runs/{run_id}/trials", json={"attempt_number": 1})
    assert (t1.status_code, retry.status_code) == (201, 200)
    assert t1.json()["trial_id"] == retry.json()["trial_id"] and count(api, V1TrialDB) == 1
    assert t1.json()["validity"] == "VALID"
    conflict = api.coach.post(f"/v1/station-runs/{run_id}/trials",
                              json={"attempt_number": 1, "validity": "INVALID", "invalid_reason": "FALSE_START"})
    assert conflict.status_code == 409 and count(api, V1TrialDB) == 1


def test_only_the_sprint_station_is_open_in_this_api_version(api):
    assessment = create_assessment(api.coach).json()
    path = f"/v1/assessments/{assessment['assessment_id']}/station-runs"
    for station in ("S2", "S3", "S4", "S5", "S6", "S9"):
        assert api.coach.post(path, json={"station_id": station}).status_code == 422
    assert api.coach.post(path, json={"station_id": "S1", "series_key": "L"}).status_code == 422
    assert count(api, V1StationRunDB) == 0


def test_invalid_trial_uses_protocol_reasons_and_timer_failure_is_not_one(api):
    assessment, run, _ = setup_trial(api)
    path = f"/v1/station-runs/{run['station_run_id']}/trials"
    ok = api.coach.post(path, json={"attempt_number": 2, "validity": "INVALID", "invalid_reason": "FALSE_START"})
    assert ok.status_code == 201 and ok.json()["invalid_reason"] == "FALSE_START"
    for bad in ({"attempt_number": 3, "validity": "INVALID"},
                {"attempt_number": 3, "validity": "INVALID", "invalid_reason": "TIMER_FAILURE"},
                {"attempt_number": 3, "validity": "INVALID", "invalid_reason": "MISSED_CONE"},
                {"attempt_number": 3, "validity": "VALID", "invalid_reason": "FALSE_START"},
                {"attempt_number": 0}, {"attempt_number": "1"}, {"attempt_number": 1.5}):
        assert api.coach.post(path, json=bad).status_code == 422, bad


# ---- 10-15 manual entry contract ---------------------------------------------------------

def test_entering_four_twenty_five_is_exact_everywhere(api):
    _, _, trial = setup_trial(api)
    response = enter(api.coach, trial["trial_id"], "4.25")
    assert response.status_code == 201
    body = response.json()
    assert '"displayed_value":"4.25"' in response.text.replace(" ", "")  # a string, never a JSON float
    assert body["displayed_value"] == "4.25" and isinstance(body["displayed_value"], str)
    assert (body["unit"], body["measurement_source"], body["measurement_method"], body["entry_status"]) == (
        "seconds", "HUMAN_MANUAL", "MANUAL_STOPWATCH", "RECORDED")
    assert body["entered_by"] == "COACH" and body["entered_at"] and body["metric_id"] == SPRINT
    assert body["trial_id"] == trial["trial_id"] and body["measurement_id"].startswith("V1MM")
    with api.db() as db:
        row = db.get(V1ManualMeasurementDB, body["measurement_id"])
        assert row.value_centiseconds == 425
    assert api.coach.get(f"/v1/manual-measurements/{body['measurement_id']}").json()["displayed_value"] == "4.25"


def test_trailing_zero_is_preserved_in_the_response(api):
    _, _, trial = setup_trial(api)
    assert enter(api.coach, trial["trial_id"], "4.2").json()["displayed_value"] == "4.20"


def test_entered_by_comes_from_the_session_not_the_client(api):
    _, _, trial = setup_trial(api)
    assert enter(api.coach, trial["trial_id"], entered_by="ADMIN").status_code == 422
    assert enter(api.admin, trial["trial_id"]).json()["entered_by"] == "ADMIN"


@pytest.mark.parametrize("extra", [{"confidence": 0.9}, {"ai_confidence": 0.9}, {"model_name": "x"},
                                   {"overall_score": 80}, {"score": 4}, {"age_band": "U9"}])
def test_ai_confidence_and_scores_are_not_accepted(api, extra):
    _, _, trial = setup_trial(api)
    assert enter(api.coach, trial["trial_id"], **extra).status_code == 422
    assert count(api, V1ManualMeasurementDB) == 0


def test_the_response_has_no_confidence_or_score_fields(api):
    _, _, trial = setup_trial(api)
    body = enter(api.coach, trial["trial_id"]).json()
    assert not [k for k in body if any(w in k for w in ("confidence", "score", "model", "ai_"))]


# ---- 16-22 value validation ---------------------------------------------------------------

@pytest.mark.parametrize("bad", ["-4.25", "0", "0.00", "4.255", "abc", "", " ", "4,25", "NaN", "Infinity",
                                 "-Infinity", "nan", "inf", "1e1", "4.", ".5", "+4.25", "6000.00"])
def test_bad_values_are_rejected_and_nothing_is_stored(api, bad):
    _, _, trial = setup_trial(api)
    assert enter(api.coach, trial["trial_id"], bad).status_code == 422, bad
    assert count(api, V1ManualMeasurementDB) == 0


@pytest.mark.parametrize("raw", [4.25, 0, -1, True, None, [4.25], {"v": 1}])
def test_json_numbers_and_non_strings_are_rejected(api, raw):
    _, _, trial = setup_trial(api)
    response = api.coach.post(f"/v1/trials/{trial['trial_id']}/manual-measurements", json={
        "metric_id": SPRINT, "displayed_value": raw, "client_entry_id": key()})
    assert response.status_code == 422
    assert count(api, V1ManualMeasurementDB) == 0


def test_a_suspicious_value_is_kept_exactly_as_entered(api):
    _, _, trial = setup_trial(api)
    body = enter(api.coach, trial["trial_id"], "42.50").json()
    assert body["displayed_value"] == "42.50" and body["effective_value"] == "42.50"
    assert "warning" not in json.dumps(body).lower()
    with api.db() as db:
        assert db.get(V1ManualMeasurementDB, body["measurement_id"]).value_centiseconds == 4250


def test_wrong_metric_unit_source_or_missing_trial_is_rejected(api):
    _, _, trial = setup_trial(api)
    tid = trial["trial_id"]
    for metric in ("PHYS-COD-180-MAN-001", "TECH-DRB-SLALOM-TIME-MAN-001", "NOPE", "PHYS-SPR-010M-MAN-001"):
        assert enter(api.coach, tid, metric_id=metric).status_code == 422, metric
    for unit in ("meters", "percent", "kilograms", "s", ""):
        assert enter(api.coach, tid, unit=unit).status_code == 422, unit
    for source in ("AI", "VIDEO", "TIMING_GATE", "HUMAN_CODED", ""):
        assert enter(api.coach, tid, measurement_source=source).status_code == 422, source
    assert enter(api.coach, "NOPE").status_code == 404
    assert count(api, V1ManualMeasurementDB) == 0
    ok = enter(api.coach, tid, unit="seconds", measurement_source="HUMAN_MANUAL")
    assert ok.status_code == 201


# ---- 25-26 NOT_RECORDED --------------------------------------------------------------------

def test_timer_failure_is_not_recorded_without_a_number_and_the_trial_stays_valid(api):
    assessment, _, trial = setup_trial(api)
    response = api.coach.post(f"/v1/trials/{trial['trial_id']}/manual-measurements", json={
        "metric_id": SPRINT, "entry_status": "NOT_RECORDED", "not_recorded_reason": "TIMER_FAILURE",
        "client_entry_id": key()})
    assert response.status_code == 201
    body = response.json()
    assert body["entry_status"] == "NOT_RECORDED" and body["displayed_value"] is None
    assert body["effective_value"] is None and body["not_recorded_reason"] == "TIMER_FAILURE"
    with api.db() as db:
        row = db.get(V1ManualMeasurementDB, body["measurement_id"])
        assert row.value_centiseconds is None
        assert db.get(V1TrialDB, trial["trial_id"]).validity == "VALID"
    tree = api.coach.get(f"/v1/assessments/{assessment['assessment_id']}").json()
    assert tree["station_runs"][0]["trials"][0]["validity"] == "VALID"
    # The child repeats: attempt 2 is accepted and gets a real time.
    run_id = trial["station_run_id"]
    t2 = api.coach.post(f"/v1/station-runs/{run_id}/trials", json={"attempt_number": 2}).json()
    assert enter(api.coach, t2["trial_id"], "4.31").status_code == 201


@pytest.mark.parametrize("fields", [
    {"entry_status": "NOT_RECORDED"},
    {"entry_status": "NOT_RECORDED", "not_recorded_reason": "SLOW"},
    {"entry_status": "NOT_RECORDED", "not_recorded_reason": "TIMER_FAILURE", "displayed_value": "0"},
    {"entry_status": "NOT_RECORDED", "not_recorded_reason": "TIMER_FAILURE", "displayed_value": "999"},
    {"entry_status": "RECORDED", "not_recorded_reason": "TIMER_FAILURE", "displayed_value": "4.25"},
    {"entry_status": "RECORDED"},
])
def test_inconsistent_entry_shapes_are_rejected(api, fields):
    _, _, trial = setup_trial(api)
    body = {"metric_id": SPRINT, "client_entry_id": key(), **fields}
    assert api.coach.post(f"/v1/trials/{trial['trial_id']}/manual-measurements", json=body).status_code == 422
    assert count(api, V1ManualMeasurementDB) == 0


# ---- 27-31 corrections -----------------------------------------------------------------------

def test_correction_keeps_the_original_and_the_whole_history(api):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    first = correct(api.coach, mid, "4.25", "4.52")
    assert first.status_code == 201
    body = first.json()
    assert body["correction"]["original_value"] == "4.52" and body["correction"]["corrected_value"] == "4.25"
    assert body["measurement"]["original_value"] == "4.52" and body["measurement"]["effective_value"] == "4.25"
    second = correct(api.coach, mid, "4.35", "4.25", reason="MISREAD_DISPLAY")
    assert second.status_code == 201
    view = api.coach.get(f"/v1/manual-measurements/{mid}").json()
    assert (view["displayed_value"], view["original_value"], view["effective_value"]) == ("4.52", "4.52", "4.35")
    assert [(c["original_value"], c["corrected_value"], c["sequence_number"]) for c in view["corrections"]] == [
        ("4.52", "4.25", 1), ("4.25", "4.35", 2)]
    c1 = view["corrections"][0]
    assert (c1["corrected_by"], c1["confirmed_by"], c1["reason_code"]) == ("COACH", "COACH2", "TYPING_ERROR")
    assert c1["corrected_at"]
    with api.db() as db:
        assert db.get(V1ManualMeasurementDB, mid).value_centiseconds == 452


def test_correction_replay_and_stale_requests(api):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    assert correct(api.coach, mid, "4.25", "4.52").status_code == 201
    assert correct(api.coach, mid, "4.25", "4.52").status_code == 200      # SAVE pressed twice
    assert count(api, V1ManualMeasurementCorrectionDB) == 1
    assert correct(api.coach, mid, "4.35", "4.52").status_code == 409      # stale: someone changed it
    assert correct(api.coach, mid, "4.35", "4.25").status_code == 201
    assert correct(api.coach, mid, "4.25", "4.52").status_code == 409      # old replay after a later change
    assert count(api, V1ManualMeasurementCorrectionDB) == 2


@pytest.mark.parametrize("kwargs", [
    dict(new="4.52", expected="4.52"),              # no change
    dict(new="0", expected="4.52"), dict(new="4.255", expected="4.52"), dict(new="x", expected="4.52"),
    dict(new="4.25", expected="not-a-number"),
    dict(new="4.25", expected="4.52", reason="OTHER"),            # OTHER needs text
    dict(new="4.25", expected="4.52", reason="BECAUSE"),
    dict(new="4.25", expected="4.52", confirmer="COACH"),           # confirming yourself
    dict(new="4.25", expected="4.52", confirmer="NOBODY"),
    dict(new="4.25", expected="4.52", confirmer="GUARD_A"),         # a guardian cannot confirm
    dict(new="4.25", expected="4.52", confirmer="REVIEWER"),
])
def test_bad_corrections_are_rejected(api, kwargs):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    response = correct(api.coach, mid, kwargs["new"], kwargs["expected"],
                       confirmer=kwargs.get("confirmer", "COACH2"), reason=kwargs.get("reason", "TYPING_ERROR"))
    assert response.status_code in (409, 422), kwargs
    assert count(api, V1ManualMeasurementCorrectionDB) == 0


def test_a_confirmer_is_required_by_the_frozen_standard(api):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    response = api.coach.post(f"/v1/manual-measurements/{mid}/corrections", json={
        "corrected_value": "4.25", "expected_effective_value": "4.52", "reason_code": "TYPING_ERROR"})
    assert response.status_code == 422


def test_other_reason_with_text_is_accepted_and_not_recorded_cannot_be_corrected(api):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    assert correct(api.coach, mid, "4.25", "4.52", reason="OTHER", reason_text="read the wrong line").status_code == 201
    t2 = api.coach.post(f"/v1/station-runs/{trial['station_run_id']}/trials", json={"attempt_number": 2}).json()
    missing = api.coach.post(f"/v1/trials/{t2['trial_id']}/manual-measurements", json={
        "metric_id": SPRINT, "entry_status": "NOT_RECORDED", "not_recorded_reason": "TIMER_FAILURE",
        "client_entry_id": key()}).json()["measurement_id"]
    assert correct(api.coach, missing, "4.25", "4.52").status_code in (409, 422)


@pytest.mark.parametrize("who", ["guard_a", "reviewer"])
def test_unauthorised_roles_cannot_correct_or_enter(api, who):
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    client = getattr(api, who)
    assert correct(client, mid, "4.25", "4.52").status_code == 403
    assert enter(client, trial["trial_id"], "4.00").status_code == 403
    assert TestClient(app).post(f"/v1/manual-measurements/{mid}/corrections", json={}).status_code == 401
    assert api.coach.get(f"/v1/manual-measurements/{mid}").json()["effective_value"] == "4.52"


def test_reviewer_may_read_but_never_alter_human_manual_times(api):
    assessment, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    assert api.reviewer.get(f"/v1/manual-measurements/{mid}").status_code == 200
    assert api.reviewer.get(f"/v1/assessments/{assessment['assessment_id']}").status_code == 200
    for method, path in (("post", f"/v1/manual-measurements/{mid}/corrections"),
                         ("post", f"/v1/trials/{trial['trial_id']}/manual-measurements"),
                         ("post", "/v1/assessments"),
                         ("put", f"/v1/manual-measurements/{mid}"), ("patch", f"/v1/manual-measurements/{mid}"),
                         ("delete", f"/v1/manual-measurements/{mid}")):
        assert api.reviewer.request(method.upper(), path, json={}).status_code in (403, 405, 422)
    assert api.guard_a.get(f"/v1/manual-measurements/{mid}").status_code == 403
    assert TestClient(app).get(f"/v1/manual-measurements/{mid}").status_code == 401
    assert count(api, V1ManualMeasurementCorrectionDB) == 0


# ---- 32-34 idempotency and concurrency ----------------------------------------------------------

def test_pressing_save_twice_creates_one_measurement(api):
    _, _, trial = setup_trial(api)
    k = key("save")
    first = enter(api.coach, trial["trial_id"], "4.25", client_entry_id=k)
    second = enter(api.coach, trial["trial_id"], "4.25", client_entry_id=k)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["measurement_id"] == second.json()["measurement_id"]
    assert count(api, V1ManualMeasurementDB) == 1
    audit = [e for e in api_audit(api) if e.action == "v1_manual_measurement_recorded"]
    assert len(audit) == 1


def api_audit(api):
    with api.db() as db:
        return db.query(AuditEventDB).all()


def test_same_key_with_a_different_payload_conflicts_and_changes_nothing(api):
    _, _, trial = setup_trial(api)
    k = key("save")
    assert enter(api.coach, trial["trial_id"], "4.25", client_entry_id=k).status_code == 201
    assert enter(api.coach, trial["trial_id"], "4.26", client_entry_id=k).status_code == 409
    other = api.coach.post(f"/v1/station-runs/{trial['station_run_id']}/trials", json={"attempt_number": 2}).json()
    assert enter(api.coach, other["trial_id"], "4.25", client_entry_id=k).status_code == 409
    assert enter(api.coach, trial["trial_id"], "4.30").status_code == 409     # new key, same trial and metric
    assert count(api, V1ManualMeasurementDB) == 1
    assert api.coach.get(f"/v1/assessments/{create_assessment_id(api, trial)}").json()


def create_assessment_id(api, trial):
    with api.db() as db:
        return db.get(V1StationRunDB, trial["station_run_id"]).assessment_id


def test_assessment_creation_is_idempotent_and_conflicts_on_a_changed_payload(api):
    k = key("asm")
    body = {"player_id": "P_A", "assessment_date": "2026-09-12", "client_entry_id": k}
    first = api.coach.post("/v1/assessments", json=body)
    again = api.coach.post("/v1/assessments", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["assessment_id"] == again.json()["assessment_id"]
    assert api.coach.post("/v1/assessments", json={**body, "player_id": "P_B"}).status_code == 409
    assert api.coach.post("/v1/assessments", json={**body, "assessment_date": "2026-10-01"}).status_code == 409
    assert count(api, V1AssessmentDB) == 1


@pytest.fixture()
def concurrent_env(tmp_path):
    """A file-backed SQLite database with real separate connections per request."""
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    previous, factory = app.dependency_overrides.get(get_db), app.state.auth_session_factory
    app.dependency_overrides[get_db] = override
    app.state.auth_session_factory = Session
    with Session() as db:
        ensure_metric_definitions(db)
        make_user(db, "COACH", "race.coach", "coach")
        make_user(db, "ADMIN", "race.admin", "admin")
        db.commit()
        from tests.test_privacy_s0 import PROFILE
        db.add(PlayerDB(player_id="P_R", first_name_ar="a", last_name_ar="b", first_name_en="Race", last_name_en="Kid",
                        date_of_birth=date(2016, 1, 1), sex="male", **{k: v for k, v in PROFILE.items()}))
        db.commit()
        PrivacyService(db).grant_consent("PT_R", "P_R", "G", "g@example.test", "in_person",
                                         ["performance_tracking"], "ADMIN")

    def login():
        client = TestClient(app)
        assert client.post("/auth/login", json={"username": "race.coach", "password": "race.coachPassword123!"}).status_code == 200
        client.headers.update({"X-CSRF-Token": client.cookies.get(CSRF_COOKIE_NAME)})
        return client

    yield Session, login
    app.state.auth_session_factory = factory
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous
    engine.dispose()


def race(clients, calls):
    barrier = threading.Barrier(len(calls))

    def run(client, call):
        barrier.wait()
        return call(client)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(run, c, call) for c, call in zip(clients, calls)]]


def test_concurrent_identical_requests_create_exactly_one_measurement(concurrent_env):
    Session, login = concurrent_env
    clients = [login() for _ in range(4)]
    assessment = create_assessment(clients[0], "P_R").json()
    run = clients[0].post(f"/v1/assessments/{assessment['assessment_id']}/station-runs", json={"station_id": "S1"}).json()
    trial = clients[0].post(f"/v1/station-runs/{run['station_run_id']}/trials", json={"attempt_number": 1}).json()
    k = key("race")
    results = race(clients, [lambda c: enter(c, trial["trial_id"], "4.25", client_entry_id=k)] * 4)
    assert sorted(r.status_code for r in results) == [200, 200, 200, 201], [r.text for r in results]
    assert len({r.json()["measurement_id"] for r in results}) == 1
    with Session() as db:
        assert db.query(V1ManualMeasurementDB).count() == 1
        assert [e.action for e in db.query(AuditEventDB).filter_by(action="v1_manual_measurement_recorded")].count(
            "v1_manual_measurement_recorded") == 1


def test_concurrent_different_keys_for_the_same_trial_and_metric_yield_one_winner(concurrent_env):
    Session, login = concurrent_env
    clients = [login() for _ in range(3)]
    assessment = create_assessment(clients[0], "P_R").json()
    run = clients[0].post(f"/v1/assessments/{assessment['assessment_id']}/station-runs", json={"station_id": "S1"}).json()
    trial = clients[0].post(f"/v1/station-runs/{run['station_run_id']}/trials", json={"attempt_number": 1}).json()
    results = race(clients, [lambda c, v=v: enter(c, trial["trial_id"], v) for v in ("4.25", "4.26", "4.27")])
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409], codes
    with Session() as db:
        assert db.query(V1ManualMeasurementDB).count() == 1


def test_concurrent_duplicate_assessment_trial_and_correction_requests(concurrent_env):
    Session, login = concurrent_env
    clients = [login() for _ in range(3)]
    k = key("asm")
    body = {"player_id": "P_R", "assessment_date": "2026-09-12", "client_entry_id": k}
    results = race(clients, [lambda c: c.post("/v1/assessments", json=body)] * 3)
    assert sorted(r.status_code for r in results) == [200, 200, 201], [r.text for r in results]
    aid = results[0].json()["assessment_id"]
    runs = race(clients, [lambda c: c.post(f"/v1/assessments/{aid}/station-runs", json={"station_id": "S1"})] * 3)
    assert sorted(r.status_code for r in runs) == [200, 200, 201], [r.text for r in runs]
    rid = runs[0].json()["station_run_id"]
    trials = race(clients, [lambda c: c.post(f"/v1/station-runs/{rid}/trials", json={"attempt_number": 1})] * 3)
    assert sorted(r.status_code for r in trials) == [200, 200, 201], [r.text for r in trials]
    tid = trials[0].json()["trial_id"]
    mid = enter(clients[0], tid, "4.52").json()["measurement_id"]
    fixes = race(clients, [lambda c: correct(c, mid, "4.25", "4.52", confirmer="ADMIN")] * 3)
    assert sorted(r.status_code for r in fixes) == [200, 200, 201], [r.text for r in fixes]
    with Session() as db:
        assert db.query(V1AssessmentDB).count() == 1 and db.query(V1StationRunDB).count() == 1
        assert db.query(V1TrialDB).count() == 1 and db.query(V1ManualMeasurementCorrectionDB).count() == 1


# ---- 35-40 read model -----------------------------------------------------------------------------

def test_read_model_returns_the_tree_with_history_and_effective_value(api):
    assessment, run, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    correct(api.coach, mid, "4.25", "4.52")
    tree = api.coach.get(f"/v1/assessments/{assessment['assessment_id']}")
    assert tree.status_code == 200
    body = tree.json()
    (station,) = body["station_runs"]
    (read_trial,) = station["trials"]
    (measurement,) = read_trial["manual_measurements"]
    assert station["station_id"] == "S1" and read_trial["attempt_number"] == 1
    assert (measurement["original_value"], measurement["effective_value"]) == ("4.52", "4.25")
    assert measurement["corrections"][0]["corrected_value"] == "4.25"
    assert api.coach.get("/v1/assessments/NOPE").status_code == 404
    assert api.coach.get("/v1/manual-measurements/NOPE").status_code == 404


def test_read_model_has_no_scores_talent_potential_ranking_or_benchmarks(api):
    assessment, _, trial = setup_trial(api)
    enter(api.coach, trial["trial_id"], "4.25")
    text = json.dumps(api.coach.get(f"/v1/assessments/{assessment['assessment_id']}").json()).lower()
    for word in ("overall", "score", "talent", "potential", "rank", "percentile", "benchmark", "norm",
                 "age_band", "confidence", "rating"):
        assert f'"{word}' not in text and f"_{word}" not in text, word


# ---- 41 no delete ------------------------------------------------------------------------------------

def test_no_v1_delete_or_generic_update_routes_exist(api):
    paths = {p: set(ops) for p, ops in app.openapi()["paths"].items() if p.startswith("/v1")}
    assert paths
    for path, methods in paths.items():
        assert methods <= {"get", "post"}, (path, methods)
    assert sorted(paths) == sorted([
        "/v1/assessments", "/v1/assessments/{assessment_id}",
        "/v1/assessments/{assessment_id}/station-runs", "/v1/station-runs/{station_run_id}/trials",
        "/v1/trials/{trial_id}/manual-measurements", "/v1/manual-measurements/{measurement_id}",
        "/v1/manual-measurements/{measurement_id}/corrections"])
    _, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.25").json()["measurement_id"]
    for method in ("delete", "put", "patch"):
        for path in (f"/v1/manual-measurements/{mid}", f"/v1/trials/{trial['trial_id']}",
                     f"/v1/station-runs/{trial['station_run_id']}", "/v1/assessments"):
            response = getattr(api.admin, method)(path)
            assert response.status_code in (404, 405), (method, path)
    assert count(api, V1ManualMeasurementDB) == 1


# ---- 42-44 privacy ------------------------------------------------------------------------------------

def seed_through_api(api, player):
    _, _, trial = setup_trial(api, player)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    correct(api.coach, mid, "4.25", "4.52")
    return mid


def test_api_created_data_is_exported_and_deleted_with_the_child(api):
    seed_through_api(api, "P_A")
    seed_through_api(api, "P_B")
    export = api.guard_a.get("/guardian/children/P_A/data-export").json()
    (assessment,) = export["v1_assessments"]
    measurement = assessment["station_runs"][0]["trials"][0]["manual_measurements"][0]
    assert measurement["value_seconds"] == "4.52" and measurement["effective_value_seconds"] == "4.25"
    assert measurement["corrections"][0]["corrected_seconds"] == "4.25"
    assert "P_B" not in json.dumps(export)
    assert run_deletion(api, api.guard_a, "P_A", "REQ_A").status_code == 200
    with api.db() as db:
        assert db.query(V1AssessmentDB).filter_by(player_id="P_A").count() == 0
        assert db.query(V1AssessmentDB).filter_by(player_id="P_B").count() == 1
    assert count(api, V1ManualMeasurementDB) == 1 and count(api, V1ManualMeasurementCorrectionDB) == 1
    body = api.guard_b.get("/guardian/children/P_B/data-export").json()
    assert body["v1_assessments"][0]["station_runs"][0]["trials"][0]["manual_measurements"][0][
        "effective_value_seconds"] == "4.25"
    # After deletion the child cannot get new V1 data (anonymised, consent removed).
    assert create_assessment(api.coach, "P_A").status_code in (403, 422)


# ---- 45-48 audit ---------------------------------------------------------------------------------------

def test_audit_events_exist_and_do_not_leak_content(api):
    assessment, _, trial = setup_trial(api)
    mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
    correct(api.coach, mid, "4.25", "4.52", reason="OTHER", reason_text="PRIVATE-NARRATIVE")
    events = {e.action: e for e in api_audit(api)}
    assert events["v1_assessment_created"].resource_id == assessment["assessment_id"]
    assert events["v1_manual_measurement_recorded"].resource_id == mid
    assert events["v1_manual_measurement_correction_recorded"].details["sequence_number"] == 1
    for name in ("v1_assessment_created", "v1_manual_measurement_recorded", "v1_manual_measurement_correction_recorded"):
        assert events[name].actor_user_id == "COACH" and events[name].details["result"] == "ok"
    blob = json.dumps([[e.action, e.resource_id, e.details] for e in events.values()], default=str).lower()
    for secret in ("fall assessment", "private-narrative", "race", "player p_a", "4.52", "4.25", "452", "425",
                   "g@example.test"):
        assert secret not in blob, secret


# ---- 49-52 classification and legacy ---------------------------------------------------------------------

def test_v1_tables_stay_classified_and_the_guard_still_fails_for_a_new_one():
    from app.privacy_classification import TABLE_CLASSIFICATIONS, unclassified_tables
    assert unclassified_tables(Base.metadata) == []
    assert all(TABLE_CLASSIFICATIONS[t].privileged_deletion_only for t in Base.metadata.tables if t.startswith("v1_"))
    without = {k: v for k, v in TABLE_CLASSIFICATIONS.items() if k != "v1_trials"}
    assert unclassified_tables(Base.metadata, without) == ["v1_trials"]


def test_legacy_assessment_api_and_rows_are_unchanged_by_v1_calls(api):
    with api.db() as db:
        db.add(PlayerAssessmentDB(
            assessment_id="LEG1", player_id="P_A", pillar="physical", test_category="speed", test_type="sprint",
            methodology_version="legacy", test_date=date(2026, 1, 1), age_at_assessment_years=10,
            raw_data={"overall_score": 77.0}, calculated_metrics={"x": 1}, ai_assisted=False, notes="legacy note",
            recorded_by_user_id="COACH", created_at=utcnow()))
        db.commit()
    before = api.coach.get("/players/P_A/assessments").json()
    seed_through_api(api, "P_A")
    assert api.coach.get("/players/P_A/assessments").json() == before
    assert "LEG1" in [a["assessment_id"] for a in before["assessments"]]
    assert "delete" in app.openapi()["paths"]["/player-assessments/{assessment_id}"]   # legacy route untouched
    assert not [p for p in app.openapi()["paths"] if p.startswith("/players") and "v1" in p]
