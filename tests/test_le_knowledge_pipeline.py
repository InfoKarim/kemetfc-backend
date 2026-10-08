"""The knowledge pipeline: queue, local analysis, library, drafts. Test doubles only; nothing here understands a video."""
import hashlib
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from learning_engine import dashboard, library, media as media_mod, workqueue
from learning_engine.analyzer import NoAnalyzer
from learning_engine.daily import run_daily
from learning_engine.review import ReviewError, ReviewService
from learning_engine.store import ResearchStore
from tests.ke_helpers import COACH, FixtureAnalyzer, NOTES, PIPE, SUGGESTION, approve_item, make_file, new_store, pass_, register
from tests.le_helpers import FAKE_KEY, NOW, FakeTransport, NoNetwork, Sleeps, config, ok, search_item, video_item, vid

ENV = {"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}


def live_cfg(**pipeline):
    return config(mode="live", network_enabled=True, youtube={"max_pages_per_channel": 1}, pipeline={"enabled": True, **pipeline})


def discover(tmp_path, n=2, extra_video=None, analyzer=None, cfg=None):
    items = extra_video or [video_item(i) for i in range(1, n + 1)]
    t = FakeTransport(search=[ok({"items": [search_item(i) for i in range(1, n + 1)]})], videos=[ok({"items": items})])
    sl = Sleeps()
    return run_daily(cfg or live_cfg(), tmp_path / "state", env=ENV, now=NOW, transport=t, sleep=sl, wall_clock=lambda: 1.0 + len(sl.calls), analyzer=analyzer)


def q(store, sql="select * from work_queue"):
    return [dict(r) for r in store.db.execute(sql)]


# ---- the queue and what a YouTube candidate can become ---------------------------------------------------------------------------------------
def test_discovered_youtube_candidates_wait_for_media_and_excluded_ones_are_skipped_with_the_reason(tmp_path):
    report = discover(tmp_path, n=3, extra_video=[video_item(1), video_item(2, privacy="private"), video_item(3, kids=True)])
    s = ResearchStore(tmp_path / "state")
    rows = {r["video_id"]: r for r in q(s)}
    assert rows[vid(1)]["state"] == "awaiting_media" and rows[vid(1)]["reason"] == workqueue.NO_MEDIA
    assert rows[vid(2)]["state"] == "skipped" and rows[vid(2)]["reason"] == "SOURCE_NOT_PUBLIC" and rows[vid(3)]["reason"] == "SOURCE_MADE_FOR_KIDS"
    k = report["knowledge"]
    assert k["sources_discovered"] == 3 and k["videos_analyzed"] == 0 and k["analyses_that_produced_knowledge"] == 0 and k["queue"]["awaiting_media"] == 1
    assert report["services"]["openai"]["status"] == "skipped"
    s.close()


def test_a_youtube_candidate_never_reaches_ready_or_analysed_without_authorised_media_even_with_an_analyzer(tmp_path):
    analyzer = FixtureAnalyzer()
    report = discover(tmp_path, analyzer=analyzer)
    s = ResearchStore(tmp_path / "state")
    assert analyzer.calls == [] and {r["state"] for r in q(s)} == {"awaiting_media"} and report["knowledge"]["fixture_analyses"] == 0
    assert s.db.execute("select count(*) from analyses").fetchone()[0] == 0
    s.close()


def test_with_no_analyzer_a_ready_source_waits_and_says_so_without_using_an_attempt_or_inventing_anything(tmp_path):
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    res = pass_(s, NoAnalyzer())
    row = q(s)[0]
    assert res.blocked == ["ANALYZER_NOT_CONFIGURED"] and row["state"] == "ready" and row["attempts"] == 0 and row["last_error"] == "ANALYZER_NOT_CONFIGURED"
    assert s.db.execute("select count(*) from analyses").fetchone()[0] == 0 and s.db.execute("select count(*) from review_items").fetchone()[0] == 0
    s.close()


# ---- analysing a registered local file ------------------------------------------------------------------------------------------------------------
def test_a_registered_file_is_analysed_into_notes_with_times_a_pending_draft_and_a_library_record(tmp_path):
    s = new_store(tmp_path)
    media_id = register(s, make_file(tmp_path / "m"))["media_id"]
    res = pass_(s, FixtureAnalyzer())
    assert (res.analyzed, res.knowledge_extracted, res.drafts_created) == (1, 1, 1)
    row = q(s)[0]
    assert row["state"] == "draft_ready" and row["analysis_id"]
    item = q(s, "select * from review_items")[0]
    assert item["origin"] == "LOCAL_MEDIA" and item["review_status"] == "pending" and item["published"] == 0 and item["media_id"] == media_id and item["approval_state"] == "current"
    view = library.draft_view(s, item["item_id"])
    assert view["analysis"]["is_fixture"] == 1 and view["analysis"]["analyzer_name"] == "FIXTURE_ANALYZER_NOT_REAL" and view["analysis"]["status"] == "knowledge_extracted"
    assert len(view["extracted_notes"]) == 5 and all(n["t_end_s"] >= n["t_start_s"] >= 0 for n in view["extracted_notes"])
    # the layers stay apart: extracted notes / analyzer suggestion / coach decision
    sug = view["analyzer_suggestion"]
    assert sug["steps"] == [NOTES[1]["text"], NOTES[2]["text"]] and sug["equipment"] == ["four small markers in a line"]            # steps and equipment only from timed notes
    assert set(sug["unverified_fields"]) == {"age_min", "age_max", "duration_minutes", "measure", "objective", "safety_notes", "skill_tags"}
    assert view["coach_versions"] == [] and view["coach_decisions"] == [] and view["review_status"] == "pending"
    entry = library.entry(s, media_id)
    assert entry["rights"]["training_allowed"] is False and entry["rights"]["analysis_allowed"] is True and entry["analyses"][0]["uncertainties"]
    assert [e["media_id"] for e in library.entries(s)] == [media_id]
    assert ReviewService(s).approved_content() == []
    s.close()


def test_statements_without_a_valid_video_time_are_discarded_and_counted_never_kept(tmp_path):
    bad = [{"kind": "step", "text": "Spin the ball twice with the left foot", "t_start_s": None, "t_end_s": 5},
           {"kind": "step", "text": "Use exactly seven cones", "t_start_s": 10, "t_end_s": 4},
           {"kind": "step", "text": "Past the end of the video", "t_start_s": 100, "t_end_s": 120},
           {"kind": "feelings", "text": "This looks like a great drill", "t_start_s": 1, "t_end_s": 2},
           {"kind": "step", "text": "Dribble forward with the ball close to the feet", "t_start_s": 8, "t_end_s": 14}]
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    pass_(s, FixtureAnalyzer(notes=bad, suggestion={}, duration_s=60))
    a = q(s, "select * from analyses")[0]
    assert a["rejected_notes"] == 4 and q(s, "select count(*) n from knowledge_notes")[0]["n"] == 1 and "discarded" in a["uncertainties_json"]
    s.close()


def test_an_analysis_that_yields_nothing_is_recorded_but_is_not_counted_as_knowledge(tmp_path):
    s = new_store(tmp_path)
    media_id = register(s, make_file(tmp_path / "m"))["media_id"]
    res = pass_(s, FixtureAnalyzer(notes=[], suggestion={}))
    assert (res.analyzed, res.knowledge_extracted, res.no_knowledge) == (1, 0, 1)
    assert q(s)[0]["state"] == "skipped" and q(s)[0]["reason"] == "NO_KNOWLEDGE_EXTRACTED"
    assert q(s, "select status from analyses")[0]["status"] == "no_knowledge" and library.entries(s) == [] and q(s, "select * from review_items") == []
    assert media_id
    s.close()


def test_the_same_file_is_never_registered_or_queued_twice(tmp_path):
    s = new_store(tmp_path)
    f = make_file(tmp_path / "m")
    a, b = register(s, f), register(s, make_file(tmp_path / "other", "copy.mp4", f.read_bytes()))
    assert a["media_id"] == b["media_id"] and b["duplicate"] is True and len(q(s)) == 1 and len(q(s, "select * from media_files")) == 1
    s.close()


# ---- reliability: lease, resume, concurrency, failure, limits ------------------------------------------------------------------------------------------
def test_a_crash_while_processing_is_recovered_once_and_never_duplicates_knowledge(tmp_path):
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    claimed = workqueue.claim_next(s, NOW, owner="W-crashed", lease_minutes=30)            # the worker dies here, holding the lease
    assert claimed["state"] == "ready" and q(s)[0]["state"] == "processing"
    res = pass_(s, FixtureAnalyzer(), now=NOW + timedelta(minutes=10))                        # lease still valid: nobody else touches it
    assert res.analyzed == 0 and q(s)[0]["state"] == "processing"
    res = pass_(s, FixtureAnalyzer(), now=NOW + timedelta(minutes=31))                        # lease expired: recovered (an attempt), backed off
    assert res.recovered == 1 and q(s)[0]["attempts"] == 1 and q(s)[0]["state"] == "ready"
    pass_(s, FixtureAnalyzer(), now=NOW + timedelta(minutes=31, seconds=90))
    assert q(s)[0]["state"] == "draft_ready" and len(q(s, "select * from analyses")) == 1 and len(q(s, "select * from review_items")) == 1
    pass_(s, FixtureAnalyzer(), now=NOW + timedelta(hours=2))                                 # running again changes nothing
    assert len(q(s, "select * from analyses")) == 1
    s.close()


def test_a_worker_that_lost_its_lease_cannot_write_a_result(tmp_path):
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    row = workqueue.claim_next(s, NOW, owner="W-a", lease_minutes=30)
    media = dict(s.db.execute("select * from media_files").fetchone())
    out = library.record_analysis(s, row, media, FixtureAnalyzer(), FixtureAnalyzer().analyze(None), started=NOW, now=NOW, lease_owner="W-someone-else")
    assert out == {"status": "lost_lease"} and q(s, "select * from analyses") == []
    s.close()


def test_two_workers_cannot_claim_the_same_source(tmp_path):
    ResearchStore(tmp_path / "state").close()
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    s.close()
    won, lock = [], threading.Lock()

    def worker(i):
        st = ResearchStore(tmp_path / "state")
        try:
            row = workqueue.claim_next(st, NOW, owner=f"W-{i}", lease_minutes=30)
            if row:
                with lock:
                    won.append(i)
        finally:
            st.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(won) == 1


def test_one_failing_source_does_not_stop_the_others_and_retries_back_off_then_stop(tmp_path):
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "a", "a.mp4"), now=NOW)
    register(s, make_file(tmp_path / "b", "b.mp4"), now=NOW + timedelta(seconds=1))

    class Flaky(FixtureAnalyzer):
        def analyze(self, path):
            if path.name == "a.mp4":
                raise RuntimeError("secret path /Users/someone/clip should not be stored")
            return super().analyze(path)

    res = pass_(s, Flaky())
    rows = {r["media_id"]: r for r in q(s)}
    by_name = {m["display_name"]: m["media_id"] for m in q(s, "select * from media_files")}
    assert res.knowledge_extracted == 1 and rows[by_name["b.mp4"]]["state"] == "draft_ready"
    a = rows[by_name["a.mp4"]]
    assert a["state"] == "ready" and a["attempts"] == 1 and a["next_attempt_at"] == "2026-10-08T15:01:00Z" and a["last_error"] == "ANALYZER_ERROR_RuntimeError" and "Users" not in str(a)
    pass_(s, Flaky(), now=NOW + timedelta(seconds=30))                                          # too early: not retried
    assert q(s, f"select attempts from work_queue where media_id='{by_name['a.mp4']}'")[0]["attempts"] == 1
    pass_(s, Flaky(), now=NOW + timedelta(seconds=61))                                          # attempt 2, wait doubles to 120 s
    assert q(s, f"select next_attempt_at, attempts from work_queue where media_id='{by_name['a.mp4']}'")[0] == {"next_attempt_at": "2026-10-08T15:03:01Z", "attempts": 2}
    pass_(s, Flaky(), now=NOW + timedelta(minutes=10))                                          # attempt 3 = max_attempts
    last = q(s, f"select state, attempts, reason from work_queue where media_id='{by_name['a.mp4']}'")[0]
    assert last == {"state": "failed", "attempts": 3, "reason": "MAX_ATTEMPTS_REACHED"}
    pass_(s, Flaky(), now=NOW + timedelta(days=1))
    assert q(s, f"select attempts from work_queue where media_id='{by_name['a.mp4']}'")[0]["attempts"] == 3          # a failed source is left alone
    s.close()


