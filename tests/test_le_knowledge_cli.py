"""The command line for the whole local cycle, on fixtures. The analyzer step is the test double (no analyzer can be chosen from the CLI)."""
import json

from learning_engine.cli import main
from learning_engine.store import ResearchStore
from tests.ke_helpers import FixtureAnalyzer, make_file, pass_
from tests.le_helpers import NOW, config_dict

CONFIRMED = {"title": "[FIXTURE] Marker weave", "category": "dribbling", "steps": ["Dribble forward with the ball close to the feet", "Weave through the markers, changing direction at each one"],
             "equipment": ["markers"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Keep the area clear of other players.",
             "objective": "close control while changing direction", "skill_tags": ["close_control"],
             "measure": {"name": "clean_runs_30s", "unit": "count", "direction": "higher_is_better", "how": "count the runs through all markers without losing the ball in 30 seconds"}}


def test_the_whole_local_cycle_through_the_command_line(tmp_path, capsys):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(config_dict(pipeline={"enabled": True}, youtube={"enabled": False})))
    state = tmp_path / "state"
    base = ["--config", str(cfg), "--state", str(state)]

    def run(*args):
        capsys.readouterr()
        code = main(base + [str(a) for a in args])
        captured = capsys.readouterr()
        assert code == 0, captured.err
        return captured

    def run_json(*args):
        return json.loads(run(*args).out)

    clip = make_file(tmp_path / "media", "clip.mp4")
    reg = run_json("media-register", clip, "--rights-basis", "OWN_RECORDING", "--grantor", "the club (test fixture)", "--minors", "no", "--recorded-by", "test-reviewer", "--name", "fixture clip")
    assert reg["duplicate"] is False and run_json("media-register", clip, "--rights-basis", "OWN_RECORDING", "--grantor", "the club (test fixture)", "--minors", "no", "--recorded-by", "x1")["duplicate"] is True
    q = run_json("queue")
    assert q["counts"]["ready"] == 1
    blocked = run_json("pipeline-run")
    assert blocked["result"]["blocked"] == ["ANALYZER_NOT_CONFIGURED"] and "no analyzer is built in" in blocked["note"]

    s = ResearchStore(state)
    pass_(s, FixtureAnalyzer(), now=NOW)                                                    # the analyzer is a test double plugged in by code
    s.close()
    drafts = run("drafts").out
    assert "FIXTURE" in drafts and "timed notes" in drafts
    item = drafts.split("\t")[0]
    view = run_json("draft", item)
    assert view["analysis"]["is_fixture"] == 1 and len(view["extracted_notes"]) == 5 and view["coach_decisions"] == []
    assert run_json("export-approved") == []
    f = tmp_path / "content.json"
    f.write_text(json.dumps(CONFIRMED))
    saved = run_json("save-content", item, "--file", f, "--reviewer-id", "test-reviewer")
    run_json("decide", item, "--version", saved["version"], "--decision", "approved", "--reviewer-id", "test-reviewer", "--reasons", "FIXTURE: test of the review path, not a coach decision.")
    exported = run_json("export-approved")
    assert [e["item_id"] for e in exported] == [item] and exported[0]["review_basis"] == "LOCAL_MEDIA_ANALYSIS"
    lib = run_json("library")
    assert len(lib) == 1 and lib[0]["rights"]["training_allowed"] is False and lib[0]["items"][0]["valid_for_use"] is True

    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"player_ref": "FIXTURE-P1", "age": 10, "fixture": True, "goal_skill": "close_control", "minutes_available": 15, "equipment_available": ["markers", "ball"]}))
    rec = run_json("recommend", "--context", ctx, "--requested-by", "test-reviewer")[0]
    assert rec["status"] == "awaiting_coach_review" and rec["item_id"] == item and rec["measure"]["name"] == "clean_runs_30s" and rec["fixture"] is True
    decided = run_json("rec-decide", rec["rec_id"], "--decision", "approve", "--reviewer-id", "test-reviewer", "--reasons", "FIXTURE: test of the path")
    assert decided["status"] == "coach_approved_for_use"
    run_json("outcome-add", "--player", "FIXTURE-P1", "--date", "2026-10-09", "--executed", "yes", "--recorded-by", "test-reviewer", "--rec-id", rec["rec_id"], "--measure", "clean_runs_30s",
             "--unit", "count", "--before", "5", "--after", "7", "--method-before", "30s count", "--method-after", "30s count", "--conditions", "indoor", "--fixture")
    summary = run_json("outcomes")[0]
    assert summary["sessions"][0]["recorded_change"] == 2 and summary["fixture"] is True and "does not show" in summary["note"]
    page = tmp_path / "view.html"
    out = run("dashboard", "--out", page).out
    assert "written" in out and oct(page.stat().st_mode & 0o777) == "0o600"
    html = page.read_text()
    assert "FIXTURE" in html and item in html and "<script" not in html.lower()
    assert "not authentication" in run("recommend", "--context", ctx, "--requested-by", "test-reviewer").err
    assert main(base + ["media-register", str(tmp_path / "nothing.mp4"), "--rights-basis", "OWN_RECORDING", "--grantor", "someone", "--minors", "no", "--recorded-by", "tt"]) == 1
