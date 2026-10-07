"""Explainable drill recommendations: plain rules, every choice with its reasons. No neural network is used and none is
claimed to be validated. Only coach-reviewed weaknesses drive a recommendation; nothing here diagnoses, rates or scores
a child. (Separate from, and not replacing, the existing app/drill_recommendations.py; it adds history, a session time
budget and a reason for every choice.)
"""
from datetime import date

SEVERITY = {"low": 1, "medium": 2, "high": 3}
LEVELS = {"beginner": 1, "intermediate": 2, "advanced": 3}
METHOD = "EXPLAINABLE_RULES_V1"
DISCLAIMER = ("Rule-based suggestions for a coach to review. Not validated against player outcomes. No neural network was used. "
              "Not a medical or psychological assessment.")


def recommend(*, age: int, weaknesses: list[dict], drills: list[dict], history: list[dict], equipment: list[str],
              session_minutes: int, today: date, level: str | None = None, repeat_window_days: int = 7,
              variety_window_days: int = 30, max_drills: int = 4) -> dict:
    have = {e.strip().lower() for e in equipment}
    reviewed = [w for w in weaknesses if w.get("coach_reviewed") is True and w.get("severity") in SEVERITY]
    ignored = [{"category": w.get("category"), "reason": "NOT_COACH_REVIEWED" if w.get("coach_reviewed") is not True else "UNKNOWN_SEVERITY"}
               for w in weaknesses if w not in reviewed]
    last_done: dict[str, date] = {}
    for h in history:
        d = h["date"]
        if h["drill_id"] not in last_done or d > last_done[h["drill_id"]]:
            last_done[h["drill_id"]] = d
    excluded, candidates = [], []
    for drill in drills:
        why = None
        if not drill.get("active", True):
            why = "NOT_ACTIVE"
        elif not (drill["min_age"] <= age <= drill["max_age"]):
            why = f"NOT_AGE_APPROPRIATE ({drill['min_age']}-{drill['max_age']}, player is {age})"
        elif not {e.strip().lower() for e in drill.get("equipment", [])} <= have:
            why = "EQUIPMENT_NOT_AVAILABLE: " + ", ".join(sorted({e.strip().lower() for e in drill.get("equipment", [])} - have))
        elif drill["duration_minutes"] > session_minutes:
            why = "LONGER_THAN_THE_SESSION"
        elif level and drill.get("difficulty") in LEVELS and LEVELS[drill["difficulty"]] > LEVELS[level] + 0:
            why = "ABOVE_THE_PLAYER_LEVEL_SET_BY_THE_COACH"
        elif drill["drill_id"] in last_done and (today - last_done[drill["drill_id"]]).days < repeat_window_days:
            why = f"DONE_{(today - last_done[drill['drill_id']]).days}_DAYS_AGO_WITHIN_{repeat_window_days}_DAY_REST"
        if why:
            excluded.append({"drill_id": drill["drill_id"], "reason": why})
            continue
        matches = [w for w in reviewed if w["category"].strip().lower() == drill["category"].strip().lower()]
        if not matches:
            excluded.append({"drill_id": drill["drill_id"], "reason": "NO_COACH_REVIEWED_WEAKNESS_IN_THIS_CATEGORY"})
            continue
        weakness = max(matches, key=lambda w: SEVERITY[w["severity"]])
        score = SEVERITY[weakness["severity"]] * 10
        reasons = [f"targets the coach-reviewed {weakness['severity']} weakness '{weakness['category']}'",
                   f"suitable for age {age} ({drill['min_age']}-{drill['max_age']})", "all equipment available"]
        if drill["drill_id"] not in last_done or (today - last_done[drill["drill_id"]]).days >= variety_window_days:
            score += 2
            reasons.append(f"not practised in the last {variety_window_days} days (variety)")
        candidates.append({"drill_id": drill["drill_id"], "name": drill["name"], "category": drill["category"],
                           "weakness_category": weakness["category"], "duration_minutes": drill["duration_minutes"],
                           "score": score, "reasons": reasons})
    candidates.sort(key=lambda c: (-c["score"], c["drill_id"]))
    plan, minutes = [], 0
    for c in candidates:
        if len(plan) >= max_drills:
            excluded.append({"drill_id": c["drill_id"], "reason": "SESSION_ALREADY_HAS_THE_MAXIMUM_NUMBER_OF_DRILLS"})
        elif minutes + c["duration_minutes"] > session_minutes:
            excluded.append({"drill_id": c["drill_id"], "reason": "DOES_NOT_FIT_THE_REMAINING_SESSION_TIME"})
        else:
            plan.append(c)
            minutes += c["duration_minutes"]
    return {"method": METHOD, "validated": False, "neural_network_used": False, "disclaimer": DISCLAIMER,
            "plan": plan, "total_minutes": minutes, "session_minutes": session_minutes, "excluded": sorted(excluded, key=lambda e: e["drill_id"]),
            "ignored_weaknesses": ignored}