def test_the_pass_stops_at_the_item_limit_and_the_time_limit(tmp_path):
    s = new_store(tmp_path)
    for i in range(4):
        register(s, make_file(tmp_path / f"m{i}", f"c{i}.mp4"), now=NOW + timedelta(seconds=i))
    cfg = PIPE.__class__(**{**PIPE.__dict__, "max_items_per_run": 2})
    res = pass_(s, FixtureAnalyzer(), cfg=cfg)
    assert res.analyzed == 2 and res.stopped_by == "item_limit" and workqueue.counts(s)["ready"] == 2
    ticks = iter([0, 0, 1000, 1000, 2000])
    cfg2 = PIPE.__class__(**{**PIPE.__dict__, "max_seconds_per_run": 500})
    res2 = pass_(s, FixtureAnalyzer(), cfg=cfg2, time_fn=lambda: next(ticks))
    assert res2.stopped_by == "time_limit" and res2.analyzed == 1
    s.close()


def test_a_missing_changed_oversized_or_unpermitted_file_is_skipped_with_the_reason_and_never_analysed(tmp_path):
    s = new_store(tmp_path)
    paths = {n: make_file(tmp_path / n, f"{n}.mp4") for n in ("gone", "changed", "big", "withdrawn")}
    for i, n in enumerate(paths):
        register(s, paths[n], now=NOW + timedelta(seconds=i))
    big = make_file(tmp_path / "big2", "big.mp4", b"x" * (PIPE.max_file_mb * 1024 * 1024 + 1))
    paths["gone"].unlink()
    paths["changed"].write_bytes(b"different bytes now")
    mid = {m["display_name"]: m["media_id"] for m in q(s, "select * from media_files")}
    with pytest.raises(media_mod.MediaError):
        register(s, big)                                                                        # refused already at registration (over the limit)
    media_mod.withdraw_rights(s, mid["withdrawn.mp4"], reason="owner withdrew permission", by="test", now=NOW)
    analyzer = FixtureAnalyzer()
    cfg = PIPE.__class__(**{**PIPE.__dict__, "max_file_mb": 1})
    paths["big"].write_bytes(b"y" * (1024 * 1024 + 5))                                           # grown past the limit after registration
    res = pass_(s, analyzer, cfg=cfg)
    got = {m: q(s, f"select reason from work_queue where media_id='{mid[m]}'")[0]["reason"] for m in mid}
    assert got == {"gone.mp4": "MEDIA_FILE_MISSING", "changed.mp4": "MEDIA_FILE_CHANGED", "big.mp4": "FILE_TOO_LARGE", "withdrawn.mp4": "RIGHTS_WITHDRAWN"}
    assert analyzer.calls == [] and res.analyzed == 0
    s.close()


