"""Learning engine: cost reservations, the AI adapters (disabled by default, never given children's data), and failure handling."""
import json
import threading

import pytest

from learning_engine import ai
from learning_engine.budget import Budget, BudgetExceeded
from learning_engine.daily import run_daily
from learning_engine.http import HttpResponse, TransportError
from learning_engine.store import ResearchStore
from tests.le_helpers import FAKE_KEY, NOW, NoNetwork, PAID_BUDGET, Sleeps, config, ok, paid_provider_dict

ENV = {"KEMET_LE_OPENAI_API_KEY": FAKE_KEY, "KEMET_LE_ANTHROPIC_API_KEY": FAKE_KEY}


def paid(only=None, **kw):
    providers = {**paid_provider_dict("openai"), **paid_provider_dict("anthropic")}
    if only:
        providers = {only: providers[only]}
    return config(providers=providers, budget={**PAID_BUDGET, **kw.pop("budget", {})}, **kw)


class Transport:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def openai_ok(text="A generic drill.", usage=True):
    body = {"output": [{"content": [{"type": "output_text", "text": text}]}]}
    if usage:
        body["usage"] = {"input_tokens": 90, "output_tokens": 120}
    return HttpResponse(200, json.dumps(body).encode())


def anthropic_ok(text="A generic drill.", usage=True):
    body = {"content": [{"type": "text", "text": text}]}
    if usage:
        body["usage"] = {"input_tokens": 80, "output_tokens": 100}
    return HttpResponse(200, json.dumps(body).encode())


class WallClock:
    """A fake wall clock whose fake sleep advances it, so pacing and backoff are tested without waiting."""

    def __init__(self, start=1000.0):
        self.now, self.sleeps = start, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make(tmp_path, cfg, transport, env=ENV, wall=None):
    store = ResearchStore(tmp_path / "s")
    budget = Budget(store, cfg, lambda: NOW)
    wall = wall or WallClock()
    return store, budget, ai.AIDrafts(store, cfg, budget, transport, env, lambda: NOW, sleep=wall.sleep, wall_clock=wall)


def ledger(store):
    return [dict(r) for r in store.db.execute("select kind, provider, status, tokens_reserved, usd_reserved, tokens_actual, usd_actual from ledger where kind='ai'")]


# ---- disabled by default ----------------------------------------------------------------------------------------------------------------
def test_by_default_no_paid_provider_is_called_even_with_keys_in_the_environment(tmp_path):
    report = run_daily(config(mode="offline", network_enabled=False), tmp_path / "state", env=ENV, now=NOW, transport=NoNetwork(), ai_transport=NoNetwork())
    assert report["api_calls"]["openai"] == 0 and report["api_calls"]["anthropic"] == 0
    assert report["services"]["openai"]["reasons"] == ["PROVIDER_NOT_CONFIGURED"] == report["services"]["anthropic"]["reasons"]


def test_a_configured_but_not_approved_provider_is_skipped_with_the_reasons_and_sends_nothing(tmp_path):
    cfg = config(providers=paid_provider_dict("openai"), budget={})          # priced and enabled, but no cost approval and no budget
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, transport=NoNetwork(), ai_transport=NoNetwork(), sleep=Sleeps())
    reasons = report["services"]["openai"]["reasons"]
    assert "COST_APPROVAL_NOT_RECORDED" in reasons and "DAILY_BUDGET_NOT_SET" in reasons and report["api_calls"]["openai"] == 0


def test_generate_refuses_before_any_request_when_a_gate_is_closed(tmp_path):
    for cfg, env in ((paid(budget={"paid_execution_approved_by": None}), ENV), (paid(), {}), (config(), ENV)):
        store, _, drafts = make(tmp_path / str(id(cfg)), cfg, NoNetwork(), env)
        with pytest.raises(ai.ProviderDisabled):
            drafts.generate("openai")
        assert ledger(store) == []
        store.close()


