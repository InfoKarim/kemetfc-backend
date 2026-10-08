"""Learning engine, review fix 1: every request that was SENT counts against the request cap, whatever happened to it afterwards.

All with fake transports, a fake wall clock and temporary databases: no network, no key, no provider call.
"""
import json
import threading

import pytest

from learning_engine import ai
from learning_engine.budget import Budget, BudgetExceeded
from learning_engine.daily import run_daily
from learning_engine.http import HttpResponse, TransportError
from learning_engine.store import ResearchStore
from tests.le_helpers import FAKE_KEY, NOW, NoNetwork, Sleeps
from tests.test_le_budget_ai import ENV, Transport, WallClock, ledger, make, openai_ok, paid

CAP_ONE = {"daily_ai_requests": 1}


def rows(store):
    return [dict(r) for r in store.db.execute("select status, sent_at, tokens_reserved, usd_reserved, note from ledger where kind='ai' order by id")]


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_a_one_request_cap_blocks_the_second_send_after_a_server_error(tmp_path, status):
    t = Transport(HttpResponse(status, b"{}"), openai_ok())
    store, _, drafts = make(tmp_path, paid(budget=CAP_ONE), t)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    with pytest.raises(BudgetExceeded) as e:
        drafts.generate("openai")
    assert e.value.reason == "AI_REQUESTS_PER_DAY_LIMIT" and len(t.requests) == 1                   # the second request never left
    store.close()


def test_a_504_keeps_its_token_and_cost_reservation_and_is_not_retried_even_when_retries_are_allowed(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 3, "daily_ai_tokens": 3000, "daily_cost_usd": 1.0})
    cfg.providers["openai"] = type(cfg.providers["openai"])(**{**cfg.providers["openai"].__dict__, "max_retries": 2})
    t = Transport(HttpResponse(504, b"{}"), openai_ok(), openai_ok())
    store, budget, drafts = make(tmp_path, cfg, t)
    with pytest.raises(TransportError) as e:
        drafts.generate("openai")
    assert e.value.code == "HTTP_504" and len(t.requests) == 1
    row = rows(store)[0]
    assert row["status"] == "uncertain" and row["sent_at"] is not None and row["tokens_reserved"] > 0 and row["usd_reserved"] > 0
    with store.transaction() as db:
        requests, tokens, usd = budget._ai_totals(db, "2026-10-08")
    assert (requests, tokens) == (1, row["tokens_reserved"]) and usd == pytest.approx(row["usd_reserved"])
    store.close()


def test_a_timeout_and_a_lost_connection_count_as_sent_and_are_not_retried(tmp_path):
    for code in ("TIMEOUT", "CONNECTION", "RESPONSE_TOO_LARGE", "REDIRECT_REFUSED"):
        t = Transport(TransportError(code), openai_ok())
        store, _, drafts = make(tmp_path / code, paid(budget=CAP_ONE), t)
        with pytest.raises(TransportError):
            drafts.generate("openai")
        assert len(t.requests) == 1 and [r["status"] for r in rows(store)] == ["uncertain"]
        with pytest.raises(BudgetExceeded):
            drafts.generate("openai")
        store.close()


def test_an_unreadable_200_and_other_statuses_are_uncertain_and_count(tmp_path):
    for response in (HttpResponse(200, b"not json"), HttpResponse(400, b"{}"), HttpResponse(401, b"{}")):
        t = Transport(response, openai_ok())
        store, _, drafts = make(tmp_path / str(response.status) / str(len(response.body)), paid(budget=CAP_ONE), t)
        with pytest.raises(TransportError):
            drafts.generate("openai")
        assert [r["status"] for r in rows(store)] == ["uncertain"] and len(t.requests) == 1
        store.close()


# ---- the only release: a request PROVEN never sent --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("code", ["NOT_HTTPS", "HOST_NOT_ALLOWED"])
def test_a_request_refused_before_sending_is_released_with_an_audit_record_and_never_counts_as_sent(tmp_path, code):
    t = Transport(TransportError(code), openai_ok())
    store, budget, drafts = make(tmp_path, paid(budget=CAP_ONE), t)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    row = rows(store)[0]
    assert row["status"] == "released" and row["sent_at"] is None and code in row["note"]
    events = [dict(r) for r in store.db.execute("select event, data_json from audit where event='AI_RESERVATION_RELEASED_NOT_SENT'")]
    assert len(events) == 1 and json.loads(events[0]["data_json"])["provider"] == "openai"
    spend = budget.report("2026-10-08", "2026-10-08")["openai"]
    assert (spend["requests_attempted"], spend["requests_sent"], spend["released_not_sent"], spend["requests_counted_against_cap"]) == (1, 0, 1, 0)
    assert drafts.generate("openai")["usage"] == "settled"                              # the cap of 1 is still free: nothing was sent
    store.close()