def test_an_external_analyzer_is_never_called_and_minors_are_never_sent_outside(tmp_path):
    class External(FixtureAnalyzer):
        external = True

        def analyze(self, path):
            raise AssertionError("an external analyzer must never be called")

    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "a", "a.mp4"), minors="yes", now=NOW)
    register(s, make_file(tmp_path / "b", "b.mp4"), minors="no", now=NOW + timedelta(seconds=1))
    res = pass_(s, External())
    assert res.skipped == {"EXTERNAL_ANALYSIS_NOT_ENABLED_AND_MINORS_NEVER_SENT_OUTSIDE": 1, "EXTERNAL_ANALYSIS_NOT_ENABLED": 1}
    s.close()


def test_media_registration_needs_a_written_basis_and_can_never_allow_training(tmp_path):
    s = new_store(tmp_path)
    f = make_file(tmp_path / "m")
    base = dict(rights_grantor="the owner", rights_note=None, minors_visible="no", recorded_by="test", now=NOW, max_file_mb=5)
    for bad in ({"rights_basis": "I think it is fine"}, {"rights_basis": "OWN_RECORDING", "rights_grantor": "x"}, {"rights_basis": "OWN_RECORDING", "minors_visible": "maybe"},
                {"rights_basis": "OWN_RECORDING", "recorded_by": ""}):
        with pytest.raises(media_mod.MediaError):
            media_mod.register_media(s, f, **{**base, **bad})
    with pytest.raises(media_mod.MediaError):
        media_mod.register_media(s, Path("relative.mp4"), rights_basis="OWN_RECORDING", **base)
    link = tmp_path / "link.mp4"
    link.symlink_to(f)
    with pytest.raises(media_mod.MediaError):
        media_mod.register_media(s, link, rights_basis="OWN_RECORDING", **base)
    mid = media_mod.register_media(s, f, rights_basis="LICENSED", **base)["media_id"]
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("UPDATE media_files SET training_allowed=1 WHERE media_id=?", (mid,))
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("UPDATE media_files SET analysis_allowed=0 WHERE media_id=?", (mid,))
    assert "file" not in q(s, "select * from audit")[0]["data_json"] and str(f) not in "".join(r["data_json"] for r in q(s, "select * from audit"))
    s.close()


