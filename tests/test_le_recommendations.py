"""Recommendations from approved, still-valid knowledge, and the results log. Fixture drills and fixture player aliases only: nothing here
says anything about real players or about the quality of any training."""
import json

import pytest

from learning_engine import library, media as media_mod, outcomes, recommend
from learning_engine.review import ReviewService
from tests.ke_helpers import COACH, FixtureAnalyzer, SUGGESTION, approve_item, make_file, new_store, pass_, register
from tests.le_helpers import NOW

M = {"name": "clean_runs_30s", "unit": "count", "direction": "higher_is_better", "how": "count the runs through all markers without losing the ball in 30 seconds"}


def drill(store, tmp_path, name, *, skills, minutes=10, equipment=("markers",), age=(8, 12), measure=M, approve=True, notes=None):
    path = make_file(tmp_path / name, f"{name}.mp4")
    media_id = register(store, path)["media_id"]
    pass_(store, FixtureAnalyzer(suggestion={**SUGGESTION, "title": f"[FIXTURE] {name}"}, notes=notes))
    item = store.db.execute("select item_id from review_items where media_id=?", (media_id,)).fetchone()["item_id"]
    if approve:
        content = {"skill_tags": list(skills), "duration_minutes": minutes, "equipment": list(equipment), "age_min": age[0], "age_max": age[1], "title": f"[FIXTURE] {name}"}
        content["measure"] = measure
        approve_item(store, item, content)
    return item, media_id


def ctx(**kw):
    base = {"player_ref": "P-001", "age": 10, "fixture": True}
    return {**base, **kw}


@pytest.fixture()
def lib(tmp_path):
    s = new_store(tmp_path)
    d = {"close": drill(s, tmp_path, "close", skills=["close_control"]),
         "turn": drill(s, tmp_path, "turn", skills=["change_of_direction"], minutes=15, equipment=("cones", "ball"), measure=None),
         "older": drill(s, tmp_path, "older", skills=["close_control"], age=(13, 16)),
         "pending": drill(s, tmp_path, "pending", skills=["close_control"], approve=False)}
    return s, d


RATING = {"rating": 2, "scale_max": 5, "rated_by": "test-reviewer", "rated_on": "2026-10-01", "method": "coach observation"}


def test_only_approved_current_drills_are_candidates(lib):
    s, d = lib
    recs = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="test")
    assert [r["item_id"] for r in recs] == [d["close"][0]]                       # 'older' is for 13-16, 'pending' was never approved
    assert recs[0]["status"] == "awaiting_coach_review" and recs[0]["applies_only_after_coach_approval"] is True


def test_a_documented_rating_picks_the_weakest_skill_and_the_reason_names_the_rating_the_rater_and_the_date(lib):
    s, d = lib
    rec = recommend.recommend(s, ctx(skill_ratings={"close_control": RATING, "change_of_direction": {**RATING, "rating": 4}}), now=NOW, actor="test")[0]
    assert rec["personalization"] == "DOCUMENTED_RATING" and rec["item_id"] == d["close"][0]
    assert "close_control as 2/5" in rec["reason"]["why"] and "test-reviewer" in rec["reason"]["why"] and "2026-10-01" in rec["reason"]["why"]
    assert rec["item_version"] == ReviewService(s).get(d["close"][0])["version"] and rec["content_hash"]
    assert rec["measure"]["how"] and rec["evidence"]["timed_notes"] and all(n["t_end_s"] >= n["t_start_s"] for n in rec["evidence"]["timed_notes"])
    assert rec["missing"] == ["minutes_available", "equipment_available"] and rec["reason"]["limits_applied"]["age"] == 10


def test_an_undocumented_rating_is_ignored_and_absence_of_data_is_not_read_as_weakness(lib):
    s, d = lib
    bad = {"close_control": {"rating": 1}, "change_of_direction": {**RATING, "rating": 5, "rated_by": ""}}
    rec = recommend.recommend(s, ctx(skill_ratings=bad), now=NOW, actor="test")[0]
    assert rec["personalization"] == "GENERIC_NOT_PERSONALIZED" and "not personalised" in rec["reason"]["why"]
    assert sorted(n for n in rec["reason"]["notes"]) == ["RATING_IGNORED_NOT_DOCUMENTED:change_of_direction", "RATING_IGNORED_NOT_DOCUMENTED:close_control"]
    only_one = recommend.recommend(s, ctx(skill_ratings={"change_of_direction": {**RATING, "rating": 5}}), now=NOW, actor="test")[0]
    assert only_one["item_id"] == d["turn"][0] and only_one["reason"]["target_skill"] == "change_of_direction"      # a skill with no rating is never "the weak one"


