"""Local demonstration of the whole knowledge cycle on FIXTURE data. Not a live search, not a real video analysis.

    python tests/demo_knowledge_cycle.py /absolute/path/to/a/NEW/empty/scratch/directory

A fake transport answers instead of YouTube; the "video" is a few made-up bytes; the analyzer is a scripted test double that understands nothing.
All of it is labelled FIXTURE. What this shows is the plumbing: queue -> media with a rights record -> analysis notes with video times -> pending
draft -> coach review -> approved knowledge -> explained recommendation -> recorded result -> page. It says nothing about training quality."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning_engine.cli import main                                   # noqa: E402
from learning_engine.daily import run_daily                            # noqa: E402
from learning_engine.pipeline import run_pass                          # noqa: E402
from learning_engine.store import ResearchStore, utc_now               # noqa: E402
from tests.ke_helpers import FixtureAnalyzer, PIPE, make_file         # noqa: E402
from tests.le_helpers import FakeTransport, Sleeps, config, config_dict, ok, search_item, video_item   # noqa: E402

CONFIRMED = {"title": "[FIXTURE] Marker weave", "category": "dribbling", "steps": ["Dribble forward with the ball close to the feet", "Weave through the markers, changing direction at each one"],
             "equipment": ["markers"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Keep the area clear of other players.",
             "objective": "close control while changing direction", "skill_tags": ["close_control"],
             "measure": {"name": "clean_runs_30s", "unit": "count", "direction": "higher_is_better", "how": "count the runs through all markers without losing the ball in 30 seconds"}}


def main_demo(work: Path) -> None:
    work = work.resolve()
    if work.exists() and any(work.iterdir()):
        sys.exit("give a NEW or empty scratch directory")
    if ROOT in work.parents or work == ROOT:
        sys.exit("the scratch directory must be outside the repository")
    work.mkdir(parents=True, exist_ok=True)
    state, cfg_file = work / "state", work / "demo-config.json"
    cfg_file.write_text(json.dumps(config_dict(mode="offline", network_enabled=False, pipeline={"enabled": True}, youtube={"enabled": False}), indent=2))
    base = ["--config", str(cfg_file), "--state", str(state)]

    def cli(*args, show=True):
        print("\n$ python -m learning_engine " + " ".join(str(a) for a in args))
        main(base + [str(a) for a in args])

    print("==== FIXTURE DATA - NOT A LIVE SEARCH, NOT A REAL VIDEO ANALYSIS (بيانات تجريبية) ====")
    t = FakeTransport(search=[ok({"items": [search_item(1, title="[DEMO] Cone weave dribbling"), search_item(2, title="[DEMO] Close control basics")]})],
                      videos=[ok({"items": [video_item(1, title="[DEMO] Cone weave dribbling"), video_item(2, title="[DEMO] Close control basics")]})])
    report = run_daily(config(mode="live", network_enabled=True, youtube={"max_pages_per_channel": 1}, pipeline={"enabled": True}), state, env={"KEMET_LE_YOUTUBE_API_KEY": "fixture"},
                       transport=t, sleep=Sleeps(), fixture=True, analyzer=None)
    print("fixture discovery:", {k: report["knowledge"][k] for k in ("sources_discovered", "queue", "videos_analyzed", "skipped_or_waiting_reasons")})
    cli("queue")
    clip = make_file(work / "media", "fixture-clip.mp4")
    cli("media-register", clip, "--rights-basis", "OWN_RECORDING", "--grantor", "FIXTURE: made-up owner", "--minors", "no", "--recorded-by", "demo-reviewer", "--name", "fixture clip")
    cli("pipeline-run")
    s = ResearchStore(state)
    run_pass(s, PIPE, FixtureAnalyzer(), utc_now())
    s.close()
    cli("drafts")
    store = ResearchStore(state)
    item = store.db.execute("select item_id from review_items where analysis_id is not null").fetchone()["item_id"]
    store.close()
    cli("draft", item)
    content = work / "confirmed.json"
    content.write_text(json.dumps(CONFIRMED))
    cli("save-content", item, "--file", content, "--reviewer-id", "demo-reviewer")
    cli("decide", item, "--version", 2, "--decision", "approved", "--reviewer-id", "demo-reviewer", "--reasons", "FIXTURE: demonstration of the review path, not a coach decision.")
    cli("export-approved")
    ctx = work / "player-context.json"
    ctx.write_text(json.dumps({"player_ref": "FIXTURE-P1", "age": 10, "fixture": True, "goal_skill": "close_control", "minutes_available": 15, "equipment_available": ["markers", "ball"]}))
    cli("recommend", "--context", ctx, "--requested-by", "demo-reviewer")
    store = ResearchStore(state)
    rec_id = store.db.execute("select rec_id from recommendations").fetchone()["rec_id"]
    store.close()
    cli("rec-decide", rec_id, "--decision", "approve", "--reviewer-id", "demo-reviewer", "--reasons", "FIXTURE: demonstration only")
    cli("outcome-add", "--player", "FIXTURE-P1", "--date", "2026-10-09", "--executed", "yes", "--recorded-by", "demo-reviewer", "--rec-id", rec_id, "--measure", "clean_runs_30s",
        "--unit", "count", "--before", "5", "--after", "7", "--method-before", "30 s count by the coach", "--method-after", "30 s count by the coach", "--conditions", "indoor, FIXTURE", "--fixture")
    cli("outcomes")
    page = work / "coach-view.html"
    cli("dashboard", "--out", page)
    print("\n---- files ----")
    print(sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file()))
    print("\n==== FIXTURE DATA - nothing here entered the player library, any player programme or any training dataset ====")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main_demo(Path(sys.argv[1]))
