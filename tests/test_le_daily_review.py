"""Learning engine: the daily run (idempotency, resume, reports), coach review, retention, safety boundaries, scheduler templates."""
import json
import os
import re
import shutil
import stat
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from learning_engine import retention
from learning_engine.config import load_config
from learning_engine.daily import render_markdown, run_daily
from learning_engine.review import AUTH_BACKEND, AUTH_CLI, ReviewError, ReviewService, Reviewer, content_hash, content_problems
from learning_engine.store import ResearchStore
from tests.le_helpers import CH1, FAKE_KEY, FakeTransport, NOW, NoNetwork, Sleeps, config, err, iid, ok, search_item, vid, video_item

ROOT = Path(__file__).resolve().parent.parent
ENV = {"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}
GOOD = {"title": "Cone weave dribble", "category": "dribbling", "steps": ["Set five cones in a line, one metre apart.", "Dribble through using the inside of each foot.", "Return at a jog and repeat."],
        "equipment": ["5 cones", "1 ball"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Flat surface, no studs on hard ground."}
COACH = Reviewer("coach-ali", AUTH_BACKEND, "coach")


def go(tmp_path, transport, cfg=None, **kw):
    sl = Sleeps()
    return run_daily(cfg or config(youtube={"max_pages_per_channel": 1}), tmp_path / "state", env=ENV, now=kw.pop("now", NOW), transport=transport, sleep=sl,
                     wall_clock=lambda: 10_000.0 + len(sl.calls), **kw)


def happy(tmp_path, **kw):
    t = FakeTransport(search=[ok({"items": [search_item(1), search_item(2)]})], videos=[ok({"items": [video_item(1), video_item(2)]})])
    return go(tmp_path, t, **kw), t


def open_store(tmp_path):
    return ResearchStore(tmp_path / "state")


# ---- the daily run -------------------------------------------------------------------------------------------------------------
def test_the_report_has_everything_asked_for_and_is_written_as_json_and_markdown(tmp_path):
    report, _ = happy(tmp_path)
    for key in ("date", "timezone", "mode", "live", "status", "sources", "review", "stages", "services", "errors", "notes", "limits", "api_calls", "tokens", "cost_usd", "quota", "retention"):
        assert key in report
    assert report["timezone"] == "America/Detroit" and report["date"] == "2026-10-08" and report["mode"] == "live" and report["live"] is True
    files = sorted(p.name for p in (tmp_path / "state" / "reports").iterdir())
    assert files == ["2026-10-08-live.json", "2026-10-08-live.md"]
    for p in (tmp_path / "state" / "reports").iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    md = (tmp_path / "state" / "reports" / "2026-10-08-live.md").read_text()
    assert "status: **SUCCESS**" in md and "America/Detroit" in md and "pending 2" in md and "FIXTURE" not in md
    assert json.loads((tmp_path / "state" / "reports" / "2026-10-08-live.json").read_text())["status"] == "SUCCESS"


def test_the_report_numbers_are_the_real_counts_not_illustrations(tmp_path):
    report, t = happy(tmp_path)
    assert report["api_calls"] == {"youtube_search": 1, "youtube_other": 1, "openai": 0, "anthropic": 0} and len(t.requests) == 2
    assert report["sources"]["new"] == 2 and report["review"] == {"pending": 2, "approved": 0, "rejected": 0, "source_review_required": 0, "source_removed_after_retention": 0}
    assert report["tokens"]["openai"] == {"reserved": 0, "actual_reported": 0, "uncertain_calls": 0}
    assert report["quota"]["quota_day_pacific"] == "2026-10-08"
    assert report["services"]["openai"]["reasons"] == ["PROVIDER_NOT_CONFIGURED"]


def test_a_fixture_run_is_labelled_and_never_claims_to_be_live(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    report = run_daily(config(mode="offline", network_enabled=False, youtube={"max_pages_per_channel": 1}), tmp_path / "state", env={}, now=NOW, transport=t, fixture=True,
                       sleep=Sleeps(), wall_clock=lambda: 1.0)
    assert report["mode"] == "fixture" and report["live"] is False and report["fixture_data_not_live_results"] is True
    assert "FIXTURE DATA, not live results" in render_markdown(report)


def test_a_successful_day_is_never_repeated(tmp_path):
    first, _ = happy(tmp_path)
    again = go(tmp_path, NoNetwork())
    assert again["status"] == "SUCCESS" and again["api_calls"] == first["api_calls"] and any("already finished" in n for n in again["notes"])
    s = open_store(tmp_path)
    assert s.db.execute("select count(*) from sources").fetchone()[0] == 2 and s.db.execute("select count(*) from runs").fetchone()[0] == 1
    s.close()


def test_a_failed_day_resumes_from_its_checkpoint_without_repeating_or_double_spending(tmp_path):
    cfg = config(youtube={"max_pages_per_channel": 1, "max_retries": 0, "channels": [
        {"channel_id": CH1, "label": "Channel One", "basis_for_allowing": "synthetic test channel", "reviewed_by": "tester", "reviewed_on": "2026-10-01"},
        {"channel_id": "UC" + "b" * 22, "label": "Channel Two", "basis_for_allowing": "synthetic test channel", "reviewed_by": "tester", "reviewed_on": "2026-10-01"}]})
    t1 = FakeTransport(search=[ok({"items": [search_item(1)]}), err(503)], videos=[ok({"items": [video_item(1)]})])
    first = go(tmp_path, t1, cfg)
    assert first["status"] == "PARTIAL" and first["services"]["youtube"]["channels_done"] == 1 and first["attempt"] == 1
    t2 = FakeTransport(search=[ok({"items": [search_item(3, channel="UC" + "b" * 22)]})], videos=[ok({"items": [video_item(3, channel="UC" + "b" * 22)]})])
    second = go(tmp_path, t2, cfg)
    assert second["status"] == "SUCCESS" and second["attempt"] == 2 and second["resumed_from_checkpoint"] is True
    assert [r.url.split("channelId=")[1].split("&")[0] for r in t2.requests if "/search?" in r.url] == ["UC" + "b" * 22]      # channel one was not searched again
    assert second["api_calls"]["youtube_search"] == 3                                                                                # 2 earlier attempts + 1 now, all counted
    assert second["sources"]["new"] >= 1


def test_a_day_where_nothing_could_be_done_is_not_a_success(tmp_path):
    no_channels = config(youtube={"channels": []})
    report = go(tmp_path / "a", NoNetwork(), no_channels)
    assert report["status"] == "NOT_CONFIGURED" and "NO_CHANNEL_ALLOWLIST" in report["services"]["youtube"]["reasons"]
    report = run_daily(config(), tmp_path / "b" / "state", env={}, now=NOW, transport=NoNetwork(), sleep=Sleeps())
    assert report["status"] == "NOT_CONFIGURED" and "YOUTUBE_API_KEY_NOT_SET" in report["services"]["youtube"]["reasons"]


def test_offline_default_does_no_discovery_and_says_so(tmp_path):
    report = run_daily(load_config(ROOT / "learning_engine/config.example.json"), tmp_path / "state", env=ENV, now=NOW, transport=NoNetwork())
    assert report["status"] == "OFFLINE_NO_DISCOVERY" and report["live"] is False and report["sources"]["discovered"] == 0
    assert all(s["status"] == "skipped" for s in report["services"].values())


def test_the_day_and_time_zone_follow_America_Detroit_not_utc(tmp_path):
    late = NOW.replace(hour=2, day=9)                      # 02:00 UTC on the 9th is still the evening of the 8th in Detroit
    report = run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=late)
    assert report["date"] == "2026-10-08" and report["timezone"] == "America/Detroit"


def test_two_runs_cannot_overlap(tmp_path):
    from learning_engine.store import AlreadyRunning
    holder = ResearchStore(tmp_path / "state")
    holder.acquire_run_lock()
    with pytest.raises(AlreadyRunning):
        run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW)
    holder.close()


def test_a_symlinked_report_file_is_refused(tmp_path):
    state = tmp_path / "state"
    ResearchStore(state).close()
    (state / "reports").mkdir(mode=0o700)
    (state / "reports" / "2026-10-08-offline.json").symlink_to(tmp_path / "victim.txt")
    with pytest.raises(OSError):
        run_daily(config(mode="offline", network_enabled=False), state, env={}, now=NOW)
    assert not (tmp_path / "victim.txt").exists()


# ---- retention -----------------------------------------------------------------------------------------------------------------------
def test_data_older_than_the_window_is_deleted_or_cleared_never_kept_silently(tmp_path):
    report, _ = happy(tmp_path)
    item = iid(tmp_path / "state", 1)
    svc = ReviewService(open_store(tmp_path))
    svc.save_content(item, GOOD, COACH)
    svc.decide(item, 2, "approved", COACH, "Inspected the source and wrote the steps myself.", [f"https://www.youtube.com/watch?v={vid(1)}"])
    svc.store.close()
    later = NOW + timedelta(days=31)
    report2 = run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=later)
    assert report2["retention"]["sources_deleted"] == 2 and report2["retention"]["items_kept_with_source_removed"] == 1 and report2["retention"]["items_deleted"] == 1
    s = open_store(tmp_path)
    assert s.db.execute("select count(*) from sources").fetchone()[0] == 0                          # no video id, channel id, link or title survives
    kept = ReviewService(s).get(item)
    assert kept["review_status"] == "approved" and kept["content"]["title"] == "Cone weave dribble"                  # the coach's own content stays
    assert kept["source"] is None and kept["source_state"] == "removed" and kept["source_removed_at"] and "removed after the retention window" in kept["note"]
    assert s.verify_audit()
    s.close()


def test_data_inside_the_window_is_kept_and_the_boundary_is_exact(tmp_path):
    happy(tmp_path)
    for days, expected in ((29, 2), (30, 2), (31, 0)):
        d = tmp_path / f"copy{days}"
        shutil.copytree(tmp_path / "state", d)
        report = run_daily(config(mode="offline", network_enabled=False), d, env={}, now=NOW + timedelta(days=days, seconds=1 if days == 31 else 0))
        s = ResearchStore(d)
        assert s.db.execute("select count(*) from sources").fetchone()[0] == expected, days
        s.close()


def test_sources_nearing_the_limit_are_refreshed_in_a_live_run(tmp_path):
    happy(tmp_path)
    store = open_store(tmp_path)
    cfg = config(youtube={"max_pages_per_channel": 1})
    assert retention.due_for_refresh(store, cfg, NOW + timedelta(days=10)) == []
    assert retention.due_for_refresh(store, cfg, NOW + timedelta(days=28)) == [vid(1), vid(2)]
    store.close()
    t = FakeTransport(search=[ok({"items": []})], videos=[ok({"items": [video_item(1)]})])
    report = go(tmp_path, t, now=NOW + timedelta(days=28))
    s = open_store(tmp_path)
    got = {r["video_id"]: r["availability"] for r in s.db.execute("select video_id, availability from sources")}
    assert got == {vid(1): "available", vid(2): "unavailable"}                                  # re-verified; the missing one is flagged, not trusted
    s.close()


def test_old_reports_are_removed_after_the_report_window(tmp_path):
    happy(tmp_path)
    for f in (tmp_path / "state" / "reports").iterdir():
        os.utime(f, (NOW.timestamp() - 200 * 86400,) * 2)
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW)
    assert sorted(p.name for p in (tmp_path / "state" / "reports").iterdir()) == ["2026-10-08-offline.json", "2026-10-08-offline.md"]


