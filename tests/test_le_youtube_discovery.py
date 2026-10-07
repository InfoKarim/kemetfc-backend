"""Learning engine: official-API discovery with fake transports only (no network, no key, no real channel)."""
import json
import threading

import pytest

from learning_engine import discovery as dsc
from learning_engine.budget import Budget, BudgetExceeded, Pacer
from learning_engine.daily import run_daily
from learning_engine.http import HttpRequest, TransportError, UrllibTransport, redact
from learning_engine.store import ResearchStore
from learning_engine.youtube import YouTubeApiError, YouTubeClient
from tests.le_helpers import CH1, CH2, FAKE_KEY, FakeTransport, NOW, NoNetwork, Sleeps, config, config_dict, err, ok, search_item, vid, video_item

ENV = {"KEMET_LE_YOUTUBE_API_KEY": FAKE_KEY}


def run(tmp_path, transport, cfg=None, **kw):
    sleeps = kw.pop("sleep", Sleeps())
    report = run_daily(cfg or config(), tmp_path / "state", env=ENV, now=NOW, transport=transport, sleep=sleeps, wall_clock=lambda: 10_000.0 + len(sleeps.calls), **kw)
    return report, sleeps


def rows(tmp_path, sql, *args):
    s = ResearchStore(tmp_path / "state")
    try:
        return [dict(r) for r in s.db.execute(sql, args).fetchall()]
    finally:
        s.close()


# ---- the happy path and the states ---------------------------------------------------------------------------------------------
def test_discovered_then_metadata_only_with_provenance_and_nothing_else(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1), search_item(2)]})], videos=[ok({"items": [video_item(1), video_item(2)]})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["status"] == "SUCCESS" and report["sources"]["discovered"] == 2 and report["sources"]["new"] == 2 and report["sources"]["metadata_only"] == 2
    src = rows(tmp_path, "select * from sources order by video_id")
    assert [s["stage"] for s in src] == ["metadata_only", "metadata_only"] and all(s["availability"] == "available" for s in src)
    s = src[0]
    assert s["url"] == f"https://www.youtube.com/watch?v={s['video_id']}" and s["channel_id"] == CH1 and s["query_version"] == "dribbling-v1"
    assert s["discovered_at"].endswith("Z") and s["verified_at"].endswith("Z")
    assert set(s) == {"video_id", "channel_id", "url", "title", "published_at", "duration_s", "made_for_kids", "privacy_status", "discovered_at",
                      "verified_at", "query_version", "text_fingerprint", "duplicate_of", "stage", "availability", "exclusion_reason"}    # no description, no thumbnail, no transcript
    items = rows(tmp_path, "select origin, stage, review_status, published from review_items")
    assert all(i == {"origin": "YOUTUBE_SOURCE", "stage": "metadata_only", "review_status": "pending", "published": 0} for i in items)
    assert report["stages"] == {"discovered": 0, "metadata_only": 2, "ai_draft": 0, "coach_reviewed": 0}


def test_a_title_is_never_taken_as_evidence_of_steps_or_age(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1, title="Dribbling drill for 8 year olds: 5 steps, no cones")]})], videos=[ok({"items": [video_item(1)]})])
    run(tmp_path, t)
    v = rows(tmp_path, "select content_json, author_kind from item_versions")[0]
    assert v == {"content_json": "{}", "author_kind": "NONE"}


def test_the_request_uses_the_documented_parameters_a_header_key_and_safe_search(tmp_path):
    t = FakeTransport(search=[ok({"items": []})], videos=[])
    run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    req = t.requests[0]
    assert req.method == "GET" and req.url.startswith("https://www.googleapis.com/youtube/v3/search?")
    q = dict(p.split("=") for p in req.url.split("?")[1].split("&"))
    assert q["part"] == "snippet" and q["type"] == "video" and q["channelId"] == CH1 and q["safeSearch"] == "strict" and q["maxResults"] == "5"
    assert req.headers["x-goog-api-key"] == FAKE_KEY and "key=" not in req.url and FAKE_KEY not in req.url
    q2 = FakeTransport(search=[ok({"items": []})])
    run_daily(config(youtube={"api_key_transport": "query", "max_pages_per_channel": 1}), tmp_path / "other", env=ENV, now=NOW, transport=q2, sleep=Sleeps(), wall_clock=lambda: 1.0)
    assert "key=" in q2.requests[0].url and "x-goog-api-key" not in q2.requests[0].headers