def test_a_reservation_already_marked_sent_cannot_be_released(tmp_path):
    store, budget, _ = make(tmp_path, paid(budget=CAP_ONE), NoNetwork())
    reservation = budget.reserve_ai("openai", 50, 100)
    budget.begin_send(reservation)
    assert budget.release_ai(reservation, "pretend it did not go") is False
    assert rows(store)[0]["status"] == "sent"
    with pytest.raises(BudgetExceeded):
        budget.reserve_ai("openai", 50, 100)                                            # still counts
    assert store.db.execute("select count(*) from audit where event='AI_RESERVATION_RELEASED_NOT_SENT'").fetchone()[0] == 0
    store.close()


def test_the_marker_is_committed_before_the_transport_is_called(tmp_path):
    seen = {}

    def transport(request):
        seen["status"] = [r["status"] for r in ledger(store)]
        return openai_ok()

    store, _, drafts = make(tmp_path, paid(), transport)
    drafts.generate("openai")
    assert seen["status"] == ["sent"]
    store.close()


# ---- limits hold across restarts and under concurrency -----------------------------------------------------------------------------------------
def test_the_request_cap_survives_a_restart_and_a_crash_after_sending(tmp_path):
    cfg = paid(budget=CAP_ONE)
    store, budget, _ = make(tmp_path, cfg, NoNetwork())
    reservation = budget.reserve_ai("openai", 50, 100)
    budget.begin_send(reservation)                                                      # the process dies here, before any result
    store.close()
    store2 = ResearchStore(tmp_path / "s")
    later = Budget(store2, cfg, lambda: NOW.replace(hour=23))
    with pytest.raises(BudgetExceeded):
        later.reserve_ai("openai", 50, 100)                                             # still counted before recovery
    assert later.recover_stale() == 1
    assert [r["status"] for r in rows(store2)] == ["uncertain"]
    with pytest.raises(BudgetExceeded):
        later.reserve_ai("openai", 50, 100)                                             # and after
    store2.close()


def test_twelve_concurrent_runs_never_send_more_requests_than_the_cap(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 3, "daily_ai_tokens": 100_000, "daily_cost_usd": 4.0})
    ResearchStore(tmp_path / "s").close()
    lock, sent, blocked = threading.Lock(), [], []

    def transport(request):
        with lock:
            sent.append(1)
        return HttpResponse(500, b"{}")

    def worker():
        store = ResearchStore(tmp_path / "s")
        wall = WallClock()
        try:
            ai.AIDrafts(store, cfg, Budget(store, cfg, lambda: NOW), transport, ENV, lambda: NOW, sleep=wall.sleep, wall_clock=wall).generate("openai")
        except BudgetExceeded:
            blocked.append(1)
        except TransportError:
            pass
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(sent) == 3 and len(blocked) == 9


# ---- 429 retry policy, pacing and bounded backoff with a fake clock -----------------------------------------------------------------------------
def with_retries(cfg, n, **provider):
    cfg.providers["openai"] = type(cfg.providers["openai"])(**{**cfg.providers["openai"].__dict__, "max_retries": n, **provider})
    return cfg


def r429(after=None):
    return HttpResponse(429, b"{}", {} if after is None else {"retry-after": str(after)})


def test_a_429_without_retry_after_is_not_retried(tmp_path):
    t = Transport(r429(), openai_ok())
    store, _, drafts = make(tmp_path, with_retries(paid(), 2), t)
    with pytest.raises(TransportError) as e:
        drafts.generate("openai")
    assert e.value.code == "HTTP_429" and len(t.requests) == 1 and [r["status"] for r in rows(store)] == ["rate_limited"]
    store.close()


def test_a_retry_after_longer_than_the_backoff_cap_stops_the_run_instead_of_waiting(tmp_path):
    wall = WallClock()
    t = Transport(r429(500), openai_ok())
    store, _, drafts = make(tmp_path, with_retries(paid(), 2, max_backoff_s=30.0), t, wall=wall)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    assert len(t.requests) == 1 and wall.sleeps == []
    store.close()


def test_retries_after_429_are_bounded_and_each_one_counts_against_the_cap(tmp_path):
    wall = WallClock()
    t = Transport(r429(1), r429(1), r429(1), openai_ok())
    store, _, drafts = make(tmp_path, with_retries(paid(), 1), t, wall=wall)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    assert len(t.requests) == 2 and [r["status"] for r in rows(store)] == ["rate_limited", "rate_limited"]
    store.close()
    t2 = Transport(r429(1), openai_ok())
    store2, _, drafts2 = make(tmp_path / "b", with_retries(paid(budget=CAP_ONE), 2), t2, wall=WallClock())
    with pytest.raises(BudgetExceeded):
        drafts2.generate("openai")                                                      # the retry would be a second request over a cap of 1
    assert len(t2.requests) == 1
    store2.close()