# ---- coach review ---------------------------------------------------------------------------------------------------------------------
def test_pending_to_approved_records_who_when_why_which_version_and_which_source(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path), clock=lambda: NOW)
    item = iid(tmp_path / "state", 1)
    assert svc.get(item)["review_status"] == "pending" and svc.get(item)["stage"] == "metadata_only"
    svc.save_content(item, GOOD, COACH)
    out = svc.decide(item, 2, "approved", COACH, "Watched the source; the steps are my own wording and safe.", [f"https://www.youtube.com/watch?v={vid(1)}"])
    assert out["review_status"] == "approved" and out["stage"] == "coach_reviewed" and out["published"] is False
    h = svc.history(item)[0]
    assert h["reviewer_id"] == "coach-ali" and h["auth_mode"] == AUTH_BACKEND and h["version"] == 2 and h["at"] == "2026-10-08T15:00:00Z"
    assert h["content_hash"] == out["content_hash"] == content_hash(GOOD, [f"https://www.youtube.com/watch?v={vid(1)}"])
    assert h["sources_json"] == [f"https://www.youtube.com/watch?v={vid(1)}"] and "own wording" in h["reasons"]
    assert svc.store.verify_audit()
    svc.store.close()


def test_nothing_is_approved_automatically_and_drill_steps_are_coach_content_not_inferred(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path))
    assert [i["review_status"] for i in svc.list_items()] == ["pending", "pending"]
    with pytest.raises(ReviewError, match="not complete"):                                      # no content was ever written
        svc.decide(iid(tmp_path / "state", 1), 1, "approved", COACH, "Looks fine to me, approving it.", [f"https://www.youtube.com/watch?v={vid(1)}"])
    assert svc.get(iid(tmp_path / "state", 1))["content"] == {} and svc.get(iid(tmp_path / "state", 1))["content_problems"]
    assert svc.approved_content() == []
    svc.store.close()