# ---- review: the analyzer's suggestion is not a decision --------------------------------------------------------------------------------------------
def test_a_draft_cannot_be_approved_until_a_coach_confirms_every_analyzer_suggestion(tmp_path):
    s = new_store(tmp_path)
    register(s, make_file(tmp_path / "m"))
    pass_(s, FixtureAnalyzer())
    item = q(s, "select item_id from review_items")[0]["item_id"]
    svc = ReviewService(s)
    with pytest.raises(ReviewError, match="not complete|not yet confirmed"):
        svc.decide(item, 1, "approved", COACH, "Approving the analyzer draft as it is.", [])
    draft = svc.get(item)["content"]
    full = {**draft, "safety_notes": "Keep the area clear of other players."}
    saved = svc.save_content(item, full, COACH)                                                    # still carries unverified_fields
    with pytest.raises(ReviewError, match="not yet confirmed"):
        svc.decide(item, saved["version"], "approved", COACH, "Approving without confirming the suggestions.", [])
    out = approve_item(s, item)
    assert out["review_status"] == "approved" and out["valid_for_export"] is True and out["last_decision"]["review_basis"] == "LOCAL_MEDIA_ANALYSIS"
    assert "unverified_fields" not in out["content"]
    view = library.draft_view(s, item)
    assert view["analyzer_suggestion"]["unverified_fields"] and len(view["coach_versions"]) >= 1 and view["coach_decisions"][0]["decision"] == "approved"      # three layers stay apart
    s.close()


