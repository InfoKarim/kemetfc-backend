"""The eight-station overview of one V1 assessment (Unified Player Assessment, Phase 1).

Everything here is derived from rows that already exist. No station state is stored, nothing is
invented for a station that is not open, and no number is a score: only counts of attempts.

State of an open station:
  NOT_STARTED  no attempt yet
  IN_PROGRESS  at least one attempt, but not every series has its required valid attempts
  COMPLETE     every series has its required number of VALID attempts with a RECORDED time
A VALID attempt whose time was NOT_RECORDED (a timer problem) does not count toward the
requirement and is not counted against the attempt limit: it is repeated. The limits are the
frozen standard's and only describe progress; they never block an entry.

A station that is not open (S3, S5, S6, S7, S8) is COMING_SOON with no series and no counts.
"""
from sqlalchemy.orm import Session

from app.v1 import definitions as d
from app.v1.models import V1AssessmentDB, V1ManualMeasurementDB, V1StationRunDB, V1TrialDB

STATE_NOT_STARTED = "NOT_STARTED"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_COMPLETE = "COMPLETE"
STATE_COMING_SOON = "COMING_SOON"


def _series_progress(rule: d.SeriesRule, trials, recorded_trial_ids) -> dict:
    mine = [t for t in trials if t.series_key == rule.key]
    valid_recorded = sum(1 for t in mine if t.validity == "VALID" and t.trial_id in recorded_trial_ids)
    invalid = sum(1 for t in mine if t.validity == "INVALID")
    timer_problems = sum(1 for t in mine if t.validity == "VALID" and t.trial_id not in recorded_trial_ids)
    counted_attempts = len(mine) - timer_problems
    return {
        "series_key": rule.key, "label": rule.label,
        "attempts": len(mine), "valid_recorded": valid_recorded, "not_valid": invalid,
        "without_time": timer_problems,
        "required_valid": rule.required_valid, "max_attempts": rule.max_attempts,
        "over_attempt_limit": counted_attempts > rule.max_attempts,
        "complete": valid_recorded >= rule.required_valid,
    }


def build_station_overview(db: Session, assessment: V1AssessmentDB) -> dict:
    runs = {r.station_id: r for r in db.query(V1StationRunDB).filter_by(assessment_id=assessment.assessment_id)}
    stations = []
    for plan in d.STATION_PLAN:
        item = {"station_id": plan.station_id, "order": plan.order, "name": plan.name,
                "description": plan.description, "enabled": plan.enabled, "metric_id": plan.metric_id,
                "series": []}
        if not plan.enabled:
            item["state"] = STATE_COMING_SOON
            stations.append(item)
            continue
        run = runs.get(plan.station_id)
        trials = (db.query(V1TrialDB).filter_by(station_run_id=run.station_run_id).all()) if run else []
        trial_ids = [t.trial_id for t in trials]
        recorded = set()
        if trial_ids:
            recorded = {
                row[0] for row in db.query(V1ManualMeasurementDB.trial_id)
                .filter(V1ManualMeasurementDB.trial_id.in_(trial_ids),
                        V1ManualMeasurementDB.entry_status == "RECORDED").all()
            }
        item["series"] = [_series_progress(rule, trials, recorded) for rule in plan.series]
        if not trials:
            item["state"] = STATE_NOT_STARTED
        elif all(s["complete"] for s in item["series"]):
            item["state"] = STATE_COMPLETE
        else:
            item["state"] = STATE_IN_PROGRESS
        stations.append(item)
    coming_soon = [s["name"] for s in stations if s["state"] == STATE_COMING_SOON]
    can_complete = (not coming_soon and assessment.status == "IN_PROGRESS"
                    and all(s["state"] == STATE_COMPLETE for s in stations))
    return {
        "assessment_id": assessment.assessment_id, "player_id": assessment.player_id,
        "status": assessment.status, "standard_version": assessment.standard_version,
        "can_complete": can_complete,
        "completion_note": (
            "" if can_complete else
            f"{len(coming_soon)} of {len(stations)} stations are not available yet, so this assessment "
            "stays in progress." if coming_soon else
            "Finish every station to complete this assessment."),
        "stations": stations,
    }