@pytest.mark.parametrize("change,expected", [
    ({"steps": []}, "STEPS_REQUIRED"), ({"steps": ["x"]}, "STEPS_REQUIRED"), ({"equipment": "cones"}, "EQUIPMENT_LIST_REQUIRED"), ({"duration_minutes": 0}, "DURATION_MINUTES"),
    ({"duration_minutes": True}, "DURATION_MINUTES"), ({"age_min": 12, "age_max": 8}, "AGE_RANGE_REQUIRED"), ({"age_min": 2}, "AGE_RANGE_REQUIRED"), ({"safety_notes": ""}, "SAFETY_NOTES_REQUIRED"),
    ({"category": "passing"}, "CATEGORY_MUST_BE_DRIBBLING"), ({"title": "ab"}, "TITLE_REQUIRED"), ({"extra": 1}, "UNKNOWN_FIELDS")])
def test_incomplete_or_odd_content_cannot_be_approved(tmp_path, change, expected):
    assert any(p.startswith(expected) for p in content_problems({**GOOD, **change}))
    assert content_problems(GOOD) == [] and content_problems("nope") == ["CONTENT_NOT_AN_OBJECT"]


def test_approving_needs_reasons_the_current_version_the_source_link_and_a_coach_or_admin(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path))
    item, link = iid(tmp_path / "state", 1), f"https://www.youtube.com/watch?v={vid(1)}"
    svc.save_content(item, GOOD, COACH)
    with pytest.raises(ReviewError, match="written reasons"):
        svc.decide(item, 2, "approved", COACH, "ok", [link])
    with pytest.raises(ReviewError, match="current version"):
        svc.decide(item, 1, "approved", COACH, "This is the old version, approving it.", [link])
    with pytest.raises(ReviewError, match="record the source you inspected"):
        svc.decide(item, 2, "approved", COACH, "Approving without recording the source I used.", [])
    with pytest.raises(ReviewError, match="record the source you inspected"):
        svc.decide(item, 2, "approved", COACH, "Approving with a different link than the source.", ["https://www.youtube.com/watch?v=other"])
    for who, mode in (("", AUTH_CLI), ("x", AUTH_CLI)):
        with pytest.raises(ReviewError):
            Reviewer(who, mode)
    with pytest.raises(ReviewError):
        Reviewer("player-1", AUTH_BACKEND, "guardian")
    with pytest.raises(ReviewError):
        Reviewer("coach-ali", "self-declared")
    assert svc.get(item)["review_status"] == "pending"
    svc.store.close()


