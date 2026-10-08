"""Unified Player Assessment, Phase 1: the assessment session, the eight-station overview, and
official manual timing for S1 (sprint), S2 (agility, per turning foot) and S4 (dribbling).

Only the frozen standard's rules are used: exact displayed hundredths (HUMAN_MANUAL), the invalid
reasons it lists per station, S2's turning-foot series, and "a missed mandatory cone is INVALID and
repeatable while a cone contact alone is not an invalid reason". S3, S5, S6, S7 and S8 are COMING
SOON and must accept nothing. Synthetic players only.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db_models import PlayerDB
from app.privacy_classification import TABLE_CLASSIFICATIONS
from app.services.privacy_service import PrivacyService
from app.v1 import definitions as d
from app.v1.models import V1AssessmentDB, V1ManualMeasurementDB, V1StationRunDB, V1TrialDB
from main import app
from tests.test_v1_access_flag import set_flag
from tests.test_v1_api import (  # noqa: F401  (api, concurrent_env are fixtures)
    api, concurrent_env, correct, count, create_assessment, key, race,
)
from tests.test_privacy_s0 import ctx  # noqa: F401  (api depends on it)

COMING_SOON = ("S3", "S5", "S6", "S7", "S8")


def attempt(client, assessment_id, station="S1", *, number=1, series="", value="4.25", valid=True,
            invalid_reason=None, not_recorded=None, op=None, **extra):
    body = {"station_id": station, "series_key": series, "attempt_number": number,
            "client_entry_id": op or key("att")}
    if valid:
        body["validity"] = "VALID"
        if not_recorded:
            body.update(entry_status="NOT_RECORDED", not_recorded_reason=not_recorded)
        else:
            body.update(entry_status="RECORDED", displayed_value=value)
    else:
        body.update(validity="INVALID", invalid_reason=invalid_reason)
    body.update(extra)
    return client.post(f"/v1/assessments/{assessment_id}/attempts", json=body)


def overview(client, assessment_id):
    response = client.get(f"/v1/assessments/{assessment_id}/stations")
    assert response.status_code == 200, response.text
    return response.json()


def station(view, station_id):
    return next(s for s in view["stations"] if s["station_id"] == station_id)


@pytest.fixture()
def asm(api):
    return create_assessment(api.coach, "P_A").json()["assessment_id"]


# ======================================================================== SESSION

def test_create_and_continue_an_assessment_for_the_same_player_identity(api):
    players_before = count(api, PlayerDB)
    first = create_assessment(api.coach, "P_A")
    assert first.status_code == 201 and first.json()["player_id"] == "P_A" and first.json()["status"] == "IN_PROGRESS"
    listed = api.coach.get("/v1/players/P_A/assessments").json()["assessments"]
    assert [a["assessment_id"] for a in listed] == [first.json()["assessment_id"]]
    assert listed[0]["status"] == "IN_PROGRESS" and listed[0]["player_id"] == "P_A"
    assert count(api, PlayerDB) == players_before                          # no second identity, ever


def test_repeating_the_start_request_never_creates_a_second_session(api):
    body = {"player_id": "P_A", "assessment_date": "2026-09-12", "client_entry_id": key("asm")}
    one = api.coach.post("/v1/assessments", json=body)
    two = api.coach.post("/v1/assessments", json=body)
    assert (one.status_code, two.status_code) == (201, 200)
    assert one.json()["assessment_id"] == two.json()["assessment_id"]
    assert count(api, V1AssessmentDB) == 1


def test_a_different_key_starts_a_separate_assessment_and_both_can_be_continued(api):
    one = create_assessment(api.coach, "P_A").json()["assessment_id"]
    two = create_assessment(api.coach, "P_A").json()["assessment_id"]
    assert one != two
    assert {a["assessment_id"] for a in api.coach.get("/v1/players/P_A/assessments").json()["assessments"]} == {one, two}


def test_a_player_without_the_required_consent_cannot_start_or_save(api):
    assert create_assessment(api.coach, "P_EMPTY").status_code == 403
    aid = create_assessment(api.coach, "P_A").json()["assessment_id"]
    with api.db() as db:
        PrivacyService(db).withdraw_consent("PT_P_A", "ADMIN")
    assert attempt(api.coach, aid).status_code == 403
    assert count(api, V1TrialDB) == 0 and count(api, V1StationRunDB) == 0


# ======================================================================== STATIONS

def test_the_overview_lists_all_eight_stations_in_order_with_nothing_invented(api, asm):
    view = overview(api.coach, asm)
    assert [s["station_id"] for s in view["stations"]] == ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    assert [s["name"] for s in view["stations"]] == [
        "Sprint", "Agility", "Ball Mastery", "Dribbling", "Passing / Receiving", "Shooting", "1v1", "Small-Sided Game"]
    assert view["status"] == "IN_PROGRESS" and view["player_id"] == "P_A"
    for s in view["stations"]:
        if s["station_id"] in ("S1", "S2", "S4"):
            assert s["enabled"] and s["state"] == "NOT_STARTED" and s["metric_id"] and s["series"]
        else:
            assert not s["enabled"] and s["state"] == "COMING_SOON"
            assert s["metric_id"] is None and s["series"] == [] and s["description"] == ""
    assert count(api, V1StationRunDB) == 0                                  # reading creates nothing


def test_the_overview_follows_the_frozen_standards_numbers(api, asm):
    view = overview(api.coach, asm)
    rule = lambda sid: [(x["series_key"], x["required_valid"], x["max_attempts"]) for x in station(view, sid)["series"]]
    assert rule("S1") == [("", 3, 5)] and rule("S4") == [("", 3, 5)]
    assert rule("S2") == [("L", 2, 4), ("R", 2, 4)]


def test_overview_access_follows_the_existing_roles(api, asm):
    assert api.reviewer.get(f"/v1/assessments/{asm}/stations").status_code == 200
    assert api.guard_a.get(f"/v1/assessments/{asm}/stations").status_code == 403
    assert TestClient(app).get(f"/v1/assessments/{asm}/stations").status_code == 401
    assert api.coach.get("/v1/assessments/NOPE/stations").status_code == 404
    assert attempt(api.reviewer, asm).status_code == 403                     # read-only role cannot save


@pytest.mark.parametrize("station_id", COMING_SOON)
def test_stations_that_are_coming_soon_accept_no_measurement_of_any_kind(api, asm, station_id):
    assert attempt(api.coach, asm, station_id).status_code == 422
    assert attempt(api.coach, asm, station_id, valid=False, invalid_reason="FALSE_START").status_code == 422
    assert api.coach.post(f"/v1/assessments/{asm}/station-runs", json={"station_id": station_id}).status_code == 422
    assert count(api, V1StationRunDB) == 0 and count(api, V1TrialDB) == 0 and count(api, V1ManualMeasurementDB) == 0
    assert station(overview(api.coach, asm), station_id)["state"] == "COMING_SOON"


# ========================================================================== SPRINT (S1)

@pytest.mark.parametrize("typed,shown", [("4.25", "4.25"), ("4.20", "4.20"), ("42.50", "42.50"),
                                         ("7", "7.00"), ("10.5", "10.50")])
def test_official_time_round_trips_exactly_as_typed_in_hundredths(api, asm, typed, shown):
    """The displayed value is the typed reading in hundredths: 4.20 stays 4.20 (never 4.2) and a
    reading typed without decimals is shown with two, which is the same number, not a new one."""
    response = attempt(api.coach, asm, "S1", value=typed)
    assert response.status_code == 201
    m = response.json()["trial"]["manual_measurements"][0]
    assert m["displayed_value"] == shown and m["effective_value"] == shown and m["original_value"] == shown
    assert m["measurement_source"] == "HUMAN_MANUAL" and m["measurement_method"] == "MANUAL_STOPWATCH"
    tree = api.coach.get(f"/v1/assessments/{asm}").json()
    assert tree["station_runs"][0]["trials"][0]["manual_measurements"][0]["displayed_value"] == shown


def test_four_twenty_five_is_stored_as_exact_hundredths(api, asm):
    attempt(api.coach, asm, "S1", value="4.25")
    with api.db() as db:
        assert db.execute(select(V1ManualMeasurementDB.value_centiseconds)).scalar_one() == 425


def test_several_attempts_progress_to_complete_and_every_time_is_listed(api, asm):
    for n, value in enumerate(("4.52", "4.31", "4.25"), start=1):
        assert attempt(api.coach, asm, "S1", number=n, value=value).status_code == 201
        view = station(overview(api.coach, asm), "S1")
        assert view["state"] == ("COMPLETE" if n == 3 else "IN_PROGRESS")
        assert view["series"][0]["valid_recorded"] == n
    trials = api.coach.get(f"/v1/assessments/{asm}").json()["station_runs"][0]["trials"]
    assert [t["manual_measurements"][0]["displayed_value"] for t in trials] == ["4.52", "4.31", "4.25"]


def test_a_not_valid_attempt_is_recorded_not_counted_and_repeatable(api, asm):
    bad = attempt(api.coach, asm, "S1", number=1, valid=False, invalid_reason="FALSE_START")
    assert bad.status_code == 201 and bad.json()["trial"]["manual_measurements"] == []
    assert attempt(api.coach, asm, "S1", number=2, value="4.40").status_code == 201
    s1 = station(overview(api.coach, asm), "S1")["series"][0]
    assert (s1["attempts"], s1["not_valid"], s1["valid_recorded"]) == (2, 1, 1)


def test_a_timer_problem_saves_no_number_and_is_not_counted(api, asm):
    response = attempt(api.coach, asm, "S1", number=1, not_recorded="DEVICE_FAILURE")
    assert response.status_code == 201
    m = response.json()["trial"]["manual_measurements"][0]
    assert m["entry_status"] == "NOT_RECORDED" and m["displayed_value"] is None
    s1 = station(overview(api.coach, asm), "S1")["series"][0]
    assert (s1["valid_recorded"], s1["without_time"]) == (0, 1)


def test_attempts_beyond_the_standards_limit_are_flagged_but_never_blocked(api, asm):
    for n in range(1, 7):
        assert attempt(api.coach, asm, "S1", number=n, value=f"4.{n}0").status_code == 201
    assert station(overview(api.coach, asm), "S1")["series"][0]["over_attempt_limit"] is True


def test_a_correction_keeps_the_original_after_a_one_step_save(api, asm):
    saved = attempt(api.coach, asm, "S1", value="4.52").json()["trial"]["manual_measurements"][0]
    fixed = correct(api.coach, saved["measurement_id"], "4.25", "4.52", confirmer="ADMIN")
    assert fixed.status_code == 201
    m = fixed.json()["measurement"]
    assert (m["original_value"], m["effective_value"]) == ("4.52", "4.25") and len(m["corrections"]) == 1


# ================================================================ AGILITY (S2, per turning foot)

def test_agility_needs_a_turning_foot_series_and_sprint_does_not_accept_one(api, asm):
    assert attempt(api.coach, asm, "S2", series="").status_code == 422
    assert attempt(api.coach, asm, "S1", series="L").status_code == 422
    assert count(api, V1TrialDB) == 0


def test_agility_times_are_exact_and_kept_per_foot(api, asm):
    assert attempt(api.coach, asm, "S2", series="L", number=1, value="6.80").status_code == 201
    assert attempt(api.coach, asm, "S2", series="R", number=1, value="7.05").status_code == 201   # same number, other foot
    trials = api.coach.get(f"/v1/assessments/{asm}").json()["station_runs"][0]["trials"]
    got = {(t["series_key"], t["attempt_number"]): t["manual_measurements"][0]["displayed_value"] for t in trials}
    assert got == {("L", 1): "6.80", ("R", 1): "7.05"}
    metric = api.coach.get(f"/v1/assessments/{asm}").json()["station_runs"][0]["trials"][0]["manual_measurements"][0]["metric_id"]
    assert metric == "PHYS-COD-180-MAN-001"


def test_agility_is_complete_only_when_both_feet_have_two_valid_attempts(api, asm):
    for series, number, value in (("L", 1, "6.80"), ("L", 2, "6.70"), ("R", 1, "7.05")):
        attempt(api.coach, asm, "S2", series=series, number=number, value=value)
    view = station(overview(api.coach, asm), "S2")
    assert view["state"] == "IN_PROGRESS"
    assert [(s["series_key"], s["valid_recorded"], s["complete"]) for s in view["series"]] == [("L", 2, True), ("R", 1, False)]
    attempt(api.coach, asm, "S2", series="R", number=2, value="7.00")
    assert station(overview(api.coach, asm), "S2")["state"] == "COMPLETE"


def test_agility_accepts_exactly_the_standards_invalid_reasons(api, asm):
    for number, reason in enumerate(sorted(d.INVALID_REASONS["S2"]), start=1):
        assert attempt(api.coach, asm, "S2", series="L", number=number, valid=False, invalid_reason=reason).status_code == 201, reason
    for foreign in ("MISSED_CONE", "STOPPED_EARLY", "CONE_CONTACT", "NOT_A_REASON"):
        assert attempt(api.coach, asm, "S2", series="R", number=1, valid=False, invalid_reason=foreign).status_code == 422, foreign
    assert d.INVALID_REASONS["S2"] == {"FALSE_START", "TURNED_BEFORE_L", "FOOT_NOT_AT_L", "WRONG_TURN_SIDE",
                                       "FALL_OR_TRIP", "LANE_VIOLATION", "INTERFERENCE"}


# =============================================================== DRIBBLING (S4)

def test_dribbling_time_is_exact_and_uses_its_own_metric(api, asm):
    response = attempt(api.coach, asm, "S4", value="14.80")
    m = response.json()["trial"]["manual_measurements"][0]
    assert (m["displayed_value"], m["metric_id"]) == ("14.80", "TECH-DRB-SLALOM-TIME-MAN-001")


def test_a_missed_mandatory_cone_is_invalid_and_the_attempt_can_be_repeated(api, asm):
    bad = attempt(api.coach, asm, "S4", number=1, valid=False, invalid_reason="MISSED_CONE")
    assert bad.status_code == 201 and bad.json()["trial"]["validity"] == "INVALID"
    assert attempt(api.coach, asm, "S4", number=2, value="15.10").status_code == 201
    s4 = station(overview(api.coach, asm), "S4")["series"][0]
    assert (s4["not_valid"], s4["valid_recorded"]) == (1, 1)                # the invalid one did not count


def test_a_cone_contact_alone_is_not_an_invalidating_reason(api, asm):
    assert "CONE_CONTACT" not in d.INVALID_REASONS["S4"] and "MISSED_CONE" in d.INVALID_REASONS["S4"]
    assert attempt(api.coach, asm, "S4", number=1, valid=False, invalid_reason="CONE_CONTACT").status_code == 422
    # A valid attempt is saved normally; there is no cone-contact field that could invalidate it.
    assert attempt(api.coach, asm, "S4", number=1, value="14.20").status_code == 201
    assert attempt(api.coach, asm, "S4", number=2, value="14.30", cone_contacts=2).status_code == 422
    assert station(overview(api.coach, asm), "S4")["series"][0]["valid_recorded"] == 1


def test_dribbling_accepts_exactly_the_standards_invalid_reasons(api, asm):
    for number, reason in enumerate(sorted(d.INVALID_REASONS["S4"]), start=1):
        assert attempt(api.coach, asm, "S4", number=number, valid=False, invalid_reason=reason).status_code == 201, reason
    assert attempt(api.coach, asm, "S4", number=9, valid=False, invalid_reason="WRONG_TURN_SIDE").status_code == 422


# ================================================================== ONE-STEP SAVE: idempotency

def test_the_same_save_twice_returns_the_first_result_and_creates_nothing_new(api, asm):
    op = key("att")
    one = attempt(api.coach, asm, "S1", number=1, value="4.25", op=op)
    two = attempt(api.coach, asm, "S1", number=1, value="4.25", op=op)
    assert (one.status_code, two.status_code) == (201, 200)
    assert two.json()["trial"]["trial_id"] == one.json()["trial"]["trial_id"] and two.json()["created"] is False
    assert count(api, V1TrialDB) == 1 and count(api, V1ManualMeasurementDB) == 1 and count(api, V1StationRunDB) == 1


def test_a_repeated_not_valid_attempt_is_not_duplicated(api, asm):
    for _ in range(3):
        attempt(api.coach, asm, "S1", number=1, valid=False, invalid_reason="FALSE_START")
    assert count(api, V1TrialDB) == 1


def test_the_same_key_with_different_content_is_a_conflict_and_changes_nothing(api, asm):
    op = key("att")
    attempt(api.coach, asm, "S1", number=1, value="4.25", op=op)
    assert attempt(api.coach, asm, "S1", number=1, value="4.26", op=op).status_code == 409
    assert attempt(api.coach, asm, "S1", number=2, value="4.25", op=op).status_code == 409
    with api.db() as db:
        assert db.execute(select(V1ManualMeasurementDB.value_centiseconds)).scalar_one() == 425


def test_an_attempt_number_that_is_taken_is_never_overwritten(api, asm):
    attempt(api.coach, asm, "S1", number=1, value="4.25")
    clash = attempt(api.coach, asm, "S1", number=1, value="3.99")            # new key, same attempt
    assert clash.status_code == 409
    other = attempt(api.coach, asm, "S1", number=1, valid=False, invalid_reason="FALSE_START")
    assert other.status_code == 409
    with api.db() as db:
        assert db.execute(select(V1ManualMeasurementDB.value_centiseconds)).scalar_one() == 425
        assert db.execute(select(func.count()).select_from(V1TrialDB)).scalar_one() == 1


def test_a_trial_left_without_a_time_gets_its_time_when_the_save_is_repeated(api, asm):
    """A trial made through the older two-step route (or by an interrupted save) is completed,
    not duplicated, by a later one-step save with the same content."""
    run = api.coach.post(f"/v1/assessments/{asm}/station-runs", json={"station_id": "S1"}).json()
    trial = api.coach.post(f"/v1/station-runs/{run['station_run_id']}/trials", json={"attempt_number": 1}).json()
    response = attempt(api.coach, asm, "S1", number=1, value="4.25")
    assert response.status_code == 201 and response.json()["trial"]["trial_id"] == trial["trial_id"]
    assert count(api, V1TrialDB) == 1 and count(api, V1ManualMeasurementDB) == 1


def test_a_rejected_save_stores_nothing_at_all(api, asm):
    for bad in ("4,25", "0", "-1", "4.255", "", "abc"):
        assert attempt(api.coach, asm, "S1", value=bad).status_code == 422, bad
    assert count(api, V1StationRunDB) == 0 and count(api, V1TrialDB) == 0 and count(api, V1ManualMeasurementDB) == 0


def test_shape_rules_for_a_save(api, asm):
    body = {"station_id": "S1", "attempt_number": 1, "client_entry_id": key("att")}
    for extra in ({"validity": "VALID"},                                                      # no time
                  {"validity": "VALID", "entry_status": "RECORDED"},                           # no value
                  {"validity": "VALID", "entry_status": "RECORDED", "displayed_value": "4.25", "invalid_reason": "FALSE_START"},
                  {"validity": "VALID", "entry_status": "NOT_RECORDED", "not_recorded_reason": "NOPE"},
                  {"validity": "INVALID", "invalid_reason": "FALSE_START", "displayed_value": "4.25"},
                  {"validity": "INVALID"},
                  {"validity": "VALID", "entry_status": "RECORDED", "displayed_value": 4.25},   # a JSON number
                  {"validity": "VALID", "entry_status": "RECORDED", "displayed_value": "4.25", "score": 90}):
        assert api.coach.post(f"/v1/assessments/{asm}/attempts", json={**body, **extra}).status_code == 422, extra
    assert count(api, V1TrialDB) == 0


def test_concurrent_identical_saves_create_exactly_one_attempt(concurrent_env):
    Session, login = concurrent_env
    clients = [login() for _ in range(4)]
    aid = create_assessment(clients[0], "P_R").json()["assessment_id"]
    op = key("race")
    results = race(clients, [lambda c: attempt(c, aid, "S2", series="L", number=1, value="6.80", op=op)] * 4)
    assert sorted(r.status_code for r in results) == [200, 200, 200, 201], [r.text for r in results]
    assert len({r.json()["trial"]["trial_id"] for r in results}) == 1
    with Session() as db:
        assert db.query(V1StationRunDB).count() == 1 and db.query(V1TrialDB).count() == 1
        assert db.query(V1ManualMeasurementDB).count() == 1


def test_concurrent_different_saves_for_the_same_attempt_have_one_winner(concurrent_env):
    Session, login = concurrent_env
    clients = [login() for _ in range(3)]
    aid = create_assessment(clients[0], "P_R").json()["assessment_id"]
    results = race(clients, [lambda c, v=v: attempt(c, aid, "S1", number=1, value=v) for v in ("4.25", "4.26", "4.27")])
    assert sorted(r.status_code for r in results) == [201, 409, 409], [r.text for r in results]
    with Session() as db:
        assert db.query(V1TrialDB).count() == 1 and db.query(V1ManualMeasurementDB).count() == 1


# ========================================================================= PHASE 1 COMPLETION

def test_finishing_s1_s2_and_s4_does_not_complete_the_eight_station_assessment(api, asm):
    for n in (1, 2, 3):
        attempt(api.coach, asm, "S1", number=n, value=f"4.{n}0")
        attempt(api.coach, asm, "S4", number=n, value=f"14.{n}0")
    for series in ("L", "R"):
        for n in (1, 2):
            attempt(api.coach, asm, "S2", series=series, number=n, value=f"6.{n}0")
    view = overview(api.coach, asm)
    assert [s["state"] for s in view["stations"]] == [
        "COMPLETE", "COMPLETE", "COMING_SOON", "COMPLETE", "COMING_SOON", "COMING_SOON", "COMING_SOON", "COMING_SOON"]
    assert view["status"] == "IN_PROGRESS" and view["can_complete"] is False
    assert "5 of 8" in view["completion_note"]
    with api.db() as db:
        assert db.get(V1AssessmentDB, asm).status == "IN_PROGRESS"
    # Nothing in the API can mark an assessment complete or waive a station.
    assert not [p for p in app.openapi()["paths"] if p.startswith("/v1") and ("complete" in p or "waive" in p or "status" in p)]


def test_a_station_that_is_not_open_cannot_be_waived_into_completion(api, asm):
    response = api.coach.post(f"/v1/assessments/{asm}/attempts", json={
        "station_id": "S3", "attempt_number": 1, "validity": "INVALID", "invalid_reason": "HANDS_USED",
        "client_entry_id": key("att")})
    assert response.status_code == 422 and station(overview(api.coach, asm), "S3")["state"] == "COMING_SOON"


# ============================================================================= FEATURE FLAG

NEW_GET = ("/v1/assessments/V1A1/stations", "/v1/outbox.js")


def test_flag_off_hides_the_new_routes_like_every_other_v1_route(api, asm, monkeypatch):
    set_flag(monkeypatch, "false", "*")
    for path in NEW_GET:
        assert api.coach.get(path).status_code == 404, path
    assert attempt(api.coach, asm).status_code == 404
    assert TestClient(app).get(f"/v1/assessments/{asm}/stations").status_code == 404


def test_on_with_an_empty_allowlist_admits_nobody_to_the_new_routes(api, asm, monkeypatch):
    set_flag(monkeypatch, "true", "")
    assert api.coach.get(f"/v1/assessments/{asm}/stations").status_code == 404
    assert attempt(api.coach, asm).status_code == 404


def test_on_with_an_allowlist_admits_only_the_named_coach(api, asm, monkeypatch):
    set_flag(monkeypatch, "true", "s0.coach")
    assert api.coach.get(f"/v1/assessments/{asm}/stations").status_code == 200
    assert attempt(api.coach, asm).status_code == 201
    assert api.admin.get(f"/v1/assessments/{asm}/stations").status_code == 404
    assert api.coach2.get(f"/v1/assessments/{asm}/stations").status_code == 404
    assert attempt(api.coach2, asm, number=2).status_code == 404


def test_phase_1_adds_no_database_table(api):
    from app.database import Base
    v1_tables = {t for t in Base.metadata.tables if t.startswith("v1_")}
    # the same six tables phase 0 created (this repository's privacy classification covers every table in the application, not only V1's)
    assert v1_tables == {"v1_assessments", "v1_manual_measurement_corrections", "v1_manual_measurements", "v1_metric_definitions", "v1_station_runs", "v1_trials"}
    assert v1_tables <= set(TABLE_CLASSIFICATIONS)
