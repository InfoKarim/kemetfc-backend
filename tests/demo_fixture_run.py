"""Local end-to-end demonstration of the Learning Engine on FIXTURE DATA. Not a live search.

    python tests/demo_fixture_run.py /absolute/path/to/a/NEW/empty/scratch/directory

Everything here is made up: a fake transport answers instead of YouTube, the channel and videos are labelled [DEMO], no network is
opened, no key is read, nothing is written outside the directory you give. The discovery step uses the engine's own `fixture=True`
mode (its report says so); review and export go through the real command line. Nothing reaches the player library or any training dataset.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning_engine.cli import main                       # noqa: E402
from learning_engine.daily import run_daily                # noqa: E402
from tests.le_helpers import FakeTransport, Sleeps, config, config_dict, ok, search_item, video_item   # noqa: E402

DRILL = {"title": "[DEMO] Cone weave dribble", "category": "dribbling",
         "steps": ["[DEMO] Set five cones in a line, one metre apart.", "[DEMO] Dribble through using the inside of each foot."],
         "equipment": ["5 cones", "1 ball"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "[DEMO] Flat surface, no studs on hard ground."}


def cli(base, *args):
    print("\n$ python -m learning_engine " + " ".join(str(a) for a in args))
    code = main(base + [str(a) for a in args])
    assert code == 0, f"exit code {code}"


def main_demo(work: Path) -> None:
    work = work.resolve()
    if work.exists() and any(work.iterdir()):
        sys.exit("give a NEW or empty scratch directory")
    if ROOT in work.parents or work == ROOT:
        sys.exit("the scratch directory must be outside the repository")
    work.mkdir(parents=True, exist_ok=True)
    state = work / "state"
    cfg_file = work / "demo-config.json"
    demo_channel = config_dict()["youtube"]["channels"][0]
    demo_channel.update(label="[DEMO] fixture channel", basis_for_allowing="FIXTURE ONLY: a made-up channel, no real source", reviewed_by="demo script")
    cfg_file.write_text(json.dumps(config_dict(mode="offline", network_enabled=False, youtube={"max_pages_per_channel": 1, "channels": [demo_channel]}), indent=2))
    print("==== بيانات تجريبية — ليست بحثًا حيًا (FIXTURE DATA - NOT A LIVE SEARCH) ====")
    transport = FakeTransport(search=[ok({"items": [search_item(1, title="[DEMO] Cone weave dribbling drill"), search_item(2, title="[DEMO] Close control dribbling")]})],
                              videos=[ok({"items": [video_item(1, title="[DEMO] Cone weave dribbling drill"), video_item(2, title="[DEMO] Close control dribbling")]})])
    report = run_daily(config(mode="offline", network_enabled=False, youtube={"max_pages_per_channel": 1}), state, env={}, transport=transport, sleep=Sleeps(), fixture=True)
    print(f"\nfixture run: status={report['status']} live={report['live']} fixture_data_not_live_results={report['fixture_data_not_live_results']} "
          f"requests_made_to_the_fake_transport={len(transport.requests)}")
    base = ["--config", str(cfg_file), "--state", str(state)]
    cli(base, "list", "--status", "pending")
    from learning_engine.review import ReviewService
    from learning_engine.store import ResearchStore
    store = ResearchStore(state)
    first = ReviewService(store).list_items("pending")[0]
    item, link = first["item_id"], first["source"]["url"]
    store.close()
    content = work / "demo-drill.json"
    content.write_text(json.dumps(DRILL, indent=2))
    print("\n[the reviewer id typed below is only text; the CLI prints a notice saying it is NOT authentication]")
    cli(base, "save-content", item, "--file", content, "--reviewer-id", "demo-coach")
    cli(base, "decide", item, "--version", 2, "--decision", "approved", "--reviewer-id", "demo-coach",
        "--reasons", "DEMO: wrote the steps myself from my own plan, not from the video.", "--source-checked", link)
    cli(base, "status")
    cli(base, "export-approved")
    print("\n---- fixture report (Markdown) ----")
    print((state / "reports").glob("*-fixture.md").__next__().read_text())
    print("---- files in the state directory ----")
    print(sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file()))
    print("\n==== بيانات تجريبية — ليست بحثًا حيًا. لم يدخل شيء إلى مكتبة اللاعبين أو أي dataset تدريب. ====")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main_demo(Path(sys.argv[1]))