def test_a_rejection_needs_reasons_but_no_complete_content(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path))
    out = svc.decide(iid(tmp_path / "state", 2), 1, "rejected", COACH, "Not a dribbling drill, wrong topic for us.", [])
    assert out["review_status"] == "rejected" and out["stage"] == "coach_reviewed"
    with pytest.raises(ReviewError, match="only a pending item"):
        svc.decide(iid(tmp_path / "state", 2), 1, "approved", COACH, "Changing my mind without new content.", [])
    svc.store.close()


def test_any_change_to_approved_content_sends_it_back_to_review_with_a_new_version(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path), clock=lambda: NOW)
    item, link = iid(tmp_path / "state", 1), f"https://www.youtube.com/watch?v={vid(1)}"
    svc.save_content(item, GOOD, COACH)
    svc.decide(item, 2, "approved", COACH, "Source inspected, content written and checked.", [link])
    assert len(svc.approved_content()) == 1
    same = svc.save_content(item, GOOD, COACH)
    assert same["review_status"] == "approved" and same["version"] == 2                         # identical content: nothing changes
    changed = svc.save_content(item, {**GOOD, "age_min": 6}, COACH)
    assert changed["review_status"] == "pending" and changed["version"] == 3 and svc.approved_content() == []
    assert [h["version"] for h in svc.history(item)] == [2]                                      # the earlier decision stays on record
    events = [r["event"] for r in svc.store.db.execute("select event from audit order by n")]
    assert events.count("CONTENT_SAVED") == 2 and "REVIEW_APPROVED" in events
    svc.store.close()


