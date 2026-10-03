"""Assessment V1 / S1A data foundation: model, exact manual times, validity,
NOT_RECORDED, append-only corrections, no delete path, privacy integration.

Synthetic data only, scratch database (foreign keys enforced), see tests/conftest.py.
"""
import inspect as pyinspect
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Float, Numeric, inspect as sa_inspect, select, func, text
from sqlalchemy.exc import IntegrityError

import app.v1.service as v1_service_module
from app.database import Base
from app.db_models import AuditEventDB, PlayerAssessmentDB, PlayerDB
from app.services.auth_service import utcnow
from app.v1 import definitions as d
from app.v1.definitions import ensure_metric_definitions
from app.v1.models import (
    V1AssessmentDB,
    V1ImmutableRecordError,
    V1ManualMeasurementCorrectionDB,
    V1ManualMeasurementDB,
    V1MetricDefinitionDB,
    V1StationRunDB,
    V1TrialDB,
)
from app.v1.service import V1AssessmentService, V1ConflictError, V1ValidationError
from app.v1.timing import (
    InvalidTimeValue,
    centiseconds_to_decimal,
    format_seconds,
    parse_seconds_to_centiseconds,
)
from main import app
from tests.test_privacy_s0 import ctx, run_deletion  # noqa: F401  (ctx is a fixture)

SPRINT = "PHYS-SPR-020M-MAN-001"
V1_MODELS = [V1MetricDefinitionDB, V1AssessmentDB, V1StationRunDB, V1TrialDB,
             V1ManualMeasurementDB, V1ManualMeasurementCorrectionDB]


@pytest.fixture()
def svc(ctx):  # noqa: F811
    db = ctx.db()
    ensure_metric_definitions(db)
    yield V1AssessmentService(db)
    db.close()


def build(svc, player="P_A", station="S1", attempts=1, title="Fall Assessment"):
    assessment = svc.create_assessment(player, date(2026, 9, 12), "COACH", title=title)
    run = svc.start_station_run(assessment.assessment_id, station, "COACH")
    trials = [svc.add_trial(run.station_run_id, n, "COACH") for n in range(1, attempts + 1)]
    return assessment, run, trials


def count(ctx, model, **where):
    with ctx.db() as db:
        q = select(func.count()).select_from(model)
        for key, value in where.items():
            q = q.where(getattr(model, key) == value)
        return db.execute(q).scalar_one()


# ---- assessments, stations, trials (tests 1-6) -----------------------------------

def test_create_assessment_for_a_player(svc):
    assessment = svc.create_assessment("P_A", date(2026, 9, 12), "COACH", title="Fall 2026 Assessment")
    assert assessment.assessment_id.startswith("V1A") and assessment.player_id == "P_A"
    assert (assessment.protocol_code, assessment.status) == ("KEMET_V1", "IN_PROGRESS")


def test_a_player_can_have_many_assessments(svc):
    first = svc.create_assessment("P_A", date(2026, 9, 12), "COACH")
    second = svc.create_assessment("P_A", date(2027, 3, 12), "COACH")
    assert first.assessment_id != second.assessment_id


def test_assessment_needs_a_real_non_anonymised_player_and_user(svc, ctx):
    with pytest.raises(V1ValidationError):
        svc.create_assessment("NOPE", date(2026, 9, 12), "COACH")
    with pytest.raises(V1ValidationError):
        svc.create_assessment("P_A", date(2026, 9, 12), "NO_USER")
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    with pytest.raises(V1ValidationError):
        svc.create_assessment("P_A", date(2026, 9, 12), "COACH")


def test_create_station_run_and_trial(svc):
    assessment, run, (trial,) = build(svc)
    assert run.station_id == "S1" and run.assessment_id == assessment.assessment_id
    assert (trial.attempt_number, trial.validity, trial.invalid_reason) == (1, "VALID", None)
    with pytest.raises(V1ConflictError):
        svc.start_station_run(assessment.assessment_id, "S1", "COACH")
    with pytest.raises(V1ValidationError):
        svc.start_station_run(assessment.assessment_id, "S9", "COACH")


