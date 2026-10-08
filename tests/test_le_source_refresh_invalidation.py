"""Learning engine, review fixes 3 and 4: what a refresh updates, what retention removes, and what happens to an APPROVED item
whose source stops being valid. Fake transports and temporary state only; no network."""
import json
from datetime import timedelta

import pytest

from learning_engine import discovery as dsc
from learning_engine.daily import run_daily
from learning_engine.review import AUTH_BACKEND, ReviewError, ReviewService, Reviewer
from learning_engine.store import ResearchStore
from tests.test_privacy_s0 import ctx  # noqa: F401  (a fixture)
from tests.le_helpers import CH1, CH2, FAKE_KEY, FakeTransport, NOW, Sleeps, config, iid, ok, search_item, vid, video_item

ENV = {"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}
COACH = Reviewer("coach-1", AUTH_BACKEND, "coach")
GOOD = {"title": "Cone weave dribble", "category": "dribbling", "steps": ["Set five cones in a line, one metre apart.", "Dribble through using the inside of each foot."],
        "equipment": ["5 cones", "1 ball"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Flat surface, no studs on hard ground."}
CFG = config(youtube={"max_pages_per_channel": 1})
LINK = f"https://www.youtube.com/watch?v={vid(1)}"


def run(tmp_path, days, videos, search=(), cfg=CFG):
    t = FakeTransport(search=[ok({"items": list(search)})], videos=[ok({"items": list(videos)})] if videos is not None else [])
    sl = Sleeps()
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW + timedelta(days=days), transport=t, sleep=sl, wall_clock=lambda: 10_000.0 + days * 86_400 + len(sl.calls))
    return report


def first_run(tmp_path, n=2, **titles):
    return run(tmp_path, 0, [video_item(i, title=titles.get(f"t{i}")) for i in range(1, n + 1)], search=[search_item(i, title=titles.get(f"t{i}")) for i in range(1, n + 1)])


def store(tmp_path):
    return ResearchStore(tmp_path / "state")


def src(tmp_path, n):
    s = store(tmp_path)
    try:
        row = s.db.execute("SELECT * FROM sources WHERE video_id=?", (vid(n),)).fetchone()
        return None if row is None else dict(row)
    finally:
        s.close()


def approve(tmp_path, n=1):
    item = iid(tmp_path / "state", n)
    s = store(tmp_path)
    svc = ReviewService(s)
    svc.save_content(item, GOOD, COACH)
    svc.decide(item, 2, "approved", COACH, "Inspected the source and wrote the steps myself.", [f"https://www.youtube.com/watch?v={vid(n)}"])
    s.close()
    return item


# ---- fix 3: a refresh updates everything it stored ---------------------------------------------------------------------------------------------------
def test_a_title_change_is_visible_after_a_refresh_with_its_date_duration_and_fingerprint(tmp_path):
    first_run(tmp_path, t1="Old Dribbling title")
    assert src(tmp_path, 1)["title"] == "Old Dribbling title"
    run(tmp_path, 28, [video_item(1, title="Brand New Cone Title", published="2026-09-20T00:00:00Z", duration="PT7M")])
    row = src(tmp_path, 1)
    assert row["title"] == "Brand New Cone Title" and row["published_at"] == "2026-09-20T00:00:00Z" and row["duration_s"] == 420
    assert row["text_fingerprint"] == dsc.fingerprint("Brand New Cone Title") and row["verified_at"].startswith((NOW + timedelta(days=28)).strftime("%Y-%m-%d"))
    s = store(tmp_path)
    assert ReviewService(s).get(iid(tmp_path / "state", 1))["source"]["title"] == "Brand New Cone Title"
    s.close()


def _without_published(item):
    del item["snippet"]["publishedAt"]


def _empty_title(item):
    item["snippet"]["title"] = ""


def _unreadable_duration(item):
    item["contentDetails"]["duration"] = "not-a-duration"


def _unknown_kids_flag(item):
    item["status"]["madeForKids"] = None


@pytest.mark.parametrize("damage", [_without_published, _empty_title, _unreadable_duration, _unknown_kids_flag])
def test_a_partial_or_malformed_response_changes_nothing_and_does_not_renew_validity(tmp_path, damage):
    first_run(tmp_path, t1="Old Dribbling title")
    before = src(tmp_path, 1)
    item = video_item(1, title="Changed title")
    damage(item)
    report = run(tmp_path, 28, [item])
    assert report["sources"]["invalid_items"] >= 1
    assert src(tmp_path, 1) == before                               # not one stored field moved, and verified_at did not advance


def test_repeated_partial_refreshes_cannot_keep_stale_fields_alive_past_the_window(tmp_path):
    first_run(tmp_path, n=1, t1="Old Dribbling title")
    run(tmp_path, 28, [video_item(1, title="Refreshed title")])                      # one good refresh on day 28
    for day in (35, 45, 55):
        part = video_item(1, title="Newer title")
        del part["snippet"]["publishedAt"]
        run(tmp_path, day, [part])
        assert src(tmp_path, 1)["title"] == "Refreshed title"                        # still the last verified copy
    assert src(tmp_path, 1)["verified_at"].startswith((NOW + timedelta(days=28)).strftime("%Y-%m-%d"))
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=59))
    assert src(tmp_path, 1) is None                                                  # 31 days after the last GOOD refresh: gone, not extended