def test_sources_that_are_unavailable_or_excluded_cannot_be_approved(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1), search_item(2)]})], videos=[ok({"items": [video_item(1, kids=True)]})])
    go(tmp_path, t)
    svc = ReviewService(open_store(tmp_path))
    for n in (1, 2):                                                                             # 1: made for kids -> excluded, 2: not returned -> unavailable
        item, link = iid(tmp_path / "state", n), f"https://www.youtube.com/watch?v={vid(n)}"
        svc.save_content(item, GOOD, COACH)
        with pytest.raises(ReviewError, match="not confirmed available"):
            svc.decide(item, 2, "approved", COACH, "Trying to approve an unusable source.", [link])
    svc.store.close()


def test_nothing_can_publish_to_players_or_change_programs(tmp_path):
    happy(tmp_path)
    svc = ReviewService(open_store(tmp_path))
    assert not [m for m in dir(svc) if "publish" in m.lower() or "assign" in m.lower() or "push" in m.lower()]
    with pytest.raises(Exception):
        svc.store.db.execute("UPDATE review_items SET published=1")
    svc.store.close()


def test_the_cli_marks_reviewer_names_as_unauthenticated_and_works_end_to_end(tmp_path, capsys):
    from learning_engine.cli import main, NOTICE
    happy(tmp_path)
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text((ROOT / "learning_engine/config.example.json").read_text())
    base = ["--config", str(cfg_path), "--state", str(tmp_path / "state")]
    content = tmp_path / "content.json"; content.write_text(json.dumps(GOOD))
    assert main(base + ["list", "--status", "pending"]) == 0
    assert main(base + ["save-content", iid(tmp_path / "state", 1), "--file", str(content), "--reviewer-id", "typed-name"]) == 0
    assert NOTICE.split(".")[0] in capsys.readouterr().err
    assert main(base + ["decide", iid(tmp_path / "state", 1), "--version", "2", "--decision", "approved", "--reviewer-id", "typed-name",
                        "--reasons", "Source inspected and content written by me.", "--source-checked", f"https://www.youtube.com/watch?v={vid(1)}"]) == 0
    s = open_store(tmp_path)
    assert ReviewService(s).history(iid(tmp_path / "state", 1))[0]["auth_mode"] == "local_cli_unauthenticated"
    s.close()
    assert main(base + ["status"]) == 0
    assert main(["--config", str(tmp_path / "missing.json"), "--state", str(tmp_path / "x"), "validate-config"]) == 2


# ---- boundaries ---------------------------------------------------------------------------------------------------------------------------
def test_the_engine_has_no_training_scraping_download_or_application_imports():
    import ast
    banned_imports = {"torch", "numpy", "sklearn", "ultralytics", "mediapipe", "cv2", "yt_dlp", "youtube_dl", "pytube", "youtube_transcript_api", "bs4", "selenium",
                      "playwright", "scrapy", "app", "main", "requests", "httpx", "dotenv"}
    for path in (ROOT / "learning_engine").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else []
            assert not (set(names) & banned_imports), (path.name, names)
    text = "".join(p.read_text() for p in (ROOT / "learning_engine").glob("*.py")).lower()
    for word in ("captions.download", "yt-dlp", "train_candidate", "load_dotenv", "subprocess", "os.system", "eval(", "exec("):
        assert word not in text, word
    assert "transcript" not in re.sub(r'"""[\s\S]*?"""', "", text)                               # only ever mentioned in docstrings that forbid it