def test_duplicate_attempt_number_in_a_station_run_is_rejected(svc):
    _, run, _ = build(svc)
    with pytest.raises(V1ConflictError):
        svc.add_trial(run.station_run_id, 1, "COACH")
    for bad in (0, -1, True, "1"):
        with pytest.raises(V1ValidationError):
            svc.add_trial(run.station_run_id, bad, "COACH")


def test_same_attempt_number_is_allowed_in_another_station_or_assessment(svc):
    assessment, run, _ = build(svc)
    other_station = svc.start_station_run(assessment.assessment_id, "S4", "COACH")
    assert svc.add_trial(other_station.station_run_id, 1, "COACH").attempt_number == 1
    _, other_run, _ = build(svc)  # a second assessment, same player, attempt 1 again
    assert other_run.station_run_id != run.station_run_id
    # Per-foot series (S2) number their attempts separately.
    s2 = svc.start_station_run(assessment.assessment_id, "S2", "COACH")
    assert svc.add_trial(s2.station_run_id, 1, "COACH", series_key="L")
    assert svc.add_trial(s2.station_run_id, 1, "COACH", series_key="R")
    with pytest.raises(V1ValidationError):
        svc.add_trial(s2.station_run_id, 2, "COACH", series_key="")


# ---- exact manual times (tests 7-10) ------------------------------------------------

def test_four_point_two_five_is_stored_and_read_back_exactly(svc, ctx):
    _, _, (trial,) = build(svc)
    measurement = svc.record_manual_time(trial.trial_id, SPRINT, "4.25", "seconds", "COACH")
    assert measurement.value_centiseconds == 425
    with ctx.db() as db:
        stored = db.get(V1ManualMeasurementDB, measurement.manual_measurement_id)
        assert stored.value_centiseconds == 425
        assert centiseconds_to_decimal(stored.value_centiseconds) == Decimal("4.25")
        assert format_seconds(stored.value_centiseconds) == "4.25"
    for typed, cs in (("14.27", 1427), (Decimal("14.27"), 1427), (14.27, 1427), ("0.01", 1), ("4.2", 420), (5, 500)):
        assert parse_seconds_to_centiseconds(typed) == cs
    assert format_seconds(420) == "4.20" and format_seconds(None) is None


def test_measurement_is_human_manual_with_no_ai_or_confidence_columns(svc):
    _, _, (trial,) = build(svc)
    measurement = svc.record_manual_time(trial.trial_id, SPRINT, "4.25", "seconds", "COACH")
    assert (measurement.measurement_source, measurement.measurement_method, measurement.unit) == (
        "HUMAN_MANUAL", "MANUAL_STOPWATCH", "seconds")
    columns = {c.name for c in V1ManualMeasurementDB.__table__.columns}
    assert not {c for c in columns if any(w in c for w in ("confidence", "model", "ai_", "score"))}


# ---- hard validation (tests 11-15) ----------------------------------------------------

@pytest.mark.parametrize("bad", ["-4.25", -4.25, "0", 0, "0.00", Decimal("0"), "", " ", None,
                                 "abc", "4,25", "4.255", "1e1", float("nan"), float("inf"), True,
                                 "6000.00", [4.25]])
def test_invalid_times_are_rejected_and_nothing_is_stored(svc, ctx, bad):
    _, _, (trial,) = build(svc)
    with pytest.raises((InvalidTimeValue, V1ValidationError)):
        svc.record_manual_time(trial.trial_id, SPRINT, bad, "seconds", "COACH")
    assert count(ctx, V1ManualMeasurementDB) == 0


@pytest.mark.parametrize("unit", ["meters", "percent", "kilograms", "s", "", None])
def test_wrong_unit_is_rejected(svc, unit):
    _, _, (trial,) = build(svc)
    with pytest.raises(V1ValidationError):
        svc.record_manual_time(trial.trial_id, SPRINT, "4.25", unit, "COACH")