def test_a_fingerprint_clash_keeps_both_sources_and_all_review_history_then_clears_when_titles_differ_again(tmp_path):
    first_run(tmp_path, t1="Cone Weave Drill", t2="Another Drill Name")
    item1, item2 = approve(tmp_path, 1), iid(tmp_path / "state", 2)
    run(tmp_path, 28, [video_item(1, title="Cone Weave Drill"), video_item(2, title="cone weave   drill!")])      # both sources now share a title
    a, b = src(tmp_path, 1), src(tmp_path, 2)
    assert a["text_fingerprint"] == dsc.fingerprint("Cone Weave Drill") and b["text_fingerprint"] is None and b["fingerprint_conflict_with"] == vid(1)
    s = store(tmp_path)
    svc = ReviewService(s)
    assert svc.get(item1)["review_status"] == "approved" and svc.get(item1)["content"]["title"] == "Cone weave dribble" and len(svc.history(item1)) == 1
    assert svc.get(item2)["source"]["video_id"] == vid(2)                            # the second candidate was not merged away
    assert s.db.execute("select count(*) from audit where event='FINGERPRINT_CONFLICT'").fetchone()[0] == 1 and s.verify_audit()
    s.close()
    run(tmp_path, 29, [video_item(1, title="Something Else Entirely")])                      # the approved one is renamed; the clash is over
    a, b = src(tmp_path, 1), src(tmp_path, 2)
    assert b["fingerprint_conflict_with"] is None and b["text_fingerprint"] == dsc.fingerprint("cone weave drill!") and a["text_fingerprint"] == dsc.fingerprint("Something Else Entirely")


def test_a_duplicate_follows_a_changed_canonical_title_or_becomes_its_own_candidate(tmp_path):
    items = [search_item(1, title="Cone Weave Drill"), search_item(2, title="cone weave drill")]
    run(tmp_path, 0, [video_item(1, title="Cone Weave Drill")], search=items)
    assert src(tmp_path, 2)["duplicate_of"] == vid(1)
    run(tmp_path, 28, [video_item(1, title="Totally New Name")])
    two = src(tmp_path, 2)
    assert two["duplicate_of"] is None                                               # no longer matches: a candidate in its own right
    s = store(tmp_path)
    assert s.db.execute("select count(*) from review_items where source_video_id=?", (vid(2),)).fetchone()[0] == 1
    s.close()