# ---- allowlist and validation ---------------------------------------------------------------------------------------------------------
def test_results_from_a_channel_that_is_not_the_allowlisted_one_are_ignored(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1, channel=CH2), search_item(2)]})], videos=[ok({"items": [video_item(2)]})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert [r["video_id"] for r in rows(tmp_path, "select video_id from sources")] == [vid(2)] and report["sources"]["invalid_items"] == 1


@pytest.mark.parametrize("item", [
    None, "text", {}, {"id": "x", "snippet": {}}, {"id": {"videoId": "short"}, "snippet": {"channelId": CH1, "title": "T"}},
    {"id": {"videoId": vid(1)}, "snippet": {"channelId": CH1, "title": ""}}, {"id": {"videoId": vid(1)}, "snippet": {"channelId": CH1, "title": "T" * 301}},
    {"id": {"videoId": vid(1)}, "snippet": {"channelId": CH1, "title": 5}}, {"id": {"videoId": "bad id with spaces"}, "snippet": {"channelId": CH1, "title": "T"}},
    {"id": {"videoId": vid(1)}, "snippet": {"channelId": CH1, "title": "T", "publishedAt": 7}},
])
def test_malformed_search_items_are_dropped_and_counted(tmp_path, item):
    t = FakeTransport(search=[ok({"items": [item]})], videos=[])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["sources"]["invalid_items"] == 1 and report["sources"]["new"] == 0 and rows(tmp_path, "select * from sources") == []


def test_incomplete_responses_are_errors_not_silent_successes(tmp_path):
    for bad in ({"nothing": 1}, {"items": "no"}, ["list"]):
        t = FakeTransport(search=[ok(bad)], videos=[])
        report, _ = run(tmp_path / bad.__class__.__name__ / str(len(str(bad))), t, config(youtube={"max_pages_per_channel": 1}))
        assert report["status"] in ("FAILED", "PARTIAL") and report["errors"] and report["status"] != "SUCCESS"


def test_details_exclude_not_public_made_for_kids_live_and_implausible_videos(tmp_path):
    items = [video_item(1), video_item(2, privacy="private"), video_item(3, kids=True), video_item(4, live="upcoming"), video_item(5, duration="PT2S"),
             video_item(6, duration="not-a-duration"), video_item(7, channel=CH2)]
    t = FakeTransport(search=[ok({"items": [search_item(n) for n in range(1, 8)]})], videos=[ok({"items": items})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1, "results_per_page": 10, "details_batch": 10, "search_calls_per_day": 10}))
    got = {r["video_id"]: (r["availability"], r["exclusion_reason"]) for r in rows(tmp_path, "select video_id, availability, exclusion_reason from sources")}
    assert got[vid(1)] == ("available", None)
    assert got[vid(2)] == ("excluded", "NOT_PUBLIC") and got[vid(3)] == ("excluded", "MADE_FOR_KIDS") and got[vid(4)] == ("excluded", "LIVE_OR_UPCOMING_BROADCAST")
    assert got[vid(5)] == ("excluded", "IMPLAUSIBLE_DURATION") and got[vid(6)] == ("excluded", "IMPLAUSIBLE_DURATION") and got[vid(7)] == ("excluded", "CHANNEL_CHANGED")
    assert report["sources"]["metadata_only"] == 1 and report["sources"]["excluded"] == 6