@pytest.mark.parametrize("source", ["AI", "VIDEO", "TIMING_GATE", "HUMAN_CODED", "", None])
def test_unsupported_source_is_rejected(svc, source):
    _, _, (trial,) = build(svc)
    with pytest.raises(V1ValidationError):
        svc.record_manual_time(trial.trial_id, SPRINT, "4.25", "seconds", "COACH", source=source)


def test_unknown_metric_or_wrong_station_metric_is_rejected(svc):
    _, _, (trial,) = build(svc)  # an S1 trial
    for metric in ("NOPE-001", "PHYS-COD-180-MAN-001", "TECH-DRB-SLALOM-TIME-MAN-001"):
        with pytest.raises(V1ValidationError):
            svc.record_manual_time(trial.trial_id, metric, "4.25", "seconds", "COACH")


def test_a_suspicious_value_is_stored_as_typed_never_guessed(svc):
    _, _, (trial,) = build(svc)
    measurement = svc.record_manual_time(trial.trial_id, SPRINT, "42.50", "seconds", "COACH")
    assert measurement.value_centiseconds == 4250  # not 425
    assert format_seconds(measurement.value_centiseconds) == "42.50"


def test_one_entry_per_trial_and_metric_and_idempotent_retry(svc, ctx):
    _, _, (trial,) = build(svc)
    first = svc.record_manual_time(trial.trial_id, SPRINT, "4.25", "seconds", "COACH", client_entry_id="E1")
    again = svc.record_manual_time(trial.trial_id, SPRINT, "4.25", "seconds", "COACH", client_entry_id="E1")
    assert again.manual_measurement_id == first.manual_measurement_id
    assert count(ctx, V1ManualMeasurementDB) == 1
    with pytest.raises(V1ConflictError):  # same key, different content
        svc.record_manual_time(trial.trial_id, SPRINT, "4.26", "seconds", "COACH", client_entry_id="E1")
    with pytest.raises(V1ConflictError):  # second time for the same trial and metric
        svc.record_manual_time(trial.trial_id, SPRINT, "4.30", "seconds", "COACH")
    assessment = svc.create_assessment("P_A", date(2026, 9, 12), "COACH", client_entry_id="A1")
    assert svc.create_assessment("P_A", date(2026, 9, 12), "COACH", client_entry_id="A1").assessment_id == \
        assessment.assessment_id


# ---- NOT_RECORDED and validity (tests 16-17) ---------------------------------------------

def test_timer_failure_is_not_recorded_with_no_number_and_no_verdict(svc, ctx):
    _, _, trials = build(svc, attempts=2)
    measurement = svc.record_not_recorded(trials[1].trial_id, SPRINT, "DEVICE_FAILURE", "COACH")
    assert measurement.entry_status == "NOT_RECORDED"
    assert measurement.value_centiseconds is None
    assert measurement.not_recorded_reason == "DEVICE_FAILURE"
    with ctx.db() as db:
        trial = db.get(V1TrialDB, trials[1].trial_id)
        # The child's attempt is not marked as a failure or as invalid.
        assert (trial.validity, trial.invalid_reason) == ("VALID", None)
    with pytest.raises(V1ValidationError):
        svc.record_not_recorded(trials[0].trial_id, SPRINT, "SLOW", "COACH")
    with pytest.raises(V1ValidationError):  # nothing can be corrected into a missing time
        svc.correct_manual_time(measurement.manual_measurement_id, "4.25", "COACH", "TYPING_ERROR")


def test_database_refuses_placeholder_numbers_for_not_recorded(svc, ctx):
    _, _, (trial,) = build(svc)
    with ctx.db() as db:
        base = dict(manual_measurement_id="X1", trial_id=trial.trial_id, metric_id=SPRINT,
                    metric_definition_version="1.0.0", unit="seconds",
                    measurement_source="HUMAN_MANUAL", measurement_method="MANUAL_STOPWATCH",
                    entered_by_user_id="COACH", entered_at=utcnow())
        for fields in (dict(entry_status="NOT_RECORDED", value_centiseconds=0, not_recorded_reason="DEVICE_FAILURE"),
                       dict(entry_status="NOT_RECORDED", value_centiseconds=99900, not_recorded_reason="DEVICE_FAILURE"),
                       dict(entry_status="NOT_RECORDED", value_centiseconds=None, not_recorded_reason=None),
                       dict(entry_status="RECORDED", value_centiseconds=0),
                       dict(entry_status="RECORDED", value_centiseconds=-5),
                       dict(entry_status="RECORDED", value_centiseconds=None),
                       dict(entry_status="RECORDED", value_centiseconds=425, measurement_source="AI"),
                       dict(entry_status="RECORDED", value_centiseconds=425, unit="meters")):
            db.add(V1ManualMeasurementDB(**{**base, **fields}))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