def test_backoff_is_at_least_the_retry_after_and_never_above_the_cap(tmp_path):
    wall = WallClock()
    t = Transport(r429(20), r429(0), openai_ok())
    store, _, drafts = make(tmp_path, with_retries(paid(budget={"daily_ai_requests": 3}), 2, max_backoff_s=30.0, min_interval_s=1.0), t, wall=wall)
    drafts.generate("openai")
    assert wall.sleeps[0] == 20.0 and 2.0 <= wall.sleeps[1] <= 30.0                     # second wait: exponential floor, not 0
    store.close()


def test_pacing_spaces_two_requests_and_survives_a_restart(tmp_path):
    wall = WallClock()
    t = Transport(openai_ok("one"), openai_ok("two"))
    store, _, drafts = make(tmp_path, paid(budget={"daily_ai_requests": 2}, ), t, wall=wall)
    drafts.generate("openai")
    store.close()
    store2 = ResearchStore(tmp_path / "s")
    cfg = paid(budget={"daily_ai_requests": 2})
    wall.now += 0.5
    ai.AIDrafts(store2, cfg, Budget(store2, cfg, lambda: NOW), t, ENV, lambda: NOW, sleep=wall.sleep, wall_clock=wall).generate("openai")
    assert wall.sleeps == [pytest.approx(1.5)]                                          # 2.0 s minimum gap minus the 0.5 s that had passed
    store2.close()


# ---- the report never hides a sent attempt ----------------------------------------------------------------------------------------------------
def test_the_daily_report_separates_attempted_sent_settled_uncertain_and_released(tmp_path):
    cfg = paid("openai", youtube={"enabled": False}, mode="live", budget={"daily_ai_requests": 3})
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=Transport(HttpResponse(504, b"{}")), transport=NoNetwork(), sleep=Sleeps())
    counts = report["ai_requests"]["openai"]
    assert counts["requests_attempted"] == 1 and counts["requests_sent"] == 1 and counts["uncertain"] == 1 and counts["settled"] == 0
    assert counts["requests_counted_against_cap"] == 1 and report["api_calls"]["openai"] == 1
    assert report["status"] != "SUCCESS" and report["services"]["openai"]["status"] == "failed"
    again = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=NoNetwork(), transport=NoNetwork(), sleep=Sleeps())
    assert again["services"]["openai"]["reasons"] == ["ALREADY_REQUESTED_TODAY_NOT_RETRIED"]


def test_a_version_1_database_is_upgraded_without_losing_a_possibly_sent_request(tmp_path):
    from pathlib import Path
    store = ResearchStore(tmp_path / "s")
    db = store.db
    db.execute("DROP TABLE ledger")
    db.execute("""CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, kind TEXT NOT NULL, provider TEXT NOT NULL,
        requests INTEGER NOT NULL DEFAULT 1, units INTEGER NOT NULL DEFAULT 0, tokens_reserved INTEGER NOT NULL DEFAULT 0, usd_reserved REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK (status IN ('reserved','settled','uncertain','released')), tokens_actual INTEGER, usd_actual REAL,
        created_at TEXT NOT NULL, settled_at TEXT, note TEXT)""")
    db.execute("INSERT INTO ledger(day,kind,provider,status,created_at,note) VALUES ('2026-10-08','ai','openai','released','2026-10-08T15:00:00Z','HTTP 504, not processed')")
    db.execute("INSERT INTO ledger(day,kind,provider,status,created_at,tokens_actual,usd_actual) VALUES ('2026-10-08','ai','openai','settled','2026-10-08T15:01:00Z',10,0.1)")
    db.execute("ALTER TABLE sources DROP COLUMN discovery_class")
    db.execute("ALTER TABLE sources DROP COLUMN discovery_query")
    db.execute("ALTER TABLE sources DROP COLUMN fingerprint_conflict_with")
    db.execute("ALTER TABLE reviews DROP COLUMN review_basis")
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("DROP TABLE review_items")
    db.execute("""CREATE TABLE review_items (item_id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT')),
        source_video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL,
        stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only','ai_draft','coach_reviewed')),
        review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')), current_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
        CHECK ((origin = 'YOUTUBE_SOURCE' AND source_video_id IS NOT NULL) OR (origin = 'AI_GENERAL_DRAFT' AND source_video_id IS NULL)))""")
    db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    db.commit()
    store.close()
    upgraded = ResearchStore(tmp_path / "s")
    assert upgraded.schema_version() == 5
    assert [r["status"] for r in rows(upgraded)] == ["uncertain", "settled"]            # the old "released" 504 is counted again, conservatively
    refs = [r["sql"] for r in upgraded.db.execute("select sql from sqlite_master where sql like '%review_items_v1%'")]
    assert refs == [] and upgraded.db.execute("pragma foreign_key_check").fetchall() == []   # no table was left pointing at a dropped name
    upgraded.close()