def test_a_video_the_details_call_does_not_return_is_unavailable_never_assumed_fine(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1), search_item(2)]})], videos=[ok({"items": [video_item(1)]})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    got = {r["video_id"]: r["availability"] for r in rows(tmp_path, "select video_id, availability from sources")}
    assert got == {vid(1): "available", vid(2): "unavailable"} and report["sources"]["unavailable"] == 1


# ---- duplicates ---------------------------------------------------------------------------------------------------------------------------
def test_the_same_video_id_is_never_stored_twice_across_runs_and_pages(tmp_path):
    first = FakeTransport(search=[ok({"items": [search_item(1), search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    report, _ = run(tmp_path, first, config(youtube={"max_pages_per_channel": 1}))
    assert report["sources"]["new"] == 1 and report["sources"]["duplicate_by_video_id"] == 1
    second = FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[])
    report2, _ = run(tmp_path, second, config(youtube={"max_pages_per_channel": 1}), day="2026-10-09")
    assert report2["sources"]["new"] == 0 and report2["sources"]["duplicate_by_video_id"] == 1 and len(rows(tmp_path, "select * from sources")) == 1


def test_exact_normalized_title_duplicates_are_flagged_but_different_wording_is_not(tmp_path):
    items = [search_item(1, title="Dribbling Drill: Cone Weave!"), search_item(2, title="  dribbling drill  cone weave "), search_item(3, title="DRIBBLING DRILL — CONE WEAVE"),
             search_item(4, title="Cone weaving dribbling exercise")]
    t = FakeTransport(search=[ok({"items": items})], videos=[ok({"items": [video_item(1), video_item(4)]})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1, "results_per_page": 10}))
    assert report["sources"]["duplicate_by_text_fingerprint"] == 2 and report["sources"]["new"] == 2
    dups = rows(tmp_path, "select video_id, duplicate_of from sources where duplicate_of is not null order by video_id")
    assert dups == [{"video_id": vid(2), "duplicate_of": vid(1)}, {"video_id": vid(3), "duplicate_of": vid(1)}]
    assert len(rows(tmp_path, "select * from review_items")) == 2                                  # duplicates do not become review items
    assert dsc.fingerprint("Cone weaving dribbling exercise") != dsc.fingerprint("Dribbling drill cone weave")        # NOT semantic deduplication


def test_normalization_rules():
    assert dsc.normalize_title("Ｄribbling  Drill!!") == "dribbling drill"
    assert dsc.fingerprint("") is None and dsc.fingerprint("!!!") is None


# ---- pagination ----------------------------------------------------------------------------------------------------------------------------
def test_pagination_stops_at_the_configured_page_limit_even_if_more_pages_exist(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1)], "nextPageToken": "T1"}), ok({"items": [search_item(2)], "nextPageToken": "T2"}),
                              ok({"items": [search_item(3)], "nextPageToken": "T3"})], videos=[ok({"items": [video_item(1), video_item(2)]})])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 2}))
    assert len([r for r in t.requests if "/search?" in r.url]) == 2 and report["sources"]["new"] == 2
    assert "pageToken=T1" in t.requests[1].url and "pageToken" not in t.requests[0].url
    assert len(t.script["search"]) == 1                                                            # the third page was never requested


def test_pagination_ends_when_there_is_no_next_token_and_a_hostile_token_is_dropped(tmp_path):
    t = FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    run(tmp_path, t, config(youtube={"max_pages_per_channel": 3}))
    assert len([r for r in t.requests if "/search?" in r.url]) == 1
    t2 = FakeTransport(search=[ok({"items": [search_item(1)], "nextPageToken": "x" * 500})], videos=[ok({"items": [video_item(1)]})])
    run(tmp_path / "b", t2, config(youtube={"max_pages_per_channel": 3}))
    assert len([r for r in t2.requests if "/search?" in r.url]) == 1


