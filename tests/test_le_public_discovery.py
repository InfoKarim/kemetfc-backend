"""Public keyword discovery (`discovery_mode = public_keyword_search`): no channel needed, results are PUBLIC_METADATA_DISCOVERY.

Fake transports and temporary state only. No network, no key, no AI."""
import json
import urllib.parse
from datetime import timedelta

import pytest

from learning_engine.config import ConfigError, parse_config, youtube_blockers
from learning_engine.daily import run_daily
from learning_engine.review import AUTH_BACKEND, ReviewService, Reviewer
from learning_engine.store import ResearchStore
from tests.le_helpers import CH1, CH2, FAKE_KEY, NOW, FakeTransport, NoNetwork, Sleeps, config, config_dict, err, iid, ok, search_item, video_item, vid
from tests.test_le_source_refresh_invalidation import COACH, GOOD

ENV = {"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}
PUBLIC = {"discovery_mode": "public_keyword_search", "channels": [], "max_pages_per_channel": 1, "results_per_page": 5, "details_batch": 5, "max_retries": 0}


def pub(**youtube):
    return config(mode="live", network_enabled=True, youtube={**PUBLIC, **youtube})


def go(tmp_path, cfg, transport, days=0, env=ENV):
    sl = Sleeps()
    return run_daily(cfg, tmp_path / "state", env=env, now=NOW + timedelta(days=days), transport=transport, sleep=sl, wall_clock=lambda: 10_000.0 + days * 86_400 + len(sl.calls))


def query_of(request):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.url).query))


def rows(tmp_path, sql):
    s = ResearchStore(tmp_path / "state")
    try:
        return [dict(r) for r in s.db.execute(sql)]
    finally:
        s.close()


def two_channel_results():
    return FakeTransport(search=[ok({"items": [search_item(1, channel=CH1), search_item(2, channel=CH2), search_item(3, channel=CH2)]})],
                         videos=[ok({"items": [video_item(1, channel=CH1), video_item(2, channel=CH2), video_item(3, channel=CH2)]})])


# ---- the request ---------------------------------------------------------------------------------------------------------------------------
def test_the_public_search_sends_no_channel_id_and_exactly_one_search_and_one_details_request(tmp_path):
    t = two_channel_results()
    report = go(tmp_path, pub(), t)
    search, details = t.requests
    q = query_of(search)
    assert "channelId" not in q and q["q"] == "football dribbling drills" and q["type"] == "video" and q["maxResults"] == "5" and "/search?" in search.url
    assert "/videos?" in details.url and len(t.requests) == 2 and report["api_calls"]["youtube_search"] == 1 and report["api_calls"]["youtube_other"] == 1
    assert report["status"] == "SUCCESS" and report["discovery"]["queries_sent_this_run"] == 1