def test_invalid_trial_stores_its_reason(svc, ctx):
    _, run, _ = build(svc)
    trial = svc.add_trial(run.station_run_id, 2, "COACH", validity="INVALID", invalid_reason="FALSE_START")
    with ctx.db() as db:
        stored = db.get(V1TrialDB, trial.trial_id)
        assert (stored.validity, stored.invalid_reason) == ("INVALID", "FALSE_START")
    with pytest.raises(V1ValidationError):
        svc.add_trial(run.station_run_id, 3, "COACH", validity="INVALID")
    for timing_reason in d.NOT_RECORDED_REASONS:  # a timing problem is never a child's invalid reason
        with pytest.raises(V1ValidationError):
            svc.add_trial(run.station_run_id, 3, "COACH", validity="INVALID", invalid_reason=timing_reason)
    with pytest.raises(V1ValidationError):  # a reason from another station's list
        svc.add_trial(run.station_run_id, 3, "COACH", validity="INVALID", invalid_reason="MISSED_CONE")
    with pytest.raises(V1ValidationError):
        svc.add_trial(run.station_run_id, 3, "COACH", validity="VALID", invalid_reason="FALSE_START")


def test_missed_cone_invalidates_in_the_slalom_but_cone_contact_is_not_a_trial_state(svc):
    assessment, _, _ = build(svc)
    run = svc.start_station_run(assessment.assessment_id, "S4", "COACH")
    assert svc.add_trial(run.station_run_id, 1, "COACH", validity="INVALID", invalid_reason="MISSED_CONE")
    assert "CONE_CONTACT" not in d.INVALID_REASONS["S4"]


# ---- corrections (tests 18-20) ------------------------------------------------------------

def test_original_survives_a_correction_and_history_is_kept(svc, ctx):
    _, _, (trial,) = build(svc)
    original = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    first = svc.correct_manual_time(original.manual_measurement_id, "4.25", "COACH", "TYPING_ERROR",
                                    confirmed_by_user_id="ADMIN")
    assert (first.original_centiseconds, first.corrected_centiseconds, first.sequence_number) == (452, 425, 1)
    with ctx.db() as db:
        assert db.get(V1ManualMeasurementDB, original.manual_measurement_id).value_centiseconds == 452
    assert svc.effective_centiseconds(original.manual_measurement_id) == 425

    second = svc.correct_manual_time(original.manual_measurement_id, "4.35", "COACH", "MISREAD_DISPLAY")
    assert (second.original_centiseconds, second.corrected_centiseconds, second.sequence_number) == (425, 435, 2)
    history = svc.history(original.manual_measurement_id)
    assert [(c.original_centiseconds, c.corrected_centiseconds) for c in history] == [(452, 425), (425, 435)]
    assert svc.effective_centiseconds(original.manual_measurement_id) == 435
    with ctx.db() as db:  # still the original on the measurement row
        assert db.get(V1ManualMeasurementDB, original.manual_measurement_id).value_centiseconds == 452


def test_correction_records_actor_time_and_reason(svc):
    _, _, (trial,) = build(svc)
    m = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    c = svc.correct_manual_time(m.manual_measurement_id, "4.25", "ADMIN", "OTHER", reason_text="read the wrong line",
                                confirmed_by_user_id="COACH")
    assert (c.corrected_by_user_id, c.reason_code, c.reason_text, c.confirmed_by_user_id) == (
        "ADMIN", "OTHER", "read the wrong line", "COACH")
    assert c.corrected_at is not None


