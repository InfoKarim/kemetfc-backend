"""The backend review API: locked by default, coach/admin only, additive, and it touches only the research database.

Uses the platform's own scratch-database test harness (tests/test_privacy_s0.ctx); no real data, no network.
"""
import json
import os
from pathlib import Path

import pytest

from learning_engine.review import ReviewService
from learning_engine.store import ResearchStore
from tests.le_helpers import FakeTransport, NOW, FAKE_KEY, Sleeps, config, iid, ok, search_item, video_item, vid
from tests.test_privacy_s0 import ctx  # noqa: F401  (a fixture)
from learning_engine.daily import run_daily
from sqlalchemy import text

GOOD = {"title": "Cone weave dribble", "category": "dribbling", "steps": ["Set five cones in a line, one metre apart.", "Dribble through using the inside of each foot."],
        "equipment": ["5 cones", "1 ball"], "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Flat surface, no studs on hard ground."}
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def state(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1), search_item(2)]})], videos=[ok({"items": [video_item(1), video_item(2)]})])
    run_daily(config(youtube={"max_pages_per_channel": 1}), tmp_path / "state", env={"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}, now=NOW, transport=t, sleep=Sleeps(), wall_clock=lambda: 1.0)
    return tmp_path / "state"


@pytest.fixture()
def item(state):
    return iid(state, 1)


@pytest.fixture()
def enabled(monkeypatch, state):
    monkeypatch.setenv("LEARNING_ENGINE_REVIEW_API_ENABLED", "1")
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", str(state))
    return state


LINK = f"https://www.youtube.com/watch?v={vid(1)}"


def test_every_route_is_a_404_for_everyone_while_it_is_locked(ctx, monkeypatch, state, item):
    monkeypatch.delenv("LEARNING_ENGINE_REVIEW_API_ENABLED", raising=False)
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", str(state))
    assert ctx.guard_a.get("/learning-review/items").status_code == 403           # guardians are turned away by the platform's own role gate first
    for client in (ctx.admin, ctx.coach):
        assert client.get("/learning-review/items").status_code == 404
        assert client.get(f"/learning-review/items/{item}").status_code == 404
        assert client.post(f"/learning-review/items/{item}/content", json={"content": GOOD}).status_code == 404
        assert client.post(f"/learning-review/items/{item}/decision", json={"version": 1, "decision": "rejected", "reasons": "x" * 20}).status_code == 404
    for wrong in ("true", "yes", "0", ""):
        monkeypatch.setenv("LEARNING_ENGINE_REVIEW_API_ENABLED", wrong)
        assert ctx.admin.get("/learning-review/items").status_code == 404


def test_logged_out_users_never_reach_it(monkeypatch, state, ctx):
    from fastapi.testclient import TestClient
    from main import app
    monkeypatch.setenv("LEARNING_ENGINE_REVIEW_API_ENABLED", "1")
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", str(state))
    assert TestClient(app).get("/learning-review/items").status_code == 401


def test_only_coaches_and_admins_may_use_it_and_it_needs_a_configured_state_directory(ctx, enabled, monkeypatch, item):
    assert ctx.guard_a.get("/learning-review/items").status_code == 403
    assert ctx.guard_a.post(f"/learning-review/items/{item}/decision", json={"version": 1, "decision": "rejected", "reasons": "x" * 20}).status_code == 403
    assert ctx.coach.get("/learning-review/items").status_code == 200 and ctx.admin.get("/learning-review/items").status_code == 200
    monkeypatch.delenv("LEARNING_ENGINE_STATE_DIR")
    assert ctx.coach.get("/learning-review/items").status_code == 503
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", "relative/dir")
    assert ctx.coach.get("/learning-review/items").status_code == 503


def test_listing_and_reading_show_pending_candidates_with_their_source_and_no_drill_content(ctx, enabled, item):
    items = ctx.coach.get("/learning-review/items", params={"status": "pending"}).json()
    assert sorted(i["item_id"] for i in items) == sorted([item, iid(enabled, 2)]) and all(i["review_status"] == "pending" and i["published"] is False for i in items)
    one = ctx.coach.get(f"/learning-review/items/{item}").json()
    assert one["content"] == {} and one["source"]["url"] == LINK and one["history"] == [] and "says nothing proven" in one["note"]
    assert ctx.coach.get("/learning-review/items/NOPE").status_code == 404
    assert ctx.coach.get("/learning-review/items", params={"status": "weird"}).status_code == 422


def test_the_reviewer_is_the_authenticated_account_never_a_name_in_the_request(ctx, enabled, item):
    assert ctx.coach.post(f"/learning-review/items/{item}/content", json={"content": GOOD}).status_code == 200
    r = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Inspected the source; the steps are mine.",
                                                                         "sources_checked": [LINK], "reviewer_id": "someone-else"})
    assert r.status_code == 200 and r.json()["review_status"] == "approved" and r.json()["published"] is False
    history = ctx.coach.get(f"/learning-review/items/{item}").json()["history"][0]
    assert history["reviewer_id"] == "COACH" and history["auth_mode"] == "backend_user" and history["version"] == 2