def test_the_allowlist_mode_is_unchanged_and_still_sends_the_channel_id(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    go(tmp_path, config(mode="live", network_enabled=True, youtube={"max_pages_per_channel": 1}), t)
    assert query_of(t.requests[0])["channelId"] == CH1
    assert config().youtube.discovery_mode == "allowlisted_channels"


def test_safesearch_is_requested_but_never_presented_as_a_safety_guarantee(tmp_path):
    t = two_channel_results()
    report = go(tmp_path, pub(), t)
    assert query_of(t.requests[0])["safeSearch"] == "strict"
    text = json.dumps(report).lower() + json.dumps(ReviewService(ResearchStore(tmp_path / "state")).list_items()).lower()
    assert "safe for" not in text and "child-safe" not in text and "safesearch" not in text


# ---- configuration -------------------------------------------------------------------------------------------------------------------------
def test_the_public_mode_needs_no_channel_but_the_allowlist_mode_still_does():
    assert youtube_blockers(pub(enabled=True), ENV) == []
    live_allowlist = config(mode="live", network_enabled=True, youtube={"channels": [], "enabled": True})
    assert "NO_CHANNEL_ALLOWLIST" in youtube_blockers(live_allowlist, ENV)


def test_a_channel_in_the_public_mode_config_is_refused_so_a_found_channel_can_never_look_approved():
    with pytest.raises(ConfigError, match="never an approved channel"):
        parse_config(config_dict(youtube={"discovery_mode": "public_keyword_search"}))        # the base config carries an allowlisted test channel
    with pytest.raises(ConfigError, match="discovery_mode"):
        parse_config(config_dict(youtube={"discovery_mode": "everything"}))


def test_the_defaults_and_the_shipped_templates_stay_offline_and_locked():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "learning_engine"
    for name in ("config.example.json", "config.next-phase.template.json"):
        cfg = parse_config(json.loads((root / name).read_text()))
        assert cfg.mode == "offline" and cfg.network_enabled is False and cfg.youtube.enabled is False and cfg.youtube.channels == ()
        assert cfg.providers == {} and cfg.budget.daily_cost_usd == 0 and cfg.budget.daily_ai_requests == 0
    assert parse_config({"schema": "kemet.learning_engine.config/1"}).mode == "offline"


def test_offline_or_keyless_public_mode_sends_nothing(tmp_path):
    offline = go(tmp_path / "a", config(mode="offline", network_enabled=False, youtube={**PUBLIC, "enabled": True}), NoNetwork())
    assert offline["status"] == "OFFLINE_NO_DISCOVERY"
    keyless = go(tmp_path / "b", pub(enabled=True), NoNetwork(), env={})
    assert "YOUTUBE_API_KEY_NOT_SET" in keyless["services"]["youtube"]["reasons"] and keyless["api_calls"]["youtube_search"] == 0


# ---- provenance ----------------------------------------------------------------------------------------------------------------------------
def test_everything_found_is_public_metadata_discovery_pending_and_claims_no_approval_or_permission(tmp_path):
    t = two_channel_results()
    report = go(tmp_path, pub(enabled=True), t)
    src = rows(tmp_path, "select * from sources order by video_id")
    assert [s["discovery_class"] for s in src] == ["PUBLIC_METADATA_DISCOVERY"] * 3 and all(s["discovery_query"] == "football dribbling drills" for s in src)
    assert {s["channel_id"] for s in src} == {CH1, CH2} and all(s["url"] == f"https://www.youtube.com/watch?v={s['video_id']}" and s["discovered_at"].endswith("Z") for s in src)
    items = rows(tmp_path, "select review_status, stage, published from review_items")
    assert len(items) == 3 and all(i["review_status"] == "pending" and i["published"] == 0 for i in items)
    assert rows(tmp_path, "select count(*) as n from reviews")[0]["n"] == 0 and rows(tmp_path, "select count(*) as n from item_versions where author_kind<>'NONE'")[0]["n"] == 0
    assert "not an approved channel" in report["discovery"]["meaning"] and "no permission" in report["discovery"]["meaning"]
    s = ResearchStore(tmp_path / "state")
    got = ReviewService(s).get(iid(tmp_path / "state", 1))
    assert got["source"]["discovery_class"] == "PUBLIC_METADATA_DISCOVERY" and "not approved" in got["note"] and got["content"] == {} and got["review_status"] == "pending"
    s.close()
    md = (tmp_path / "state" / "reports" / "2026-10-08-live.md").read_text()
    assert "PUBLIC_METADATA_DISCOVERY" in md and "queries sent this run: 1" in md


def test_a_discovered_channel_is_never_added_to_any_allowlist(tmp_path):
    cfg = pub(enabled=True)
    go(tmp_path, cfg, two_channel_results())
    assert cfg.youtube.channels == () and cfg.youtube.future_queries == ()
    tables = {r["name"] for r in rows(tmp_path, "select name from sqlite_master where type='table'")}
    assert not any("channel" in name or "allow" in name for name in tables)


# ---- limits --------------------------------------------------------------------------------------------------------------------------------
def test_pagination_is_bounded_and_the_next_page_token_is_followed_only_up_to_the_limit(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1)], "nextPageToken": "T1"}), ok({"items": [search_item(2, title="Another dribbling title")], "nextPageToken": "T2"}),
                              ok({"items": [search_item(3, title="Third dribbling title")]})], videos=[ok({"items": [video_item(1), video_item(2, title="Another dribbling title")]})])
    go(tmp_path, pub(max_pages_per_channel=2, search_calls_per_day=6, units_per_day=20), t)
    searches = [r for r in t.requests if "/search?" in r.url]
    assert len(searches) == 2 and "pageToken" not in query_of(searches[0]) and query_of(searches[1])["pageToken"] == "T1"


def test_with_zero_retries_a_failed_search_is_sent_once_and_not_repeated(tmp_path):
    t = FakeTransport(search=[err(500), ok({"items": [search_item(1)]})])
    report = go(tmp_path, pub(), t)
    assert len(t.requests) == 1 and report["status"] in ("FAILED", "PARTIAL") and report["errors"][0]["code"].startswith("HTTP_500")
    again = go(tmp_path, pub(), FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})]), days=0)
    assert again["attempt"] == 2 and again["api_calls"]["youtube_search"] == 2          # a later run is a new, counted attempt; nothing repeats by itself


