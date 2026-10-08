"""Explainable drill recommendations from APPROVED, STILL-VALID knowledge only. No AI service, no model, no hidden score.

Rules (all visible in the recommendation itself):
* Candidates are items a coach approved whose approval is current, whose media rights are active, and whose content is complete and has no
  unverified analyzer suggestion left. A drill whose source went bad, whose rights were withdrawn or that needs a new review is never a candidate.
* Hard filters: the player's age inside the drill's age range; the drill's duration not above the minutes available; the drill's equipment
  inside the equipment available; equipment the coach said to avoid.
* The target: the coach's stated goal skill; else the lowest DOCUMENTED rating (rated_by, rated_on, and a rating on a stated scale); else none,
  and the result is labelled GENERIC_NOT_PERSONALIZED. Missing data is never read as weakness.
* A health or injury note is not interpreted: the engine produces nothing and refers the player to the coach or a specialist.
* Every recommendation waits for a coach before it is applied, and is re-checked whenever it is shown: if the drill it rests on stops being
  valid it becomes `needs_rereview` (its history is kept).
Player data stays minimal: a local alias, an age, the ratings the coach typed, and constraints. Nothing is read from the application database."""
import json
import re
import secrets
from datetime import datetime

from learning_engine.review import content_problems
from learning_engine.store import ResearchStore, iso

ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
CONTEXT_KEYS = {"player_ref", "age", "goal_skill", "minutes_available", "equipment_available", "skill_ratings", "constraints", "health_note", "fixture"}
CONSTRAINT_KEYS = {"avoid_equipment", "max_items"}
HEALTH = "HEALTH_RESTRICTION_REFER_TO_COACH_OR_SPECIALIST"
GENERIC = "GENERIC_NOT_PERSONALIZED"


class RecommendError(ValueError):
    pass


def _norm_eq(text: str) -> str:
    t = re.sub(r"^\d+\s*x?\s*", "", str(text).lower().strip())
    return t[:-1] if t.endswith("s") and len(t) > 3 else t


def clean_context(raw: dict) -> dict:
    """Strict: unknown keys are refused (so extra personal data cannot ride along), values are checked."""
    if not isinstance(raw, dict) or set(raw) - CONTEXT_KEYS:
        raise RecommendError("unknown fields in the player context: " + ", ".join(sorted(set(raw) - CONTEXT_KEYS)) if isinstance(raw, dict) else "context must be an object")
    ref = raw.get("player_ref")
    if not isinstance(ref, str) or not ALIAS.match(ref):
        raise RecommendError("player_ref must be a short local alias (letters, digits, - or _), not a name")
    ctx: dict = {"player_ref": ref, "fixture": bool(raw.get("fixture", False))}
    age = raw.get("age")
    if age is not None:
        if isinstance(age, bool) or not isinstance(age, int) or not 3 <= age <= 21:
            raise RecommendError("age must be a whole number from 3 to 21")
        ctx["age"] = age
    minutes = raw.get("minutes_available")
    if minutes is not None:
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 180:
            raise RecommendError("minutes_available must be 1 to 180")
        ctx["minutes_available"] = minutes
    if raw.get("equipment_available") is not None:
        eq = raw["equipment_available"]
        if not isinstance(eq, list) or len(eq) > 30 or not all(isinstance(e, str) and 1 <= len(e) <= 60 for e in eq):
            raise RecommendError("equipment_available must be a list of short names")
        ctx["equipment_available"] = eq
    goal = raw.get("goal_skill")
    if goal is not None:
        if not isinstance(goal, str) or not re.match(r"^[a-z][a-z0-9_]{2,31}$", goal):
            raise RecommendError("goal_skill must look like close_control")
        ctx["goal_skill"] = goal
    ratings = raw.get("skill_ratings") or {}
    if not isinstance(ratings, dict) or len(ratings) > 20:
        raise RecommendError("skill_ratings must be an object")
    ctx["skill_ratings"] = ratings
    cons = raw.get("constraints") or {}
    if not isinstance(cons, dict) or set(cons) - CONSTRAINT_KEYS:
        raise RecommendError("constraints may hold only: " + ", ".join(sorted(CONSTRAINT_KEYS)))
    mi = cons.get("max_items", 3)
    if isinstance(mi, bool) or not isinstance(mi, int) or not 1 <= mi <= 5:
        raise RecommendError("constraints.max_items must be 1 to 5")
    av = cons.get("avoid_equipment", [])
    if not isinstance(av, list) or not all(isinstance(e, str) and 1 <= len(e) <= 60 for e in av):
        raise RecommendError("constraints.avoid_equipment must be a list of short names")
    ctx["constraints"] = cons
    if raw.get("health_note"):
        ctx["health_note"] = "present"                   # the text itself is not stored or interpreted
    return ctx