def test_missing_age_gives_no_recommendation_and_names_what_is_missing(lib):
    s, _ = lib
    rec = recommend.recommend(s, {"player_ref": "P-001", "fixture": True}, now=NOW, actor="test")[0]
    assert rec["status"] == "not_generated" and rec["status_reason"] == "INSUFFICIENT_INFORMATION:age" and rec["missing"] == ["age"] and rec["item_id"] is None


def test_hard_limits_age_time_equipment_and_avoided_equipment(lib):
    s, d = lib
    assert recommend.recommend(s, ctx(goal_skill="change_of_direction", minutes_available=10), now=NOW, actor="t")[0]["status"] == "not_generated"          # 15 min drill
    assert recommend.recommend(s, ctx(goal_skill="change_of_direction", minutes_available=20, equipment_available=["Cones", "balls"]), now=NOW, actor="t")[0]["item_id"] == d["turn"][0]
    assert recommend.recommend(s, ctx(goal_skill="change_of_direction", equipment_available=["ball"]), now=NOW, actor="t")[0]["status"] == "not_generated"      # no cones
    assert recommend.recommend(s, ctx(goal_skill="change_of_direction", constraints={"avoid_equipment": ["cone"]}), now=NOW, actor="t")[0]["status"] == "not_generated"
    r = recommend.recommend(s, ctx(goal_skill="change_of_direction"), now=NOW, actor="t")[0]
    assert "measure: the approved drill has no coach-defined measurement" in r["missing"] and r["measure"] is None


def test_a_health_note_produces_no_drill_and_a_referral(lib):
    s, _ = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control", health_note="sore knee since Tuesday"), now=NOW, actor="test")[0]
    assert rec["status"] == "not_generated" and rec["status_reason"] == recommend.HEALTH and rec["item_id"] is None
    assert "sore knee" not in json.dumps(rec)                                                  # the text is neither stored nor interpreted


def test_the_context_is_strict_so_no_extra_personal_data_can_ride_along(lib):
    s, _ = lib
    for bad in ({"player_ref": "Ali Hassan", "age": 10}, {"player_ref": "P-1", "age": 10, "full_name": "Ali Hassan"}, {"player_ref": "P-1", "age": 2}, {"player_ref": "P-1", "age": 10, "constraints": {"x": 1}}):
        with pytest.raises(recommend.RecommendError):
            recommend.recommend(s, bad, now=NOW, actor="t")


def test_no_approved_knowledge_means_no_recommendation(tmp_path):
    s = new_store(tmp_path)
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    assert rec["status"] == "not_generated" and rec["status_reason"] == "NO_APPROVED_VALID_KNOWLEDGE"
    drill(s, tmp_path, "p", skills=["close_control"], approve=False)
    assert recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]["status"] == "not_generated"


def test_a_recommendation_is_flagged_when_its_knowledge_stops_being_valid_and_its_history_is_kept(lib):
    s, d = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    media_mod.withdraw_rights(s, d["close"][1], reason="owner withdrew permission", by="t", now=NOW)
    shown = recommend.get(s, rec["rec_id"])
    assert shown["status"] == "awaiting_coach_review"                                           # not yet re-checked
    assert recommend.refresh(s, NOW) == 1
    shown = recommend.get(s, rec["rec_id"])
    assert shown["status"] == "needs_rereview" and shown["status_reason"] == "APPROVAL_SUSPENDED:RIGHTS_WITHDRAWN" and [e["event"] for e in shown["events"]] == ["CREATED", "NEEDS_REREVIEW"]
    assert shown["item_id"] == d["close"][0] and shown["content_hash"]
    with pytest.raises(recommend.RecommendError):
        recommend.decide(s, rec["rec_id"], "approve", reviewer="t", reasons="applying it anyway", now=NOW)
    assert recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]["status"] == "not_generated"          # and no new recommendation rests on it


def test_a_coach_edit_of_the_drill_after_the_recommendation_flags_it(lib):
    s, d = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    svc = ReviewService(s)
    svc.save_content(d["close"][0], {**svc.get(d["close"][0])["content"], "duration_minutes": 12}, COACH)           # back to pending, approved content replaced by a draft
    assert recommend.list_all(s, NOW)[-1]["status_reason"] in ("DRILL_NOT_APPROVED_ANY_MORE", "DRILL_CONTENT_CHANGED_SINCE_THE_RECOMMENDATION") and recommend.get(s, rec["rec_id"])["status"] == "needs_rereview"