def test_a_reanalysis_makes_a_new_pending_draft_and_never_changes_approved_knowledge(tmp_path):
    s = new_store(tmp_path)
    media_id = register(s, make_file(tmp_path / "m"))["media_id"]
    pass_(s, FixtureAnalyzer())
    first = q(s, "select item_id from review_items")[0]["item_id"]
    approved = approve_item(s, first)
    before = dict(s.db.execute("select current_version, review_status from review_items where item_id=?", (first,)).fetchone())
    assert workqueue.request_reanalysis(s, media_id, NOW) is True
    better = FixtureAnalyzer(notes=NOTES + [{"kind": "step", "text": "Finish with a shot", "t_start_s": 26, "t_end_s": 30}])
    better.version = "test-2"
    pass_(s, better, now=NOW + timedelta(minutes=1))
    items = q(s, "select * from review_items order by created_at, rowid")
    assert len(items) == 2 and items[1]["review_status"] == "pending" and items[1]["supersedes_item_id"] == first
    assert dict(s.db.execute("select current_version, review_status from review_items where item_id=?", (first,)).fetchone()) == before
    assert [i["item_id"] for i in ReviewService(s).approved_content()] == [first] and ReviewService(s).get(first)["content_hash"] == approved["content_hash"]
    assert [a["version"] for a in q(s, "select version from analyses order by version")] == [1, 2]
    s.close()