# ---- retries, backoff, quota ------------------------------------------------------------------------------------------------------------------
def test_a_temporary_error_is_retried_with_backoff_and_every_attempt_is_counted(tmp_path):
    t = FakeTransport(search=[err(503), err(503), ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    report, sleeps = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["status"] == "SUCCESS" and sleeps.calls == [1.0, 2.0, 0.5]        # backoff 1s, 2s; then 0.5s of pacing before the details call
    assert report["api_calls"]["youtube_search"] == 3 and report["api_calls"]["youtube_other"] == 1 and report["quota"]["units_used"] == 4


def test_retries_are_bounded_and_a_persistent_failure_is_a_failed_day_not_a_silent_success(tmp_path):
    t = FakeTransport(search=[err(500), err(500), err(500), err(500)], videos=[])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1, "max_retries": 2}))
    assert report["status"] == "FAILED" and report["errors"] == [{"where": "search.list", "channel": CH1, "code": "HTTP_500"}]
    assert report["api_calls"]["youtube_search"] == 3 and len(t.script["search"]) == 1             # 1 + 2 retries, then stop


def test_a_quota_error_is_not_retried_and_stops_the_run(tmp_path):
    t = FakeTransport(search=[err(403, "quotaExceeded")], videos=[])
    report, sleeps = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["status"] == "FAILED" and report["errors"][0]["code"] == "HTTP_403_quotaExceeded" and sleeps.calls == [] and len(t.requests) == 1


@pytest.mark.parametrize("status,reason", [(400, "keyInvalid"), (400, None), (401, None), (403, "forbidden"), (404, "notFound")])
def test_permanent_errors_are_not_retried(tmp_path, status, reason):
    t = FakeTransport(search=[err(status, reason)], videos=[])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["status"] == "FAILED" and len(t.requests) == 1


def test_timeouts_and_connection_errors_are_retried_then_reported_by_code_only(tmp_path):
    t = FakeTransport(search=[TransportError("TIMEOUT"), TransportError("CONNECTION"), TransportError("TIMEOUT")], videos=[])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    assert report["status"] == "FAILED" and report["errors"][0]["code"] == "TRANSPORT_TIMEOUT" and report["api_calls"]["youtube_search"] == 3


def test_the_daily_search_limit_is_persisted_and_stops_the_run_cleanly(tmp_path):
    cfg = config(youtube={"search_calls_per_day": 1, "max_pages_per_channel": 2, "channels": config_dict()["youtube"]["channels"]})
    t = FakeTransport(search=[ok({"items": [search_item(1)], "nextPageToken": "T"}), ok({"items": [search_item(2)]})], videos=[ok({"items": [video_item(1)]})])
    report, _ = run(tmp_path, t, cfg)
    assert report["status"] != "SUCCESS" and {"where": "youtube_budget", "code": "YOUTUBE_SEARCH_CALLS_PER_DAY_LIMIT"} in report["errors"]
    assert report["api_calls"]["youtube_search"] == 1 and any("limit" in n for n in report["notes"])
    t2 = FakeTransport(search=[], videos=[])                                                         # a new process, same day: the limit is remembered
    report2, _ = run(tmp_path, t2, cfg)
    assert t2.requests == [] and report2["api_calls"]["youtube_search"] == 1 and report2["attempt"] == 2


def test_units_and_search_calls_are_counted_separately(tmp_path):
    cfg = config(youtube={"search_calls_per_day": 5, "units_per_day": 2, "max_pages_per_channel": 1})
    t = FakeTransport(search=[ok({"items": [search_item(1)]})], videos=[ok({"items": [video_item(1)]})])
    report, _ = run(tmp_path, t, cfg)
    assert report["quota"]["search_calls"] == 1 and report["quota"]["other_calls"] == 1 and report["quota"]["units_used"] == 2 and report["status"] == "SUCCESS"
    t2 = FakeTransport(search=[ok({"items": []})])
    report2, _ = run(tmp_path, t2, cfg, day="2026-10-09")                                           # same quota day (Pacific): units exhausted
    assert {"where": "youtube_budget", "code": "YOUTUBE_UNITS_PER_DAY_LIMIT"} in report2["errors"] and t2.requests == []