def test_the_coach_decides_each_recommendation_and_the_decision_is_logged(lib):
    s, _ = lib
    a = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    out = recommend.decide(s, a["rec_id"], "approve", reviewer="test-reviewer", reasons="fits this week's plan", now=NOW)
    assert out["status"] == "coach_approved_for_use" and out["applies_only_after_coach_approval"] is False and out["events"][-1]["actor"] == "test-reviewer"
    with pytest.raises(recommend.RecommendError):
        recommend.decide(s, a["rec_id"], "reject", reviewer="t", reasons="changing my mind", now=NOW)
    assert s.verify_audit()


# ---- recorded results ----------------------------------------------------------------------------------------------------------------------------------
def add(s, rec, date, before, after, mb="30s count, coach A", ma="30s count, coach A", executed=True, **kw):
    return outcomes.add_outcome(s, player_ref="P-001", session_date=date, executed=executed, recorded_by="test-reviewer", auth_mode="local_cli_unauthenticated", now=NOW,
                                rec_id=rec["rec_id"], measure_name="clean_runs_30s", unit="count", before_value=before, after_value=after, method_before=mb, method_after=ma, fixture=True, **kw)


def test_results_are_recorded_as_results_never_as_proof_and_change_nothing_in_the_library(lib):
    s, d = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    before = dict(s.db.execute("select current_version, review_status, approval_state from review_items where item_id=?", (d["close"][0],)).fetchone())
    hash_before = ReviewService(s).get(d["close"][0])["content_hash"]
    add(s, rec, "2026-10-02", 6, 8, conditions="indoor, new ball")
    summ = outcomes.summaries(s)[0]
    assert summ["sessions"][0]["recorded_change"] == 2 and summ["sessions"][0]["same_method"] is True and "does not show that the drill caused it" in summ["note"]
    assert summ["fixture"] is True and summ["sessions"][0]["conditions"] == "indoor, new ball"
    assert dict(s.db.execute("select current_version, review_status, approval_state from review_items where item_id=?", (d["close"][0],)).fetchone()) == before
    assert ReviewService(s).get(d["close"][0])["content_hash"] == hash_before and s.db.execute("select count(*) from analyses").fetchone()[0] == 4


def test_a_change_of_measuring_method_makes_the_result_not_comparable(lib):
    s, _ = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    add(s, rec, "2026-10-02", 6, 9, ma="stopwatch, coach B")
    summ = outcomes.summaries(s)[0]
    assert summ["sessions"][0]["comparable"] is False and summ["sessions"][0]["recorded_change"] is None and summ["suggestion"].startswith("NOT_COMPARABLE")


def test_three_sessions_without_improvement_suggest_a_plan_review_but_change_nothing(lib):
    s, d = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    for date, b, a in (("2026-10-02", 6, 6), ("2026-10-09", 6, 5), ("2026-10-16", 7, 7)):
        add(s, rec, date, b, a)
    assert outcomes.summaries(s)[0]["suggestion"].startswith("SUGGEST_COACH_REVIEWS_THE_PLAN")
    assert ReviewService(s).get(d["close"][0])["review_status"] == "approved"                  # the library is untouched
    add(s, rec, "2026-10-23", 7, 10)
    assert outcomes.summaries(s)[0]["suggestion"] is None


def test_a_session_not_done_cannot_carry_numbers_and_a_result_needs_its_method(lib):
    s, _ = lib
    rec = recommend.recommend(s, ctx(goal_skill="close_control"), now=NOW, actor="t")[0]
    with pytest.raises(outcomes.OutcomeError):
        add(s, rec, "2026-10-02", 6, 8, executed=False)
    with pytest.raises(outcomes.OutcomeError):
        outcomes.add_outcome(s, player_ref="P-001", session_date="2026-10-02", executed=True, recorded_by="test-reviewer", auth_mode="x", now=NOW, rec_id=rec["rec_id"], before_value=1, after_value=2)
    with pytest.raises(outcomes.OutcomeError):
        outcomes.add_outcome(s, player_ref="P-002", session_date="2026-10-02", executed=True, recorded_by="test-reviewer", auth_mode="x", now=NOW, rec_id=rec["rec_id"])        # another player's recommendation
    outcomes.add_outcome(s, player_ref="P-001", session_date="2026-10-02", executed=False, recorded_by="test-reviewer", auth_mode="x", now=NOW, rec_id=rec["rec_id"], conditions="rain")
    assert outcomes.summaries(s)[0]["sessions_done"] == 0


def test_no_function_here_trains_or_changes_a_model_or_the_library_from_results():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "learning_engine"
    for name in ("outcomes.py", "recommend.py"):
        text = (root / name).read_text()
        for word in ("torch", "numpy", "weights", "train(", "UPDATE review_items", "UPDATE item_versions", "INSERT INTO item_versions"):
            assert word not in text, (name, word)