def test_withdrawn_rights_suspend_approved_knowledge_but_keep_history(tmp_path):
    s = new_store(tmp_path)
    media_id = register(s, make_file(tmp_path / "m"))["media_id"]
    pass_(s, FixtureAnalyzer())
    item = q(s, "select item_id from review_items")[0]["item_id"]
    approve_item(s, item)
    assert media_mod.withdraw_rights(s, media_id, reason="owner withdrew permission", by="test", now=NOW) == 1
    got = ReviewService(s).get(item)
    assert got["needs_source_review"] is True and got["source_issue"] == "RIGHTS_WITHDRAWN" and ReviewService(s).approved_content() == [] and len(ReviewService(s).history(item)) == 1
    assert len(q(s, "select * from knowledge_notes")) == 5
    s.close()


# ---- retention and the library ---------------------------------------------------------------------------------------------------------------------------
def test_after_the_retention_window_youtube_identifiers_go_but_the_local_analysis_and_decisions_stay(tmp_path):
    discover(tmp_path, n=1)
    s = ResearchStore(tmp_path / "state")
    queue_id = q(s)[0]["queue_id"]
    media_id = register(s, make_file(tmp_path / "m"), queue_id=queue_id)["media_id"]
    assert q(s)[0]["state"] == "ready"
    pass_(s, FixtureAnalyzer())
    item = q(s, "select item_id from review_items")[0]["item_id"]
    assert q(s, "select media_id from review_items")[0]["media_id"] == media_id
    approve_item(s, item, sources=[f"https://www.youtube.com/watch?v={vid(1)}"])
    s.close()
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=31))
    s = ResearchStore(tmp_path / "state")
    assert s.db.execute("select count(*) from sources").fetchone()[0] == 0 and vid(1) not in "\n".join(s.db.iterdump())
    got = ReviewService(s).get(item)
    assert got["source_state"] == "removed" and got["review_status"] == "approved" and len(q(s, "select * from knowledge_notes")) == 5
    e = library.entry(s, media_id)
    assert e["source"]["kind"] == "LOCAL_FILE" and e["analyses"][0]["status"] == "knowledge_extracted" and e["items"][0]["decisions"][0]["decision"] == "approved"
    s.close()


def test_a_youtube_candidate_that_never_got_media_is_removed_with_its_queue_row_after_the_window(tmp_path):
    discover(tmp_path, n=2)
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=31))
    s = ResearchStore(tmp_path / "state")
    assert q(s) == [] and s.db.execute("select count(*) from sources").fetchone()[0] == 0
    s.close()


# ---- the daily report and the page -------------------------------------------------------------------------------------------------------------------------
def test_the_daily_report_keeps_discovered_analysed_and_knowledge_apart_and_labels_fixture_work(tmp_path):
    media_dir = tmp_path / "m"
    discover(tmp_path, n=2)
    s = ResearchStore(tmp_path / "state")
    register(s, make_file(media_dir))
    s.close()
    sl = Sleeps()
    report = run_daily(live_cfg(), tmp_path / "state", env=ENV, now=NOW + timedelta(days=1), transport=FakeTransport(search=[ok({"items": []})], videos=[ok({"items": [video_item(1), video_item(2)]})]),
                       sleep=sl, wall_clock=lambda: 9.0 + len(sl.calls), analyzer=FixtureAnalyzer())
    k = report["knowledge"]
    assert k["sources_discovered"] == 2 and k["videos_analyzed"] == 1 and k["analyses_that_produced_knowledge"] == 1 and k["fixture_analyses"] == 1
    assert k["drafts_awaiting_review"] == 1 and k["approved_valid_knowledge"] == 0 and k["this_run"]["drafts_created"] == 1 and k["queue"]["awaiting_media"] == 2
    md = (tmp_path / "state" / "reports" / "2026-10-09-live.md").read_text()
    assert "Knowledge pipeline" in md and "fixture analyses: 1" in md
    assert report["api_calls"]["openai"] == 0 and report["cost_usd"]["openai"]["reserved"] == 0