def test_bad_corrections_are_rejected(svc):
    _, _, (trial,) = build(svc)
    m = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    mid = m.manual_measurement_id
    for kwargs in (dict(new_value="4.52", reason_code="TYPING_ERROR"),            # no change
                   dict(new_value="0", reason_code="TYPING_ERROR"),
                   dict(new_value="4.255", reason_code="TYPING_ERROR"),
                   dict(new_value="4.25", reason_code="BECAUSE"),
                   dict(new_value="4.25", reason_code="OTHER"),                   # OTHER needs text
                   dict(new_value="4.25", reason_code="TYPING_ERROR", confirmed_by_user_id="COACH")):  # same person
        with pytest.raises((V1ValidationError, InvalidTimeValue)):
            svc.correct_manual_time(mid, corrected_by_user_id="COACH", **kwargs)
    assert svc.history(mid) == []


def test_database_refuses_a_correction_that_confirms_itself(svc, ctx):
    _, _, (trial,) = build(svc)
    m = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    with ctx.db() as db:
        db.add(V1ManualMeasurementCorrectionDB(
            correction_id="C1", manual_measurement_id=m.manual_measurement_id, sequence_number=1,
            original_centiseconds=452, corrected_centiseconds=425, reason_code="TYPING_ERROR",
            corrected_by_user_id="COACH", corrected_at=utcnow(), confirmed_by_user_id="COACH"))
        with pytest.raises(IntegrityError):
            db.commit()


# ---- no normal delete (test 21) -------------------------------------------------------------

def test_there_is_no_delete_or_update_operation_for_v1_data(svc, ctx):
    names = [n for n, _ in pyinspect.getmembers(V1AssessmentService, pyinspect.isfunction)]
    assert not [n for n in names if any(w in n for w in ("delete", "remove", "purge", "update", "edit", "overwrite"))]
    source = "".join(p.read_text() for p in Path(v1_service_module.__file__).parent.glob("*.py"))
    assert ".delete(" not in source and "DELETE FROM" not in source.upper()
    paths = app.openapi()["paths"]
    for path, ops in paths.items():  # the V1 API (S1B1) is get/post only
        if path.startswith("/v1"):
            assert set(ops) <= {"get", "post"}, path


def test_saved_v1_records_cannot_be_edited_or_deleted_through_the_orm(svc, ctx):
    assessment, run, (trial,) = build(svc)
    m = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    c = svc.correct_manual_time(m.manual_measurement_id, "4.25", "COACH", "TYPING_ERROR")
    with ctx.db() as db:
        row = db.get(V1ManualMeasurementDB, m.manual_measurement_id)
        row.value_centiseconds = 999
        with pytest.raises(V1ImmutableRecordError):
            db.commit()
        db.rollback()
        for model, key in ((V1ManualMeasurementDB, m.manual_measurement_id),
                           (V1ManualMeasurementCorrectionDB, c.correction_id),
                           (V1TrialDB, trial.trial_id), (V1StationRunDB, run.station_run_id),
                           (V1AssessmentDB, assessment.assessment_id)):
            db.delete(db.get(model, key))
            with pytest.raises(V1ImmutableRecordError):
                db.commit()
            db.rollback()
        db.get(V1TrialDB, trial.trial_id).validity = "INVALID"
        with pytest.raises(V1ImmutableRecordError):
            db.commit()
        db.rollback()
        # Foreign keys are RESTRICT: even a raw delete of a parent with children fails.
        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM v1_trials"))
            db.commit()
        db.rollback()
    assert count(ctx, V1ManualMeasurementDB) == 1 and count(ctx, V1ManualMeasurementCorrectionDB) == 1


# ---- audit ------------------------------------------------------------------------------------