def test_only_the_one_configured_query_is_sent_and_future_queries_are_stored_but_never_used(tmp_path):
    t = two_channel_results()
    report = go(tmp_path, pub(future_queries=["youth dribbling cone drills", "1v1 dribbling moves"]), t)
    assert [query_of(r)["q"] for r in t.requests if "/search?" in r.url] == ["football dribbling drills"]
    assert report["discovery"]["future_queries_prepared_not_sent"] == ["youth dribbling cone drills", "1v1 dribbling moves"] and report["discovery"]["queries_sent_this_run"] == 1
    with pytest.raises(ConfigError, match="future_queries"):
        parse_config(config_dict(youtube={"future_queries": ["football tactics"], "discovery_mode": "allowlisted_channels"}))


def test_no_ai_request_and_nothing_but_youtube_metadata_requests_are_made(tmp_path):
    t = two_channel_results()
    report = go(tmp_path, pub(), t)
    assert all(r.url.startswith("https://www.googleapis.com/youtube/v3/") and r.method == "GET" and r.body is None for r in t.requests)
    assert report["api_calls"]["openai"] == 0 and report["api_calls"]["anthropic"] == 0 and report["cost_usd"]["openai"]["reserved"] == 0


# ---- deduplication, retention, invalidation still work for public finds -----------------------------------------------------------------------------------------
def test_duplicates_across_runs_and_by_exact_title_are_handled(tmp_path):
    first = FakeTransport(search=[ok({"items": [search_item(1, title="Cone Weave Drill"), search_item(2, channel=CH2, title="cone  weave drill!")]})],
                          videos=[ok({"items": [video_item(1, title="Cone Weave Drill")]})])
    r1 = go(tmp_path, pub(), first)
    assert r1["sources"]["new"] == 1 and r1["sources"]["duplicate_by_text_fingerprint"] == 1
    again = FakeTransport(search=[ok({"items": [search_item(1, title="Cone Weave Drill")]})], videos=[])
    r2 = go(tmp_path, pub(), again, days=1)
    assert r2["sources"]["duplicate_by_video_id"] == 1 and r2["sources"]["new"] == 0


def test_a_public_find_that_was_approved_and_then_went_private_needs_review_and_is_removed_after_the_window(tmp_path):
    go(tmp_path, pub(), FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})]))
    item = iid(tmp_path / "state", 1)
    s = ResearchStore(tmp_path / "state")
    svc = ReviewService(s)
    svc.save_content(item, GOOD, COACH)
    svc.decide(item, 2, "approved", COACH, "Wrote the steps myself; inspected the public page.", [f"https://www.youtube.com/watch?v={vid(1)}"])
    s.close()
    go(tmp_path, pub(), FakeTransport(search=[ok({"items": []})], videos=[ok({"items": [video_item(1, privacy="private")]})]), days=1)
    s = ResearchStore(tmp_path / "state")
    assert ReviewService(s).get(item)["needs_source_review"] is True and ReviewService(s).approved_content() == []
    s.close()
    run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env={}, now=NOW + timedelta(days=33))
    s = ResearchStore(tmp_path / "state")
    assert s.db.execute("select count(*) from sources").fetchone()[0] == 0 and ReviewService(s).approved_content() == []
    s.close()


# ---- migration 3 -> 4 -----------------------------------------------------------------------------------------------------------------------
def test_a_version_3_database_upgrades_and_old_sources_are_marked_as_allowlisted(tmp_path):
    st = ResearchStore(tmp_path / "s")
    st.db.execute("INSERT INTO sources(video_id,channel_id,url,discovered_at,query_version,stage) VALUES ('vidAAAAAAAA',?,'u','t','q','discovered')", (CH1,))
    st.db.execute("ALTER TABLE sources DROP COLUMN discovery_class")
    st.db.execute("ALTER TABLE sources DROP COLUMN discovery_query")
    st.db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    st.db.commit()
    st.close()
    up = ResearchStore(tmp_path / "s")
    row = up.db.execute("select discovery_class, discovery_query from sources").fetchone()
    assert up.schema_version() == 5 and (row[0], row[1]) == ("ALLOWLISTED_CHANNEL", None)
    up.close()
    assert ResearchStore(tmp_path / "s").schema_version() == 5
