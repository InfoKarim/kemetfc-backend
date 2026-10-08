"""Hermetic helpers for the learning-engine tests: fake transports, builders. No network, no keys, no real data."""
import json
from datetime import datetime, timezone

from learning_engine.config import parse_config
from learning_engine.http import HttpResponse, TransportError

CH1 = "UC" + "a" * 22
CH2 = "UC" + "b" * 22
FAKE_KEY = "FAKE-KEY-0123456789-NOT-A-REAL-KEY"
NOW = datetime(2026, 10, 8, 15, 0, 0, tzinfo=timezone.utc)


def config_dict(**overrides):
    base = {"schema": "kemet.learning_engine.config/1", "mode": "live", "network_enabled": True, "timezone": "America/Detroit",
            "youtube": {"enabled": True, "channels": [{"channel_id": CH1, "label": "Test Channel One", "basis_for_allowing": "synthetic test channel, no real source",
                                                       "reviewed_by": "tester", "reviewed_on": "2026-10-01"}],
                        "min_interval_s": 0.5, "search_calls_per_day": 6, "units_per_day": 20, "max_pages_per_channel": 2, "results_per_page": 5,
                        "max_retries": 2, "backoff_base_s": 1.0, "details_batch": 5},
            "providers": {}, "budget": {}, "retention": {}}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def config(**overrides):
    return parse_config(config_dict(**overrides))


def paid_provider_dict(name="openai", **extra):
    return {name: {"enabled": True, "model": "model-name-set-by-operator", "api_key_env": f"KEMET_LE_{name.upper()}_API_KEY",
                   "max_input_tokens": 400, "max_output_tokens": 300, "max_call_usd": 0.5,
                   "pricing": {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0, "source_url": "https://example.test/pricing", "verified_on": "2026-10-01"},
                   **extra}}


PAID_BUDGET = {"daily_ai_requests": 3, "daily_ai_tokens": 3000, "daily_cost_usd": 1.0, "paid_execution_approved_by": "owner (test)",
               "paid_execution_approved_on": "2026-10-01"}


def vid(n: int) -> str:
    return f"vid{n:08d}"[-11:].ljust(11, "x")[:11]


def search_item(n, channel=CH1, title=None):
    return {"id": {"kind": "youtube#video", "videoId": vid(n)}, "snippet": {"channelId": channel, "title": title or f"Dribbling drill number {n}", "publishedAt": "2026-09-01T00:00:00Z"}}


def video_item(n, channel=CH1, privacy="public", kids=False, duration="PT5M30S", live="none", title=None, published="2026-09-01T00:00:00Z"):
    return {"id": vid(n), "snippet": {"channelId": channel, "title": title or f"Dribbling drill number {n}", "publishedAt": published, "liveBroadcastContent": live},
            "contentDetails": {"duration": duration}, "status": {"privacyStatus": privacy, "madeForKids": kids}}


def ok(payload):
    return HttpResponse(200, json.dumps(payload).encode())


def err(status, reason=None, text="secret-ish error text"):
    body = {"error": {"code": status, "message": text, "errors": [{"reason": reason}] if reason else []}}
    return HttpResponse(status, json.dumps(body).encode())


class FakeTransport:
    """Scripted responses per endpoint in order; records every request. A script entry may be an HttpResponse or an Exception."""

    def __init__(self, search=(), videos=()):
        self.script = {"search": list(search), "videos": list(videos)}
        self.requests = []

    def __call__(self, request):
        endpoint = request.url.split("/youtube/v3/")[1].split("?")[0]
        self.requests.append(request)
        queue = self.script[endpoint]
        if not queue:
            raise AssertionError(f"unexpected {endpoint} request")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NoNetwork:
    def __call__(self, request):
        raise AssertionError("a network request was made")


def fixed_clock(now=NOW):
    return lambda: now


class Sleeps:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def iid(state_dir, n):
    """The review item that rests on video number n. Item ids are random (they must not contain the video id), so tests look them up."""
    from pathlib import Path
    from learning_engine.store import ResearchStore
    store = ResearchStore(Path(state_dir))
    try:
        row = store.db.execute("SELECT item_id FROM review_items WHERE source_video_id=?", (vid(n),)).fetchone()
        return row["item_id"] if row else None
    finally:
        store.close()