def test_audit_events_hold_ids_and_results_only(svc, ctx):
    _, _, (trial,) = build(svc, title="Child Secret Name Fall")
    m = svc.record_manual_time(trial.trial_id, SPRINT, "4.52", "seconds", "COACH")
    svc.correct_manual_time(m.manual_measurement_id, "4.25", "COACH", "OTHER", reason_text="PRIVATE-NARRATIVE")
    with ctx.db() as db:
        events = {e.action: e for e in db.query(AuditEventDB).all()}
    for action in ("v1_assessment_created", "v1_manual_measurement_recorded",
                   "v1_manual_measurement_correction_recorded"):
        assert events[action].actor_user_id == "COACH" and events[action].details["result"] == "ok"
    blob = json.dumps([e.details for e in events.values()], default=str)
    for secret in ("Child Secret", "PRIVATE-NARRATIVE", "4.52", "4.25", "452", "425"):
        assert secret not in blob


# ---- privacy integration (tests 22-27) ---------------------------------------------------------

def seed_two_children(svc):
    for player in ("P_A", "P_B"):
        assessment, run, trials = build(svc, player=player, attempts=2)
        m = svc.record_manual_time(trials[0].trial_id, SPRINT, "4.52", "seconds", "COACH")
        svc.correct_manual_time(m.manual_measurement_id, "4.25", "COACH", "TYPING_ERROR")
        svc.record_not_recorded(trials[1].trial_id, SPRINT, "DEVICE_FAILURE", "COACH")


def test_privacy_export_includes_assessment_measurement_and_correction_history(svc, ctx):
    seed_two_children(svc)
    body = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    (assessment,) = body["v1_assessments"]
    assert assessment["player_id"] == "P_A" and assessment["title"] == "Fall Assessment"
    (run,) = assessment["station_runs"]
    first, second = run["trials"]
    (measurement,) = first["manual_measurements"]
    assert (measurement["value_seconds"], measurement["effective_value_seconds"]) == ("4.52", "4.25")
    (correction,) = measurement["corrections"]
    assert (correction["original_seconds"], correction["corrected_seconds"]) == ("4.52", "4.25")
    (missing,) = second["manual_measurements"]
    assert missing["entry_status"] == "NOT_RECORDED" and missing["value_seconds"] is None
    assert body["completeness"]["categories"]["v1_assessments"]["state"] == "INCLUDED"
    assert "P_B" not in json.dumps(body)


def test_privacy_export_of_a_child_with_no_v1_data_is_empty(svc, ctx):
    assert ctx.guard_empty.get("/guardian/children/P_EMPTY/data-export").json()["v1_assessments"] == []