# ---- fix 3: past the window nothing from the API stays except the coach's own work ---------------------------------------------------------------------
def test_after_the_window_ids_links_and_titles_are_deleted_but_coach_content_and_decisions_stay(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=31))
    s = store(tmp_path)
    svc = ReviewService(s)
    kept = svc.get(item)
    assert kept["source"] is None and kept["source_state"] == "removed" and kept["review_status"] == "approved" and kept["content"] == GOOD
    history = svc.history(item)
    assert len(history) == 1 and history[0]["decision"] == "approved" and history[0]["sources_json"] == ["SOURCE_REMOVED_AFTER_RETENTION_WINDOW"]
    dump = "\n".join(s.db.iterdump())
    for forbidden in (vid(1), vid(2), LINK, "Dribbling drill number"):
        assert forbidden not in dump, forbidden                                      # nothing API-derived is left anywhere in the database
    assert [i["item_id"] for i in svc.approved_content()] == [item] and svc.approved_content()[0]["source_removed"] is True
    assert s.verify_audit()
    s.close()


def test_a_version_1_item_id_that_embeds_the_video_id_is_replaced_when_the_source_is_removed(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    s = store(tmp_path)
    legacy = f"SRC-{vid(1)}"
    with s.transaction() as db:
        db.execute("PRAGMA defer_foreign_keys=ON")
        db.execute("UPDATE review_items SET item_id=? WHERE item_id=?", (legacy, item))
        db.execute("UPDATE item_versions SET item_id=? WHERE item_id=?", (legacy, item))
        db.execute("UPDATE reviews SET item_id=? WHERE item_id=?", (legacy, item))
    s.close()
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=31))
    s = store(tmp_path)
    ids = [r["item_id"] for r in s.db.execute("select item_id from review_items")]
    assert len(ids) == 1 and vid(1) not in ids[0] and s.db.execute("select count(*) from item_versions where item_id=?", (ids[0],)).fetchone()[0] == 2
    assert s.db.execute("select count(*) from reviews where item_id=?", (ids[0],)).fetchone()[0] == 1
    s.close()


def test_new_item_ids_do_not_contain_the_video_id(tmp_path):
    first_run(tmp_path)
    assert vid(1) not in iid(tmp_path / "state", 1) and iid(tmp_path / "state", 1).startswith("SRC-")


# ---- fix 4: an approved item whose source stops being valid ------------------------------------------------------------------------------------------------
INVALIDATIONS = [
    ("private", lambda: [video_item(1, privacy="private")], "NOT_PUBLIC"),
    ("deleted", lambda: [], "NOT_RETURNED_BY_API"),
    ("made_for_kids", lambda: [video_item(1, kids=True)], "MADE_FOR_KIDS"),
    ("channel_changed", lambda: [video_item(1, channel=CH2)], "CHANNEL_CHANGED"),
    ("live", lambda: [video_item(1, live="upcoming")], "LIVE_OR_UPCOMING_BROADCAST"),
    ("too_short", lambda: [video_item(1, duration="PT2S")], "IMPLAUSIBLE_DURATION"),
]


@pytest.mark.parametrize("name,videos,reason", INVALIDATIONS, ids=[i[0] for i in INVALIDATIONS])
def test_an_approved_item_whose_source_becomes_invalid_keeps_its_history_and_needs_review(tmp_path, name, videos, reason):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    s = store(tmp_path)
    assert [i["item_id"] for i in ReviewService(s).approved_content()] == [item]
    s.close()
    report = run(tmp_path, 2, videos())
    s = store(tmp_path)
    svc = ReviewService(s)
    got = svc.get(item)
    assert got["review_status"] == "approved" and got["approval_state"] == "review_required" and got["needs_source_review"] is True
    assert got["source_issue"] == reason and got["source_issue_at"] and got["content"] == GOOD and got["published"] is False
    assert got["source"]["video_id"] == vid(1) and got["source"]["url"] == LINK                         # source identity stays visible
    assert got["source"]["availability"] in ("excluded", "unavailable") and got["source"]["exclusion_reason"] == reason     # current verification, apart from the decision
    assert got["last_decision"]["decision"] == "approved" and len(svc.history(item)) == 1                # the old decision is untouched
    assert svc.approved_content() == []                                                              # out of the valid export
    assert [i["item_id"] for i in svc.list_items(needs_source_review=True)] == [item]
    assert report["review"]["source_review_required"] == 1
    assert s.db.execute("select count(*) from audit where event='SOURCE_INVALIDATED_REVIEW_REQUIRED'").fetchone()[0] == 1 and s.verify_audit()
    s.close()