# ---- what is sent -----------------------------------------------------------------------------------------------------------------------
def test_only_the_fixed_generic_prompt_can_ever_be_sent():
    for provider in ("openai", "anthropic"):
        req = ai.build_request(provider, "m", 100, "KEY")
        assert json.loads(req.body)["input" if provider == "openai" else "messages"] in (ai.PROMPT, [{"role": "user", "content": ai.PROMPT}])
    for hostile in ("Describe the 8-year-old player Karim in this video", ai.PROMPT + " The player is AP-0123456789abcdef", ""):
        with pytest.raises(ai.PayloadRefused):
            ai.build_request("openai", "m", 100, "KEY", prompt=hostile)
    with pytest.raises(ai.PayloadRefused):
        ai.build_request("gemini", "m", 100, "KEY")


def test_the_fixed_prompt_contains_no_child_data_source_text_ids_or_metadata():
    import re
    assert not re.search(r"(AP-|CR-|AC-|AV-)[0-9a-f]{16}|@|https?://|\d{4}-\d{2}-\d{2}|player_id|UC[A-Za-z0-9_-]{22}", ai.PROMPT)
    assert "Do not cite or describe any specific video, person or source" in ai.PROMPT                        # it forbids, never names
    for forbidden in ("age", "year-old", "rating", "score", "name"):
        assert forbidden not in ai.PROMPT.lower().replace("do not claim the drill is suitable for any particular age", "").replace("any particular age", "")


def test_requests_follow_the_documented_shapes_and_openai_storage_is_switched_off():
    o = ai.build_request("openai", "the-model", 300, "KEY")
    assert o.url == "https://api.openai.com/v1/responses" and o.method == "POST" and o.headers["Authorization"] == "Bearer KEY"
    body = json.loads(o.body)
    assert body == {"model": "the-model", "input": ai.PROMPT, "max_output_tokens": 300, "store": False}
    a = ai.build_request("anthropic", "the-model", 300, "KEY")
    assert a.url == "https://api.anthropic.com/v1/messages" and a.headers["anthropic-version"] == "2023-06-01" and a.headers["content-type"] == "application/json"
    assert json.loads(a.body) == {"model": "the-model", "max_tokens": 300, "messages": [{"role": "user", "content": ai.PROMPT}]}


def test_the_model_name_comes_only_from_the_operator_configuration():
    import pathlib
    source = "".join(p.read_text() for p in pathlib.Path("learning_engine").glob("*.py"))
    for guess in ("gpt-", "claude-", "o1", "o3", "sonnet", "opus", "haiku", "gemini"):
        assert guess not in source.lower().replace("claude's", ""), guess
    assert not any(guess in pathlib.Path("learning_engine/config.example.json").read_text().lower() for guess in ("gpt", "claude"))


# ---- reservation, settlement, uncertainty -------------------------------------------------------------------------------------------------
def test_a_call_reserves_first_settles_with_real_usage_and_stores_an_unverified_unattached_draft(tmp_path):
    cfg = paid()
    t = Transport(openai_ok())
    store, budget, drafts = make(tmp_path, cfg, t)
    out = drafts.generate("openai")
    row = ledger(store)[0]
    assert row["status"] == "settled" and row["tokens_actual"] == 210 and row["usd_actual"] == pytest.approx(90 / 1e6 * 1.0 + 120 / 1e6 * 2.0, abs=1e-7)
    assert row["tokens_reserved"] == ai.estimate_tokens(ai.PROMPT) + 300 and row["usd_reserved"] > row["usd_actual"]
    item = dict(store.db.execute("select * from review_items where item_id=?", (out["item_id"],)).fetchone())
    assert item["origin"] == "AI_GENERAL_DRAFT" and item["source_video_id"] is None and item["stage"] == "ai_draft" and item["review_status"] == "pending" and item["published"] == 0
    version = dict(store.db.execute("select * from item_versions where item_id=?", (out["item_id"],)).fetchone())
    content = json.loads(version["content_json"])
    assert content["unverified"] is True and content["source_analysis"] is False and version["author_kind"] == "AI_DRAFT"
    store.close()