def test_privacy_deletion_removes_one_childs_v1_data_and_keeps_the_other(svc, ctx):
    seed_two_children(svc)
    before_b = {model.__tablename__: count(ctx, model) for model in V1_MODELS[1:]}
    assert run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A").status_code == 200
    with ctx.db() as db:
        assert db.query(V1AssessmentDB).filter_by(player_id="P_A").count() == 0
        assert db.query(V1AssessmentDB).filter_by(player_id="P_B").count() == 1
        event = db.query(AuditEventDB).filter_by(action="child_deletion_completed").one()
    assert count(ctx, V1ManualMeasurementDB) == 2 and count(ctx, V1ManualMeasurementCorrectionDB) == 1
    assert count(ctx, V1TrialDB) == 2 and count(ctx, V1StationRunDB) == 1
    after_b = {model.__tablename__: count(ctx, model) for model in V1_MODELS[1:]}
    assert after_b == {k: v // 2 for k, v in before_b.items()}
    removed = event.details["removed_counts"]
    assert removed["v1_manual_measurement_corrections"] == 1 and removed["v1_manual_measurements"] == 2
    # Player B is untouched, value for value.
    body = ctx.guard_b.get("/guardian/children/P_B/data-export").json()
    assert body["v1_assessments"][0]["station_runs"][0]["trials"][0]["manual_measurements"][0][
        "effective_value_seconds"] == "4.25"


# ---- schema guards (tests 28-32) -----------------------------------------------------------------

def test_no_score_talent_or_float_fields_exist_in_v1_tables():
    banned = ("score", "talent", "potential", "overall", "rating", "rank", "percentile", "confidence", "model")
    for table in (m.__table__ for m in V1_MODELS):
        for column in table.columns:
            assert not any(word in column.name for word in banned), (table.name, column.name)
            assert not isinstance(column.type, (Float, Numeric)), (table.name, column.name)
    manual_columns = {c.name for c in V1ManualMeasurementDB.__table__.columns}
    assert "confidence" not in " ".join(manual_columns) and "model" not in " ".join(manual_columns)


def test_all_v1_tables_are_in_the_metadata_with_restrict_keys():
    names = sorted(t for t in Base.metadata.tables if t.startswith("v1_"))
    assert names == sorted(m.__tablename__ for m in V1_MODELS)
    for name in names:
        for fk in Base.metadata.tables[name].foreign_keys:
            assert (fk.ondelete or "").upper() == "RESTRICT", (name, fk.parent.name)


# ---- legacy separation (test 33) -----------------------------------------------------------------

def test_legacy_assessment_data_is_untouched_by_v1_work(svc, ctx):
    with ctx.db() as db:
        db.add(PlayerAssessmentDB(
            assessment_id="LEG1", player_id="P_A", pillar="physical", test_category="speed",
            test_type="sprint", methodology_version="legacy", test_date=date(2026, 1, 1),
            age_at_assessment_years=10, raw_data={"overall_score": 77.0}, calculated_metrics={"x": 1},
            ai_assisted=False, notes="legacy note", recorded_by_user_id="COACH", created_at=utcnow()))
        db.commit()

        def snapshot():
            row = db.get(PlayerAssessmentDB, "LEG1")
            db.refresh(row)
            return {c.name: getattr(row, c.name) for c in row.__table__.columns}

        before = snapshot()
    seed_two_children(svc)
    with ctx.db() as db:
        row = db.get(PlayerAssessmentDB, "LEG1")
        assert {c.name: getattr(row, c.name) for c in row.__table__.columns} == before
    legacy_columns = {c.name for c in PlayerAssessmentDB.__table__.columns}
    assert not [c for c in legacy_columns if "v1" in c]


# ---- test safety (tests 37-38) -----------------------------------------------------------------

def test_this_session_uses_scratch_storage_and_real_data_is_unchanged_so_far():
    import os
    import app.avatar_upload as avatar_upload
    import tests.conftest as guard

    for name in ("PLAYER_VIDEO_UPLOAD_DIR", "DRILL_UPLOAD_DIR", "VIDEO_ANALYSIS_OUTPUT_DIR"):
        assert not guard._inside_repo(Path(os.environ[name])), name
    assert not guard._inside_repo(Path(avatar_upload.AVATAR_DIR))
    database_file = guard.sqlite_file(os.environ["DATABASE_URL"])
    assert database_file is None or not guard._inside_repo(database_file)
    for path in guard._GUARDED_PATHS:
        assert guard._fingerprint(path) == guard._BEFORE[str(path)], str(path)


# ---- NOT_RECORDED contract (S1B3) ------------------------------------------------------------------

def test_not_recorded_reasons_are_the_frozen_standards_list(svc):
    assert d.NOT_RECORDED_REASONS == ("TIMER_MISSED_STOP", "TIMER_RESET_BEFORE_ENTRY", "DEVICE_FAILURE",
                                      "EVENT_UNCERTAIN", "OTHER")
    assert set(d.NOT_RECORDED_REASON_LABELS) == set(d.NOT_RECORDED_REASONS)
    assert not set(d.NOT_RECORDED_REASONS) & {r for reasons in d.INVALID_REASONS.values() for r in reasons}
    _, _, trials = build(svc, attempts=len(d.NOT_RECORDED_REASONS) + 1)
    for trial, reason in zip(trials, d.NOT_RECORDED_REASONS):
        saved = svc.record_not_recorded(trial.trial_id, SPRINT, reason, "COACH")
        assert saved.not_recorded_reason == reason and saved.value_centiseconds is None
    for old in ("TIMER_FAILURE", "TIMER_RESET_EARLY", "TIMER_WRONG_EVENT"):   # S1A's retired codes
        with pytest.raises(V1ValidationError):
            svc.record_not_recorded(trials[-1].trial_id, SPRINT, old, "COACH")