def documented_ratings(ratings: dict) -> tuple[dict, list]:
    """Only ratings with who rated, when, and a value on a stated scale count. Others are listed as ignored."""
    ok, ignored = {}, []
    for skill, r in ratings.items():
        valid = (isinstance(r, dict) and isinstance(r.get("rating"), (int, float)) and not isinstance(r.get("rating"), bool)
                 and isinstance(r.get("scale_max"), int) and 2 <= r["scale_max"] <= 10 and 1 <= r["rating"] <= r["scale_max"]
                 and isinstance(r.get("rated_by"), str) and r["rated_by"].strip() and isinstance(r.get("rated_on"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", r["rated_on"]))
        if valid and re.match(r"^[a-z][a-z0-9_]{2,31}$", skill):
            ok[skill] = r
        else:
            ignored.append(skill)
    return ok, ignored


def valid_items(store: ResearchStore) -> list[dict]:
    """Approved, current, rights-active, complete drills."""
    out = []
    for r in store.db.execute("SELECT r.item_id, r.current_version, r.media_id, r.analysis_id, r.source_video_id, v.content_json, v.content_hash FROM review_items r "
                              "JOIN item_versions v ON v.item_id=r.item_id AND v.version=r.current_version LEFT JOIN media_files m ON m.media_id=r.media_id "
                              "WHERE r.review_status='approved' AND r.approval_state='current' AND (r.media_id IS NULL OR m.status='active') ORDER BY r.item_id"):
        content = json.loads(r["content_json"])
        if content_problems(content) or content.get("unverified_fields"):
            continue
        out.append({"item_id": r["item_id"], "version": r["current_version"], "content": content, "content_hash": r["content_hash"], "analysis_id": r["analysis_id"],
                    "video_id": r["source_video_id"]})
    return out


def _evidence(store: ResearchStore, item: dict) -> dict:
    notes, link, source_status = [], None, None
    if item["analysis_id"]:
        notes = [dict(n) for n in store.db.execute("SELECT note_id, kind, text, t_start_s, t_end_s FROM knowledge_notes WHERE analysis_id=? AND kind IN ('step','observation','common_error') "
                                                    "ORDER BY t_start_s LIMIT 5", (item["analysis_id"],))]
    if item["video_id"]:
        s = store.db.execute("SELECT url, availability FROM sources WHERE video_id=?", (item["video_id"],)).fetchone()
        if s:
            link, source_status = s["url"], s["availability"]
    return {"source_link": link, "source_status": source_status, "timed_notes": notes,
            "note": None if notes else "NO_TIMED_EVIDENCE: this drill's content was written by a coach, not extracted from a timed analysis"}


def _outcome_note(store: ResearchStore, player: str, item_id: str) -> tuple[str | None, int]:
    rows = store.db.execute("SELECT executed, before_value, after_value, method_before, method_after FROM outcomes WHERE player_ref=? AND item_id=? AND executed=1 "
                            "AND before_value IS NOT NULL AND after_value IS NOT NULL ORDER BY session_date, outcome_id", (player, item_id)).fetchall()
    if not rows:
        return None, 0
    return f"already done {len(rows)} time(s) with recorded results (see the outcomes list)", len(rows)


def recommend(store: ResearchStore, raw_context: dict, *, now: datetime, actor: str) -> list[dict]:
    ctx = clean_context(raw_context)
    missing: list[str] = []
    ratings, ignored = documented_ratings(ctx["skill_ratings"])
    notes_out = [f"RATING_IGNORED_NOT_DOCUMENTED:{s}" for s in ignored]
    if "age" not in ctx:
        return [_store_none(store, ctx, now, actor, "INSUFFICIENT_INFORMATION:age", ["age"], notes_out)]
    if ctx.get("health_note"):
        return [_store_none(store, ctx, now, actor, HEALTH, [], notes_out + ["no drill was chosen: a health or injury matter is for the coach or a specialist"])]
    for key in ("minutes_available", "equipment_available"):
        if key not in ctx:
            missing.append(key)
    items = valid_items(store)
    if not items:
        return [_store_none(store, ctx, now, actor, "NO_APPROVED_VALID_KNOWLEDGE", missing, notes_out)]
    cands = []
    avoid = {_norm_eq(e) for e in ctx["constraints"].get("avoid_equipment", [])}
    have = {_norm_eq(e) for e in ctx.get("equipment_available", [])}
    for it in items:
        c = it["content"]
        if not c["age_min"] <= ctx["age"] <= c["age_max"]:
            continue
        if "minutes_available" in ctx and c["duration_minutes"] > ctx["minutes_available"]:
            continue
        eq = {_norm_eq(e) for e in c["equipment"]}
        if eq & avoid or ("equipment_available" in ctx and not eq <= have):
            continue
        cands.append(it)
    target, basis = None, GENERIC
    if ctx.get("goal_skill"):
        target, basis = ctx["goal_skill"], "COACH_STATED_GOAL"
    elif ratings:
        target = sorted(ratings, key=lambda s: (ratings[s]["rating"] / ratings[s]["scale_max"], s))[0]
        basis = "DOCUMENTED_RATING"
    if target:
        cands = [i for i in cands if target in i["content"].get("skill_tags", [])]
        if not cands:
            return [_store_none(store, ctx, now, actor, f"NO_APPROVED_DRILL_FOR_{target}_WITH_THESE_LIMITS", missing, notes_out)]
    elif not cands:
        return [_store_none(store, ctx, now, actor, "NO_APPROVED_DRILL_FITS_THE_LIMITS", missing, notes_out)]

    def rank(i):
        done = _outcome_note(store, ctx["player_ref"], i["item_id"])[1]
        return (0 if "measure" in i["content"] else 1, done, i["item_id"])
    cands.sort(key=rank)
    limit = ctx["constraints"].get("max_items", 3)
    out = []
    for it in cands[:limit]:
        c = it["content"]
        if basis == "DOCUMENTED_RATING":
            r = ratings[target]
            why = (f"The coach documented {target} as {r['rating']}/{r['scale_max']} (rated by {r['rated_by']} on {r['rated_on']}); it is the lowest documented rating, "
                   f"and this approved drill lists {target} among its skills.")
        elif basis == "COACH_STATED_GOAL":
            why = f"The coach's stated goal is {target}; this approved drill lists {target} among its skills."
        else:
            why = "No goal and no documented rating were given, so this is a general suggestion that fits the age, time and equipment limits. It is not personalised."
        m = c.get("measure")
        miss = list(missing) + ([] if m else ["measure: the approved drill has no coach-defined measurement"])
        note, _n = _outcome_note(store, ctx["player_ref"], it["item_id"])
        reason = {"basis": basis, "target_skill": target, "why": why, "limits_applied": {"age": ctx["age"], "minutes_available": ctx.get("minutes_available"),
                                                                                          "equipment_available": ctx.get("equipment_available")},
                  "history": note, "notes": notes_out, "approved_content_duration_minutes": c["duration_minutes"], "approved_equipment": c["equipment"]}
        out.append(_store(store, ctx, now, actor, it, basis, reason, _evidence(store, it), m, miss))
    return out


def _store(store, ctx, now, actor, it, basis, reason, evidence, measure, missing) -> dict:
    rec_id = "REC-" + secrets.token_hex(8)
    with store.transaction() as db:
        db.execute("INSERT INTO recommendations(rec_id,created_at,player_ref,is_fixture,request_json,item_id,item_version,content_hash,personalization,reason_json,evidence_json,"
                   "measure_json,missing_json,status,status_reason,status_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rec_id, iso(now), ctx["player_ref"], int(ctx["fixture"]), json.dumps(ctx, sort_keys=True), it["item_id"], it["version"], it["content_hash"], basis,
                    json.dumps(reason, sort_keys=True), json.dumps(evidence, sort_keys=True), json.dumps(measure) if measure else None, json.dumps(missing),
                    "awaiting_coach_review", None, iso(now)))
        db.execute("INSERT INTO recommendation_events(rec_id,at,event,actor,detail) VALUES (?,?,?,?,?)", (rec_id, iso(now), "CREATED", actor[:80], "awaiting coach review"))
        store.audit("RECOMMENDATION_CREATED", actor[:80], {"rec_id": rec_id, "item_id": it["item_id"], "personalization": basis, "fixture": ctx["fixture"]}, now)
    return get(store, rec_id)


def _store_none(store, ctx, now, actor, reason_code, missing, notes) -> dict:
    rec_id = "REC-" + secrets.token_hex(8)
    with store.transaction() as db:
        db.execute("INSERT INTO recommendations(rec_id,created_at,player_ref,is_fixture,request_json,personalization,reason_json,evidence_json,missing_json,status,status_reason,status_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (rec_id, iso(now), ctx["player_ref"], int(ctx["fixture"]), json.dumps(ctx, sort_keys=True), GENERIC,
                                                          json.dumps({"notes": notes}), "{}", json.dumps(missing), "not_generated", reason_code, iso(now)))
        db.execute("INSERT INTO recommendation_events(rec_id,at,event,actor,detail) VALUES (?,?,?,?,?)", (rec_id, iso(now), "NOT_GENERATED", actor[:80], reason_code))
    return get(store, rec_id)


def _validity(store: ResearchStore, rec) -> str | None:
    """None if the drill behind this recommendation is still valid and unchanged; otherwise the reason."""
    if rec["item_id"] is None:
        return None
    row = store.db.execute("SELECT r.review_status, r.approval_state, r.current_version, r.media_id, r.source_issue, v.content_hash, m.status AS media_status FROM review_items r "
                           "JOIN item_versions v ON v.item_id=r.item_id AND v.version=r.current_version LEFT JOIN media_files m ON m.media_id=r.media_id WHERE r.item_id=?", (rec["item_id"],)).fetchone()
    if row is None:
        return "DRILL_NO_LONGER_EXISTS"
    if row["approval_state"] != "current":
        return "APPROVAL_SUSPENDED:" + (row["source_issue"] or "REVIEW_REQUIRED")
    if row["review_status"] != "approved":
        return "DRILL_NOT_APPROVED_ANY_MORE"
    if row["media_id"] and row["media_status"] != "active":
        return "MEDIA_RIGHTS_WITHDRAWN"
    if row["current_version"] != rec["item_version"] or row["content_hash"] != rec["content_hash"]:
        return "DRILL_CONTENT_CHANGED_SINCE_THE_RECOMMENDATION"
    return None


def refresh(store: ResearchStore, now: datetime) -> int:
    """Re-check every live recommendation; flag those whose drill is no longer valid. History is kept. Returns how many were flagged."""
    flagged = 0
    for rec in store.db.execute("SELECT * FROM recommendations WHERE status IN ('awaiting_coach_review','coach_approved_for_use')").fetchall():
        reason = _validity(store, rec)
        if reason:
            with store.transaction() as db:
                db.execute("UPDATE recommendations SET status='needs_rereview', status_reason=?, status_at=? WHERE rec_id=?", (reason, iso(now), rec["rec_id"]))
                db.execute("INSERT INTO recommendation_events(rec_id,at,event,actor,detail) VALUES (?,?,?,?,?)", (rec["rec_id"], iso(now), "NEEDS_REREVIEW", "system", reason))
            flagged += 1
    return flagged


def decide(store: ResearchStore, rec_id: str, decision: str, *, reviewer: str, reasons: str, now: datetime) -> dict:
    if decision not in ("approve", "reject"):
        raise RecommendError("decision must be approve or reject")
    if not isinstance(reasons, str) or len(reasons.strip()) < 5:
        raise RecommendError("a reason is required")
    refresh(store, now)
    rec = store.db.execute("SELECT status FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    if rec is None:
        raise RecommendError("no such recommendation")
    if rec["status"] != "awaiting_coach_review":
        raise RecommendError(f"only a recommendation awaiting coach review can be decided (this one is {rec['status']})")
    status = "coach_approved_for_use" if decision == "approve" else "coach_rejected"
    with store.transaction() as db:
        db.execute("UPDATE recommendations SET status=?, status_reason=?, status_at=? WHERE rec_id=?", (status, reasons.strip()[:300], iso(now), rec_id))
        db.execute("INSERT INTO recommendation_events(rec_id,at,event,actor,detail) VALUES (?,?,?,?,?)", (rec_id, iso(now), decision.upper(), reviewer[:80], reasons.strip()[:300]))
        store.audit("RECOMMENDATION_" + decision.upper(), reviewer[:80], {"rec_id": rec_id}, now)
    return get(store, rec_id)


def get(store: ResearchStore, rec_id: str) -> dict:
    r = store.db.execute("SELECT * FROM recommendations WHERE rec_id=?", (rec_id,)).fetchone()
    if r is None:
        raise RecommendError("no such recommendation")
    d = dict(r)
    for k in ("request_json", "reason_json", "evidence_json", "missing_json"):
        d[k[:-5]] = json.loads(d.pop(k))
    d["measure"] = json.loads(d.pop("measure_json")) if d.get("measure_json") else None
    d.pop("measure_json", None)
    d["events"] = [dict(e) for e in store.db.execute("SELECT at, event, actor, detail FROM recommendation_events WHERE rec_id=? ORDER BY event_id", (rec_id,))]
    d["fixture"] = bool(d.pop("is_fixture"))
    d["applies_only_after_coach_approval"] = d["status"] != "coach_approved_for_use"
    return d


def list_all(store: ResearchStore, now: datetime) -> list[dict]:
    refresh(store, now)
    return [get(store, r["rec_id"]) for r in store.db.execute("SELECT rec_id FROM recommendations ORDER BY created_at, rowid")]