def test_the_page_escapes_everything_has_no_scripts_or_links_out_and_labels_fixtures(tmp_path):
    s = new_store(tmp_path)
    hostile = [{**NOTES[1], "text": "<script>alert(1)</script> Dribble forward"}] + NOTES[2:]
    register(s, make_file(tmp_path / "m"))
    pass_(s, FixtureAnalyzer(notes=hostile))
    page = dashboard.build(s, NOW)
    assert "<script" not in page.lower() and "&lt;script&gt;" in page and "FIXTURE" in page and "http://" not in page and "https://" not in page and "src=" not in page
    for section in ("Queue", "Library", "Drafts waiting", "Approved knowledge", "Recommendations", "Recorded session results"):
        assert section in page
    s.close()


def test_the_pipeline_is_off_by_default_and_the_only_analyzer_a_config_can_name_is_none():
    from learning_engine.config import ConfigError, parse_config
    from tests.le_helpers import config_dict
    assert config().pipeline.enabled is False and config().pipeline.analyzer == "none"
    with pytest.raises(ConfigError, match="analyzer"):
        parse_config(config_dict(pipeline={"analyzer": "openai"}))
    with pytest.raises(ConfigError):
        parse_config(config_dict(pipeline={"max_items_per_run": 0}))
    assert hashlib.sha256(b"x")                                                                # (keeps the import used)


def test_a_version_4_database_upgrades_to_5_keeping_every_item_and_version(tmp_path):
    s = new_store(tmp_path)
    db = s.db
    db.execute("PRAGMA foreign_keys=OFF")
    for t in ("work_queue", "recommendation_events", "recommendations", "outcomes", "knowledge_notes", "analyses"):
        db.execute(f"DROP TABLE {t}")
    db.execute("DROP TABLE item_versions"); db.execute("DROP TABLE review_items"); db.execute("DROP TABLE media_files")
    db.execute("""CREATE TABLE review_items (item_id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT')),
        source_video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL, stage TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending',
        current_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
        source_state TEXT NOT NULL DEFAULT 'present', approval_state TEXT NOT NULL DEFAULT 'current', source_issue TEXT, source_issue_at TEXT, source_removed_at TEXT)""")
    db.execute("""CREATE TABLE item_versions (item_id TEXT NOT NULL REFERENCES review_items(item_id), version INTEGER NOT NULL, content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
        author_kind TEXT NOT NULL CHECK (author_kind IN ('NONE','AI_DRAFT','COACH')), author_ref TEXT, created_at TEXT NOT NULL, PRIMARY KEY (item_id, version))""")
    db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) VALUES ('AI-x','AI_GENERAL_DRAFT',NULL,'ai_draft','pending',1,'t','t')")
    db.execute("INSERT INTO item_versions VALUES ('AI-x',1,'{}','h','AI_DRAFT','openai:m','t')")
    db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    db.commit()
    s.close()
    up = ResearchStore(tmp_path / "state")
    assert up.schema_version() == 5 and q(up, "select item_id, approval_state, media_id from review_items") == [{"item_id": "AI-x", "approval_state": "current", "media_id": None}]
    assert q(up, "select author_kind from item_versions") == [{"author_kind": "AI_DRAFT"}]
    up.db.execute("INSERT INTO media_files(media_id,sha256,local_path,size_bytes,rights_basis,rights_grantor,rights_recorded_by,rights_recorded_at,minors_visible,registered_at) "
                  "VALUES ('MED-1','h','/p',1,'OWN_RECORDING','me','me','t','no','t')")
    assert up.db.execute("pragma foreign_key_check").fetchall() == []
    up.close()