def test_incomplete_content_stale_versions_and_repeat_decisions_are_refused_with_clear_codes(ctx, enabled, item):
    r = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 1, "decision": "approved", "reasons": "Approving with no content at all.", "sources_checked": [LINK]})
    assert r.status_code == 422 and "not complete" in r.json()["detail"]
    ctx.coach.post(f"/learning-review/items/{item}/content", json={"content": GOOD})
    stale = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 1, "decision": "rejected", "reasons": "Deciding on the old version of it."})
    assert stale.status_code == 409
    ok_ = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "rejected", "reasons": "Not suitable for our age groups."})
    assert ok_.status_code == 200
    again = ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Changing my mind later on.", "sources_checked": [LINK]})
    assert again.status_code == 409
    assert ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 0, "decision": "approved", "reasons": "x" * 20}).status_code == 422
    assert ctx.coach.post(f"/learning-review/items/{item}/decision", json={"decision": "approved"}).status_code == 422


def test_editing_approved_content_through_the_api_sends_it_back_to_pending(ctx, enabled, item):
    ctx.coach.post(f"/learning-review/items/{item}/content", json={"content": GOOD})
    ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Source inspected and content written.", "sources_checked": [LINK]})
    edit = ctx.admin.post(f"/learning-review/items/{item}/content", json={"content": {**GOOD, "duration_minutes": 25}})
    assert edit.status_code == 200 and edit.json()["review_status"] == "pending" and edit.json()["version"] == 3


def test_post_requests_without_the_csrf_token_are_rejected_like_every_other_write(ctx, enabled, item):
    from fastapi.testclient import TestClient
    from main import app
    bare = TestClient(app)
    assert bare.post("/auth/login", json={"username": "s0.coach", "password": "s0.coachPassword123!"}).status_code == 200
    assert bare.post(f"/learning-review/items/{item}/content", json={"content": GOOD}).status_code == 403


def test_the_api_only_ever_touches_the_research_database_not_the_application_database(ctx, enabled, item):
    def snapshot():
        with ctx.engine.connect() as c:
            return {t: c.execute(text(f"select count(*) from {t}")).scalar() for t in ("players", "guardian_consents", "ml_dataset_entries", "videos", "drills", "training_plans", "audit_events")}
    before = snapshot()
    ctx.coach.post(f"/learning-review/items/{item}/content", json={"content": GOOD})
    ctx.coach.post(f"/learning-review/items/{item}/decision", json={"version": 2, "decision": "approved", "reasons": "Source inspected and content written.", "sources_checked": [LINK]})
    assert snapshot() == before
    router_source = (ROOT / "app/routers/learning_review.py").read_text()
    for forbidden in ("get_db", "db_models", "Session", "PlayerDB", "VideoDB", "TrainingPlan", "DrillDB"):
        assert forbidden not in router_source, forbidden


def test_the_application_imports_and_starts_without_the_research_package_being_loaded():
    import subprocess, sys
    code = "import sys; import main; print('learning_engine' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=120,
                         env={**os.environ, "DATABASE_URL": "sqlite://", "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip().splitlines()[-1] == "False"                           # lazy import: nothing is loaded until the feature is used