def test_the_ml_package_never_imports_the_research_engine_so_its_data_cannot_reach_a_training_dataset():
    for path in (ROOT / "app" / "ml1").glob("*.py"):
        assert "learning_engine" not in path.read_text(), path.name
    assert not list((ROOT / "app" / "ml1").glob("*learning_engine*"))


def test_the_engine_reads_no_dotenv_and_only_named_environment_variables():
    text = "".join(p.read_text() for p in (ROOT / "learning_engine").glob("*.py"))
    assert "load_dotenv" not in text and not re.search(r"open\([^)]*\.env", text) and "environ[" not in text
    assert text.count("os.environ") == 1                                                          # only the default for an injected environment


# ---- scheduler templates -----------------------------------------------------------------------------------------------------------------------------
def test_the_scheduler_templates_exist_are_not_active_and_contain_no_secrets():
    base = ROOT / "deploy" / "learning_engine"
    assert sorted(p.name for p in base.iterdir()) == ["README.md", "com.kemetfc.learning-engine.plist.template", "kemet-learning-engine.service.template",
                                                      "kemet-learning-engine.timer.template", "run_daily.sh"]
    for p in base.iterdir():
        text = p.read_text()
        assert not re.search(r"AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|API_KEY\s*=\s*\S+", text), p.name
        assert os.access(p, os.X_OK) == (p.name == "run_daily.sh")


def test_the_timer_and_plist_use_the_detroit_schedule_and_are_disabled_by_default():
    base = ROOT / "deploy" / "learning_engine"
    timer = (base / "kemet-learning-engine.timer.template").read_text()
    assert "OnCalendar=*-*-* 06:30:00 America/Detroit" in timer and "Persistent=true" in timer
    plist = (base / "com.kemetfc.learning-engine.plist.template").read_text()
    assert "<key>Disabled</key>" in plist and "<true/>" in plist and "<key>RunAtLoad</key>" in plist and "<false/>" in plist and "America/Detroit" in plist
    service = (base / "kemet-learning-engine.service.template").read_text()
    for hardening in ("NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=strict", "UMask=0077"):
        assert hardening in service


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil (macOS) is not available")
def test_the_launchd_template_is_valid_xml_once_its_placeholders_are_filled(tmp_path):
    text = (ROOT / "deploy/learning_engine/com.kemetfc.learning-engine.plist.template").read_text()
    filled = re.sub(r"@[A-Z_]+@", "/example/path", text)
    f = tmp_path / "x.plist"; f.write_text(filled)
    out = subprocess.run(["plutil", "-lint", str(f)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_the_wrapper_script_is_valid_bash_validates_first_and_defaults_to_offline(tmp_path):
    script = ROOT / "deploy/learning_engine/run_daily.sh"
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    text = script.read_text()
    assert text.index("validate-config") < text.index('"$KEMET_LE_STATE" run') and "set -euo pipefail" in text and "TZ=America/Detroit" in text and "umask 077" in text
    cfg = tmp_path / "bad.json"; cfg.write_text("{not json")
    env = {**os.environ, "KEMET_LE_CONFIG": str(cfg), "KEMET_LE_STATE": str(tmp_path / "state"), "KEMET_LE_REPO": str(ROOT)}
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode != 0 and not (tmp_path / "state" / "reports").exists()                 # a bad config stops the run before anything happens
    good = tmp_path / "good.json"; good.write_text((ROOT / "learning_engine/config.example.json").read_text())
    env["KEMET_LE_CONFIG"] = str(good)
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "OFFLINE_NO_DISCOVERY" in "".join(p.read_text() for p in (tmp_path / "state" / "reports").glob("*.json"))
