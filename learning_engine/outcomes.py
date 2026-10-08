"""Session results recorded by a coach. They are RECORDED RESULTS, not proof: a change after a drill does not show the drill caused it.
Nothing here changes the library, a plan or any model. The only output beyond the list is a plain-rule suggestion that a coach may want to
look at the plan again."""
import re
from datetime import datetime

from learning_engine.store import ResearchStore, iso

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAUSE_NOTE = "recorded results only: a change after a drill does not show that the drill caused it"


class OutcomeError(ValueError):
    pass


def add_outcome(store: ResearchStore, *, player_ref: str, session_date: str, executed: bool, recorded_by: str, auth_mode: str, now: datetime,
                rec_id: str | None = None, item_id: str | None = None, measure_name: str | None = None, unit: str | None = None,
                before_value: float | None = None, after_value: float | None = None, method_before: str | None = None, method_after: str | None = None,
                conditions: str | None = None, coach_notes: str | None = None, fixture: bool = False) -> int:
    if not isinstance(player_ref, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$", player_ref):
        raise OutcomeError("player_ref must be a short local alias")
    if not DATE.match(session_date or ""):
        raise OutcomeError("session_date must be YYYY-MM-DD")
    if not recorded_by or len(recorded_by.strip()) < 2:
        raise OutcomeError("say who recorded the result")
    if rec_id:
        rec = store.db.execute("SELECT item_id, player_ref FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
        if rec is None:
            raise OutcomeError("no such recommendation")
        if rec["player_ref"] != player_ref:
            raise OutcomeError("that recommendation is for another player")
        item_id = item_id or rec["item_id"]
    if executed and (before_value is not None or after_value is not None):
        if before_value is None or after_value is None or not measure_name or not method_before or not method_after:
            raise OutcomeError("a before/after result needs both values, the measure name and the method used for each")
    if not executed and (before_value is not None or after_value is not None):
        raise OutcomeError("a session that was not done cannot have before/after values")
    with store.transaction() as db:
        cur = db.execute("INSERT INTO outcomes(rec_id,player_ref,item_id,is_fixture,session_date,executed,measure_name,unit,before_value,after_value,method_before,method_after,"
                         "conditions,coach_notes,recorded_by,auth_mode,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rec_id, player_ref, item_id, int(fixture), session_date, int(executed), measure_name, unit, before_value, after_value, method_before, method_after,
                          (conditions or "")[:300] or None, (coach_notes or "")[:500] or None, recorded_by.strip()[:80], auth_mode, iso(now)))
        return cur.lastrowid


def summaries(store: ResearchStore) -> list[dict]:
    """Per player and drill: the recorded sessions, whether before/after used the same method, the recorded change, and a rule-based suggestion."""
    out = []
    for g in store.db.execute("SELECT DISTINCT player_ref, item_id, is_fixture FROM outcomes WHERE item_id IS NOT NULL ORDER BY player_ref, item_id"):
        rows = [dict(r) for r in store.db.execute("SELECT * FROM outcomes WHERE player_ref=? AND item_id=? ORDER BY session_date, outcome_id", (g["player_ref"], g["item_id"]))]
        sessions, done = [], [r for r in rows if r["executed"]]
        for r in rows:
            s = {"date": r["session_date"], "executed": bool(r["executed"]), "conditions": r["conditions"], "coach_notes": r["coach_notes"]}
            if r["before_value"] is not None:
                same = r["method_before"] == r["method_after"]
                s.update(measure=r["measure_name"], unit=r["unit"], before=r["before_value"], after=r["after_value"], same_method=same,
                         recorded_change=round(r["after_value"] - r["before_value"], 6) if same else None,
                         comparable=same)
            sessions.append(s)
        measured = [s for s in sessions if "before" in s]
        suggestion = None
        if any(not s["same_method"] for s in measured):
            suggestion = "NOT_COMPARABLE: before and after were measured with different methods; check the measurement method before reading the change"
        elif len(measured) >= 3 and len({(s["measure"], s["unit"]) for s in measured}) == 1:
            last = measured[-3:]
            direction = store.db.execute("SELECT measure_json FROM recommendations WHERE item_id=? AND measure_json IS NOT NULL ORDER BY created_at DESC LIMIT 1", (g["item_id"],)).fetchone()
            higher = True
            if direction:
                import json
                higher = json.loads(direction["measure_json"]).get("direction", "higher_is_better") == "higher_is_better"
            gains = [(s["after"] - s["before"]) * (1 if higher else -1) for s in last]
            if all(x <= 0 for x in gains):
                suggestion = "SUGGEST_COACH_REVIEWS_THE_PLAN: the last 3 recorded sessions show no improvement on this measure (conditions may explain it; this is not proof either way)"
        out.append({"player_ref": g["player_ref"], "item_id": g["item_id"], "fixture": bool(g["is_fixture"]), "sessions_recorded": len(rows), "sessions_done": len(done),
                    "sessions": sessions, "suggestion": suggestion, "note": CAUSE_NOTE})
    return out