def test_a_source_that_becomes_public_again_does_not_bring_the_approval_back(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 2, [video_item(1, privacy="private")])
    run(tmp_path, 3, [video_item(1)])                                                             # public, valid, complete again
    s = store(tmp_path)
    svc = ReviewService(s)
    got = svc.get(item)
    assert got["source"]["availability"] == "available" and got["approval_state"] == "review_required" and got["review_status"] == "approved"
    assert svc.approved_content() == []
    s.close()


def test_re_review_needs_an_authorised_coach_the_current_version_and_a_confirmed_source(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 2, [video_item(1, privacy="private")])
    with pytest.raises(ReviewError):
        Reviewer("guardian-1", AUTH_BACKEND, "guardian")                                         # not a coach or admin
    s = store(tmp_path)
    svc = ReviewService(s)
    with pytest.raises(ReviewError, match="not confirmed available"):
        svc.decide(item, 2, "approved", COACH, "Looking again while it is still private.", [LINK])
    run(tmp_path, 3, [video_item(1)])
    with pytest.raises(ReviewError, match="changed since"):
        svc.decide(item, 1, "approved", COACH, "Deciding on an old version of the item.", [LINK])
    with pytest.raises(ReviewError, match="record the source"):
        svc.decide(item, 2, "approved", COACH, "Approving without recording what I looked at.", [])
    assert svc.get(item)["approval_state"] == "review_required" and svc.approved_content() == []   # none of that changed anything
    out = svc.decide(item, 2, "approved", Reviewer("admin-1", AUTH_BACKEND, "admin"), "Checked the video again today; it is the same drill.", [LINK])
    assert out["approval_state"] == "current" and out["source_issue"] is None and out["review_status"] == "approved"
    assert [i["item_id"] for i in svc.approved_content()] == [item]
    history = svc.history(item)
    assert [h["reviewer_id"] for h in history] == ["coach-1", "admin-1"]                           # both decisions are kept
    event = json.loads(s.db.execute("select data_json from audit where event='REVIEW_APPROVED' order by n desc limit 1").fetchone()["data_json"])
    assert event["re_review_after_source_change"] is True and event["previous_source_issue"] == "NOT_PUBLIC"
    assert s.db.execute("select count(*) from review_items where published<>0").fetchone()[0] == 0   # nothing was published to anyone
    s.close()


def test_a_coach_can_reject_an_item_whose_source_went_bad(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 2, [video_item(1, privacy="private")])
    s = store(tmp_path)
    svc = ReviewService(s)
    out = svc.decide(item, 2, "rejected", COACH, "The source is gone; I will not use this drill.", [])
    assert out["review_status"] == "rejected" and out["needs_source_review"] is False and svc.approved_content() == []
    with pytest.raises(ReviewError, match="only a pending"):
        svc.decide(item, 2, "approved", COACH, "Changing my mind without a source problem.", [LINK])
    s.close()