def test_rate_pacing_waits_between_requests_and_remembers_across_restarts(tmp_path):
    store = ResearchStore(tmp_path / "s")
    sleeps, clock = Sleeps(), {"t": 100.0}
    pacer = Pacer(store, 2.0, lambda: clock["t"], sleeps)
    pacer.wait(); assert sleeps.calls == []
    clock["t"] = 100.5; pacer.wait(); assert sleeps.calls == [1.5]
    store.close()
    store2 = ResearchStore(tmp_path / "s")
    Pacer(store2, 2.0, lambda: 100.6, sleeps).wait()                                                 # a restarted process still paces
    assert len(sleeps.calls) == 2 and sleeps.calls[1] == pytest.approx(1.9, abs=0.001)
    store2.close()


# ---- concurrency -----------------------------------------------------------------------------------------------------------------------------------
def test_concurrent_reservations_never_exceed_the_limit(tmp_path):
    cfg = config(youtube={"search_calls_per_day": 5, "units_per_day": 100})
    ResearchStore(tmp_path / "s").close()
    granted, refused = [], []

    def worker():
        store = ResearchStore(tmp_path / "s")
        try:
            Budget(store, cfg, lambda: NOW).reserve_youtube("youtube_search")
            granted.append(1)
        except BudgetExceeded:
            refused.append(1)
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(granted) == 5 and len(refused) == 11


# ---- transport safety --------------------------------------------------------------------------------------------------------------------------------
def test_the_real_transport_refuses_http_unknown_hosts_credentials_in_urls_and_odd_ports():
    tr = UrllibTransport()
    for url, code in (("http://www.googleapis.com/youtube/v3/search", "NOT_HTTPS"), ("https://evil.example.com/youtube/v3/search", "HOST_NOT_ALLOWED"),
                      ("https://www.googleapis.com.evil.com/x", "HOST_NOT_ALLOWED"), ("https://user:pw@www.googleapis.com/x", "HOST_NOT_ALLOWED"),
                      ("https://www.googleapis.com:8443/x", "HOST_NOT_ALLOWED"), ("ftp://www.googleapis.com/x", "NOT_HTTPS")):
        with pytest.raises(TransportError) as e:
            tr(HttpRequest("GET", url))
        assert e.value.code == code


def test_redirects_are_refused():
    from learning_engine.http import _NoRedirect
    with pytest.raises(TransportError) as e:
        _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/")
    assert e.value.code == "REDIRECT_REFUSED"


def test_nothing_is_ever_reported_that_could_contain_a_key_or_a_response_body(tmp_path):
    t = FakeTransport(search=[err(500, text=f"leaked {FAKE_KEY} in message"), err(500, text="x"), err(500, text="x")], videos=[])
    report, _ = run(tmp_path, t, config(youtube={"max_pages_per_channel": 1}))
    state = tmp_path / "state"
    blob = json.dumps(report) + "".join(p.read_text() for p in (state / "reports").iterdir())
    blob += "".join(str(r) for r in rows(tmp_path, "select * from audit"))
    assert FAKE_KEY not in blob and "leaked" not in blob and "secret-ish" not in blob
    assert redact(f"https://x/?key={FAKE_KEY}&a=1", [FAKE_KEY]) == "https://x/?key=[REDACTED]&a=1"
    for path in state.rglob("*"):
        if path.is_file():
            assert FAKE_KEY.encode() not in path.read_bytes(), path.name


def test_offline_mode_never_touches_the_network_even_with_a_key_present(tmp_path):
    report = run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env=ENV, now=NOW, transport=NoNetwork())
    assert report["status"] == "OFFLINE_NO_DISCOVERY" and report["api_calls"] == {"youtube_search": 0, "youtube_other": 0, "openai": 0, "anthropic": 0}
    assert report["services"]["youtube"]["reasons"] == ["MODE_IS_OFFLINE"]