def test_anthropic_responses_are_parsed_and_settled_too(tmp_path):
    store, _, drafts = make(tmp_path, paid(), Transport(anthropic_ok()))
    drafts.generate("anthropic")
    assert ledger(store)[0]["tokens_actual"] == 180
    store.close()


def test_a_response_without_usage_is_uncertain_and_its_reserved_cost_stays_counted(tmp_path):
    store, budget, drafts = make(tmp_path, paid(), Transport(openai_ok(usage=False)))
    assert drafts.generate("openai")["usage"] == "uncertain"
    row = ledger(store)[0]
    assert row["status"] == "uncertain" and row["tokens_actual"] is None
    with store.transaction() as db:
        assert budget._ai_totals(db, "2026-10-08")[1] == row["tokens_reserved"]
    store.close()


def test_a_timeout_is_uncertain_and_is_never_retried_because_it_might_have_been_charged(tmp_path):
    t = Transport(TransportError("TIMEOUT"), openai_ok())
    store, _, drafts = make(tmp_path, paid(), t)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    assert len(t.requests) == 1 and [r["status"] for r in ledger(store)] == ["uncertain"]
    store.close()


def test_a_429_with_retry_after_is_retried_within_the_limits_and_each_attempt_counts(tmp_path):
    wall = WallClock()
    t = Transport(HttpResponse(429, b"{}", {"retry-after": "3"}), openai_ok())
    store, _, drafts = make(tmp_path, paid(), t, wall=wall)
    assert drafts.generate("openai")["usage"] == "settled"
    assert len(t.requests) == 2 and [r["status"] for r in ledger(store)] == ["rate_limited", "settled"]
    assert wall.sleeps == [3.0]                                    # waited for the provider's Retry-After; the 2 s pacing gap had already passed
    store.close()


def test_a_server_error_is_never_retried_automatically(tmp_path):
    t2 = Transport(HttpResponse(500, b"{}"), HttpResponse(500, b"{}"), HttpResponse(500, b"{}"))
    store2, _, drafts2 = make(tmp_path / "b", paid(), t2)
    with pytest.raises(TransportError):
        drafts2.generate("openai")
    assert len(t2.requests) == 1 and [r["status"] for r in ledger(store2)] == ["uncertain"]
    store2.close()


def test_an_unexpected_status_is_uncertain_not_retried(tmp_path):
    t = Transport(HttpResponse(400, b"{}"), openai_ok())
    store, _, drafts = make(tmp_path, paid(), t)
    with pytest.raises(TransportError):
        drafts.generate("openai")
    assert len(t.requests) == 1 and ledger(store)[0]["status"] == "uncertain"
    store.close()


# ---- limits ------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("budget,expected", [
    ({"daily_ai_requests": 1}, "AI_REQUESTS_PER_DAY_LIMIT"), ({"daily_ai_tokens": 500}, "AI_TOKENS_PER_DAY_LIMIT"), ({"daily_cost_usd": 0.0012}, "AI_COST_PER_DAY_LIMIT")])
def test_each_daily_limit_stops_the_next_call_before_it_is_sent(tmp_path, budget, expected):
    t = Transport(openai_ok(), openai_ok("another"), openai_ok("third"))
    store, _, drafts = make(tmp_path, paid(budget=budget), t)
    sent = 0
    with pytest.raises(BudgetExceeded) as e:
        for _ in range(3):
            drafts.generate("openai"); sent += 1
    assert e.value.reason == expected and len(t.requests) == sent
    store.close()


def test_per_call_ceilings_and_missing_prices_refuse_before_sending(tmp_path):
    cheap = paid(); cheap.providers["openai"] = type(cheap.providers["openai"])(**{**cheap.providers["openai"].__dict__, "max_call_usd": 0.0000001})
    store, _, drafts = make(tmp_path, cheap, Transport(openai_ok()))
    with pytest.raises(BudgetExceeded) as e:
        drafts.generate("openai")
    assert e.value.reason == "COST_PER_CALL_CEILING" and ledger(store) == []
    store.close()