def test_an_item_that_was_never_approved_is_not_flagged_and_a_valid_source_changes_nothing(tmp_path):
    first_run(tmp_path)
    run(tmp_path, 2, [video_item(1, privacy="private")])
    s = store(tmp_path)
    assert ReviewService(s).list_items(needs_source_review=True) == []
    s.close()
    first = tmp_path / "other"
    first.mkdir()
    first_run(first)
    item = approve(first, 1)
    run(first, 2, [video_item(1)])
    s = store(first)
    got = ReviewService(s).get(item)
    assert got["approval_state"] == "current" and got["needs_source_review"] is False and len(ReviewService(s).approved_content()) == 1
    s.close()


# ---- fix 4 through the CLI -----------------------------------------------------------------------------------------------------------------------------
def test_the_cli_lists_and_shows_items_that_need_a_source_review(tmp_path, capsys):
    from learning_engine.cli import main
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 2, [video_item(1, kids=True)])
    cfg_file = tmp_path / "cfg.json"
    from tests.le_helpers import config_dict
    cfg_file.write_text(json.dumps(config_dict(youtube={"max_pages_per_channel": 1})))
    base = ["--config", str(cfg_file), "--state", str(tmp_path / "state")]
    capsys.readouterr()
    assert main(base + ["list", "--needs-source-review"]) == 0
    out = capsys.readouterr().out
    assert item in out and "NEEDS_REVIEW(MADE_FOR_KIDS)" in out
    assert main(base + ["show", item]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["needs_source_review"] is True and shown["source"]["exclusion_reason"] == "MADE_FOR_KIDS" and shown["history"][0]["decision"] == "approved"
    assert main(base + ["export-approved"]) == 0 and json.loads(capsys.readouterr().out) == []
    assert main(base + ["status"]) == 0 and json.loads(capsys.readouterr().out)["source_review_required"] == 1


def test_an_item_whose_source_was_removed_by_retention_can_still_be_edited_and_decided_as_the_coachs_own_content(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=31))
    s = store(tmp_path)
    svc = ReviewService(s)
    item = svc.get(item)["item_id"]
    svc.save_content(item, {**GOOD, "duration_minutes": 12}, COACH)
    assert svc.get(item)["review_status"] == "pending"
    out = svc.decide(item, 3, "approved", COACH, "My own drill; the source link is gone but the steps are mine.", [])
    assert out["review_status"] == "approved" and out["source_state"] == "removed" and out["source"] is None
    assert svc.history(item)[-1]["sources_json"] == []
    s.close()


# ---- fix 4 through the backend API ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def api_state(tmp_path, monkeypatch):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 2, [video_item(1, privacy="private")])
    monkeypatch.setenv("LEARNING_ENGINE_REVIEW_API_ENABLED", "1")
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", str(tmp_path / "state"))
    return tmp_path, item


def test_the_api_lists_shows_and_re_decides_an_item_whose_source_went_bad(ctx, api_state):
    tmp_path, item = api_state
    listed = ctx.coach.get("/learning-review/items", params={"needs_source_review": "true"}).json()
    assert [i["item_id"] for i in listed] == [item] and listed[0]["review_status"] == "approved" and listed[0]["needs_source_review"] is True
    shown = ctx.coach.get(f"/learning-review/items/{item}").json()
    assert shown["source_issue"] == "NOT_PUBLIC" and shown["source"]["availability"] == "excluded" and shown["history"][0]["decision"] == "approved"
    assert ctx.guard_a.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "x" * 20, "sources_checked": [LINK]}).status_code == 403
    refused = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Looking at it while it is still private.", "sources_checked": [LINK]})
    assert refused.status_code == 409 and "not confirmed available" in refused.json()["detail"]
    run(tmp_path, 3, [video_item(1)])
    assert ctx.coach.get("/learning-review/items", params={"needs_source_review": "true"}).json()[0]["item_id"] == item          # public again: still waiting for a coach
    done = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Looked at the video again today; same drill.", "sources_checked": [LINK]})
    assert done.status_code == 200 and done.json()["approval_state"] == "current" and done.json()["published"] is False
    assert ctx.coach.get("/learning-review/items", params={"needs_source_review": "true"}).json() == []