def test_the_reservations_survive_a_restart_and_stale_ones_become_uncertain_and_stay_counted(tmp_path):
    cfg = paid()
    store, budget, _ = make(tmp_path, cfg, NoNetwork())
    res = budget.reserve_ai("openai", 50, 300)
    store.close()
    store2 = ResearchStore(tmp_path / "s")
    later = Budget(store2, cfg, lambda: NOW.replace(hour=23))
    assert later.recover_stale() == 1
    assert ledger(store2)[0]["status"] == "uncertain"
    with store2.transaction() as db:
        assert later._ai_totals(db, "2026-10-08")[0] == 1                                                     # still counted
    store2.close()


def test_concurrent_ai_reservations_never_exceed_the_daily_request_limit(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 3, "daily_ai_tokens": 100_000, "daily_cost_usd": 4.0})
    ResearchStore(tmp_path / "s").close()
    got, refused = [], []

    def worker():
        store = ResearchStore(tmp_path / "s")
        try:
            Budget(store, cfg, lambda: NOW).reserve_ai("openai", 50, 100); got.append(1)
        except BudgetExceeded:
            refused.append(1)
        finally:
            store.close()

    ts = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(got) == 3 and len(refused) == 9


def test_a_released_reservation_no_longer_counts(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 1})
    store, budget, _ = make(tmp_path, cfg, NoNetwork())
    r = budget.reserve_ai("openai", 50, 100)
    with pytest.raises(BudgetExceeded):
        budget.reserve_ai("openai", 50, 100)
    budget.release_ai(r, "never sent")
    budget.reserve_ai("openai", 50, 100)
    store.close()


# ---- the daily run with AI gates open (fake transport, fake key) ---------------------------------------------------------------------------
def test_the_daily_run_makes_at_most_one_general_draft_per_provider_per_day_and_reports_it(tmp_path):
    t = Transport(openai_ok())
    cfg = paid("openai", youtube={"enabled": False}, mode="live")
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=t, transport=NoNetwork(), sleep=Sleeps())
    assert report["services"]["openai"]["status"] == "ok" and report["api_calls"]["openai"] == 1 and report["stages"]["ai_draft"] == 1
    assert report["tokens"]["openai"]["actual_reported"] == 210 and report["cost_usd"]["openai"]["reserved"] > 0
    assert "not a guarantee" in report["cost_note"]
    again = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=Transport(), transport=NoNetwork(), sleep=Sleeps())
    assert again["api_calls"]["openai"] == 1                                                              # nothing repeated
    assert report["services"]["anthropic"] == {"status": "skipped", "reasons": ["PROVIDER_NOT_CONFIGURED"]}


def test_a_failed_paid_call_makes_the_day_partial_never_success(tmp_path):
    cfg = paid("openai", youtube={"enabled": False}, mode="live")
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=Transport(TransportError("TIMEOUT"), TransportError("TIMEOUT")), transport=NoNetwork(), sleep=Sleeps())
    assert report["status"] != "SUCCESS" and {"where": "openai", "code": "TRANSPORT_TIMEOUT"} in report["errors"]


def test_an_unexpected_crash_inside_a_step_is_reported_by_type_only_and_never_hides_behind_success(tmp_path):
    class Broken:
        def __call__(self, request):
            raise RuntimeError(f"surprise containing {FAKE_KEY}")
    cfg = paid("openai", youtube={"enabled": False}, mode="live")
    report = run_daily(cfg, tmp_path / "state", env=ENV, now=NOW, ai_transport=Broken(), transport=NoNetwork(), sleep=Sleeps())
    assert report["status"] != "SUCCESS" and {"where": "openai", "code": "UNEXPECTED_RuntimeError"} in report["errors"]
    assert FAKE_KEY not in json.dumps(report)
