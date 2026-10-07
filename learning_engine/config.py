"""Configuration: strict, explicit, offline by default. Unknown keys are errors; nothing is inferred or guessed.

Official facts this relies on (checked 2026-10-08, see README for the links): YouTube Data API default quota is 100
search.list calls/day plus 10,000 units/day for other endpoints; stored non-authorized API data must be refreshed or
deleted within 30 calendar days; Anthropic requires `anthropic-version` and `content-type` headers; OpenAI `store`
defaults to true when omitted. No price is built in: a provider can be enabled only with an operator-supplied price
and the page it came from.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
SCHEMA = "kemet.learning_engine.config/1"
PROVIDERS = ("openai", "anthropic")
HARD_MAX_DAILY_USD = 5.0          # no configuration can exceed this
HARD_MAX_DAILY_AI_REQUESTS = 20
YOUTUBE_DOCUMENTED_DEFAULT_SEARCH_CALLS = 100
YOUTUBE_DOCUMENTED_DEFAULT_UNITS = 10_000
YOUTUBE_RETENTION_DAYS = 30


class ConfigError(ValueError):
    pass


def _only(data: dict, allowed: set, where: str) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be an object")
    extra = set(data) - allowed
    if extra:
        raise ConfigError(f"unknown settings in {where}: {sorted(extra)}")


def _int(value, lo, hi, name) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ConfigError(f"{name} must be a whole number from {lo} to {hi}")
    return value


def _num(value, lo, hi, name) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ConfigError(f"{name} must be a number from {lo} to {hi}")
    return float(value)


def _bool(value, name) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


@dataclass(frozen=True)
class Channel:
    channel_id: str
    label: str
    basis_for_allowing: str
    reviewed_by: str
    reviewed_on: str


@dataclass(frozen=True)
class YouTubeConfig:
    enabled: bool = False
    channels: tuple = ()
    query: str = "football dribbling drills"
    query_version: str = "dribbling-v1"
    results_per_page: int = 10
    max_pages_per_channel: int = 1
    search_calls_per_day: int = 6
    units_per_day: int = 100
    details_batch: int = 20
    timeout_s: float = 15.0
    min_interval_s: float = 2.0
    max_retries: int = 2
    backoff_base_s: float = 2.0
    max_response_bytes: int = 262_144
    api_key_env: str = "KEMET_LE_YOUTUBE_API_KEY"
    api_key_transport: str = "header"


@dataclass(frozen=True)
class Pricing:
    input_usd_per_million: float
    output_usd_per_million: float
    source_url: str
    verified_on: str


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    model: str | None = None
    api_key_env: str | None = None
    max_input_tokens: int = 400
    max_output_tokens: int = 500
    max_call_usd: float = 0.0
    pricing: Pricing | None = None
    timeout_s: float = 30.0
    max_retries: int = 1


@dataclass(frozen=True)
class BudgetConfig:
    daily_ai_requests: int = 4
    daily_ai_tokens: int = 8_000
    daily_cost_usd: float = 0.0
    paid_execution_approved_by: str | None = None
    paid_execution_approved_on: str | None = None
    reservation_stale_minutes: int = 30


@dataclass(frozen=True)
class RetentionConfig:
    youtube_data_days: int = YOUTUBE_RETENTION_DAYS
    refresh_window_days: int = 3
    reports_days: int = 90


@dataclass(frozen=True)
class Config:
    mode: str = "offline"
    network_enabled: bool = False
    timezone: str = "America/Detroit"
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    providers: dict = field(default_factory=dict)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)


def parse_config(data: dict) -> Config:
    _only(data, {"schema", "mode", "network_enabled", "timezone", "youtube", "providers", "budget", "retention"}, "config")
    if data.get("schema") != SCHEMA:
        raise ConfigError(f"config schema must be {SCHEMA}")
    mode = data.get("mode", "offline")
    if mode not in ("offline", "live"):
        raise ConfigError("mode must be offline or live")
    tz = data.get("timezone", "America/Detroit")
    try:
        ZoneInfo(tz)
    except Exception as exc:
        raise ConfigError(f"unknown timezone {tz!r}") from exc

    y = data.get("youtube", {})
    _only(y, {"enabled", "channels", "query", "query_version", "results_per_page", "max_pages_per_channel", "search_calls_per_day",
              "units_per_day", "details_batch", "timeout_s", "min_interval_s", "max_retries", "backoff_base_s", "max_response_bytes",
              "api_key_env", "api_key_transport"}, "youtube")
    channels = []
    for i, ch in enumerate(y.get("channels", [])):
        _only(ch, {"channel_id", "label", "basis_for_allowing", "reviewed_by", "reviewed_on"}, f"youtube.channels[{i}]")
        if not CHANNEL_ID.match(str(ch.get("channel_id", ""))):
            raise ConfigError(f"youtube.channels[{i}].channel_id must be a YouTube channel id (UC...)")
        for key in ("label", "basis_for_allowing", "reviewed_by"):
            if not isinstance(ch.get(key), str) or len(ch[key].strip()) < 5:
                raise ConfigError(f"youtube.channels[{i}].{key} is required: why this channel may be used, and who checked")
        try:
            date.fromisoformat(ch.get("reviewed_on", ""))
        except ValueError as exc:
            raise ConfigError(f"youtube.channels[{i}].reviewed_on must be YYYY-MM-DD") from exc
        channels.append(Channel(ch["channel_id"], ch["label"], ch["basis_for_allowing"], ch["reviewed_by"], ch["reviewed_on"]))
    if len({c.channel_id for c in channels}) != len(channels):
        raise ConfigError("duplicate channel ids in the allowlist")
    transport = y.get("api_key_transport", "header")
    if transport not in ("header", "query"):
        raise ConfigError("youtube.api_key_transport must be header or query")
    env = y.get("api_key_env", "KEMET_LE_YOUTUBE_API_KEY")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", env):
        raise ConfigError("youtube.api_key_env must be an environment variable NAME")
    query = y.get("query", "football dribbling drills")
    if not isinstance(query, str) or not 3 <= len(query) <= 100 or "dribbl" not in query.lower():
        raise ConfigError("youtube.query must be a short Dribbling search (3-100 characters)")
    yt = YouTubeConfig(
        enabled=_bool(y.get("enabled", False), "youtube.enabled"), channels=tuple(channels), query=query,
        query_version=str(y.get("query_version", "dribbling-v1"))[:40],
        results_per_page=_int(y.get("results_per_page", 10), 1, 50, "youtube.results_per_page"),
        max_pages_per_channel=_int(y.get("max_pages_per_channel", 1), 1, 3, "youtube.max_pages_per_channel"),
        search_calls_per_day=_int(y.get("search_calls_per_day", 6), 0, YOUTUBE_DOCUMENTED_DEFAULT_SEARCH_CALLS, "youtube.search_calls_per_day"),
        units_per_day=_int(y.get("units_per_day", 100), 0, YOUTUBE_DOCUMENTED_DEFAULT_UNITS, "youtube.units_per_day"),
        details_batch=_int(y.get("details_batch", 20), 1, 50, "youtube.details_batch"),
        timeout_s=_num(y.get("timeout_s", 15.0), 1, 60, "youtube.timeout_s"),
        min_interval_s=_num(y.get("min_interval_s", 2.0), 0.5, 600, "youtube.min_interval_s"),
        max_retries=_int(y.get("max_retries", 2), 0, 3, "youtube.max_retries"),
        backoff_base_s=_num(y.get("backoff_base_s", 2.0), 0.1, 60, "youtube.backoff_base_s"),
        max_response_bytes=_int(y.get("max_response_bytes", 262_144), 1_024, 1_000_000, "youtube.max_response_bytes"),
        api_key_env=env, api_key_transport=transport)

    providers = {}
    p_all = data.get("providers", {})
    _only(p_all, set(PROVIDERS), "providers")
    for name, p in p_all.items():
        _only(p, {"enabled", "model", "api_key_env", "max_input_tokens", "max_output_tokens", "max_call_usd", "pricing", "timeout_s", "max_retries"},
              f"providers.{name}")
        pricing = None
        if p.get("pricing") is not None:
            pr = p["pricing"]
            _only(pr, {"input_usd_per_million", "output_usd_per_million", "source_url", "verified_on"}, f"providers.{name}.pricing")
            if not str(pr.get("source_url", "")).startswith("https://"):
                raise ConfigError(f"providers.{name}.pricing.source_url must be the https page the price came from")
            try:
                date.fromisoformat(pr.get("verified_on", ""))
            except ValueError as exc:
                raise ConfigError(f"providers.{name}.pricing.verified_on must be YYYY-MM-DD") from exc
            pricing = Pricing(_num(pr.get("input_usd_per_million"), 0, 100_000, "input price"),
                              _num(pr.get("output_usd_per_million"), 0, 100_000, "output price"), pr["source_url"], pr["verified_on"])
        key_env = p.get("api_key_env")
        if key_env is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", key_env):
            raise ConfigError(f"providers.{name}.api_key_env must be an environment variable NAME")
        providers[name] = ProviderConfig(
            enabled=_bool(p.get("enabled", False), f"providers.{name}.enabled"),
            model=p.get("model") if isinstance(p.get("model"), str) and p.get("model").strip() else None,
            api_key_env=key_env, max_input_tokens=_int(p.get("max_input_tokens", 400), 1, 2_000, f"providers.{name}.max_input_tokens"),
            max_output_tokens=_int(p.get("max_output_tokens", 500), 1, 1_500, f"providers.{name}.max_output_tokens"),
            max_call_usd=_num(p.get("max_call_usd", 0.0), 0, HARD_MAX_DAILY_USD, f"providers.{name}.max_call_usd"), pricing=pricing,
            timeout_s=_num(p.get("timeout_s", 30.0), 1, 120, f"providers.{name}.timeout_s"),
            max_retries=_int(p.get("max_retries", 1), 0, 2, f"providers.{name}.max_retries"))

    b = data.get("budget", {})
    _only(b, {"daily_ai_requests", "daily_ai_tokens", "daily_cost_usd", "paid_execution_approved_by", "paid_execution_approved_on",
              "reservation_stale_minutes"}, "budget")
    budget = BudgetConfig(
        daily_ai_requests=_int(b.get("daily_ai_requests", 4), 0, HARD_MAX_DAILY_AI_REQUESTS, "budget.daily_ai_requests"),
        daily_ai_tokens=_int(b.get("daily_ai_tokens", 8_000), 0, 200_000, "budget.daily_ai_tokens"),
        daily_cost_usd=_num(b.get("daily_cost_usd", 0.0), 0, HARD_MAX_DAILY_USD, "budget.daily_cost_usd"),
        paid_execution_approved_by=b.get("paid_execution_approved_by"), paid_execution_approved_on=b.get("paid_execution_approved_on"),
        reservation_stale_minutes=_int(b.get("reservation_stale_minutes", 30), 1, 1_440, "budget.reservation_stale_minutes"))
    r = data.get("retention", {})
    _only(r, {"youtube_data_days", "refresh_window_days", "reports_days"}, "retention")
    retention = RetentionConfig(
        youtube_data_days=_int(r.get("youtube_data_days", YOUTUBE_RETENTION_DAYS), 1, YOUTUBE_RETENTION_DAYS, "retention.youtube_data_days"),
        refresh_window_days=_int(r.get("refresh_window_days", 3), 0, 14, "retention.refresh_window_days"),
        reports_days=_int(r.get("reports_days", 90), 7, 3650, "retention.reports_days"))
    if retention.refresh_window_days >= retention.youtube_data_days:
        raise ConfigError("retention.refresh_window_days must be shorter than retention.youtube_data_days")
    return Config(mode=mode, network_enabled=_bool(data.get("network_enabled", False), "network_enabled"), timezone=tz,
                  youtube=yt, providers=providers, budget=budget, retention=retention)


def load_config(path: Path) -> Config:
    path = Path(path)
    if path.is_symlink():
        raise ConfigError("the config file must not be a symbolic link")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read the config file: {type(exc).__name__}") from exc
    return parse_config(data)


def youtube_blockers(config: Config, env: dict) -> list[str]:
    """Why a LIVE YouTube discovery may not run now. Empty means it may."""
    out = []
    if config.mode != "live":
        out.append("MODE_IS_OFFLINE")
    if not config.network_enabled:
        out.append("NETWORK_DISABLED")
    if not config.youtube.enabled:
        out.append("YOUTUBE_NOT_ENABLED")
    if not config.youtube.channels:
        out.append("NO_CHANNEL_ALLOWLIST")
    if not env.get(config.youtube.api_key_env):
        out.append("YOUTUBE_API_KEY_NOT_SET")
    if config.youtube.search_calls_per_day < 1 or config.youtube.units_per_day < 1:
        out.append("NO_YOUTUBE_QUOTA_BUDGET")
    return out


def paid_blockers(config: Config, provider: str, env: dict) -> list[str]:
    """Why a PAID AI call may not run now. Every one of these must be absent first; nothing in this module has a way
    around them."""
    out = []
    p = config.providers.get(provider)
    if p is None:
        return ["PROVIDER_NOT_CONFIGURED"]
    if config.mode != "live":
        out.append("MODE_IS_OFFLINE")
    if not config.network_enabled:
        out.append("NETWORK_DISABLED")
    if not p.enabled:
        out.append("PROVIDER_NOT_ENABLED")
    if not p.model:
        out.append("MODEL_NOT_CONFIGURED")
    if not p.api_key_env or not env.get(p.api_key_env):
        out.append("API_KEY_NOT_SET")
    if p.pricing is None:
        out.append("PRICING_NOT_CONFIGURED_WITH_SOURCE")
    if p.max_call_usd <= 0:
        out.append("PER_CALL_COST_CAP_NOT_SET")
    b = config.budget
    if b.daily_cost_usd <= 0 or b.daily_ai_requests < 1 or b.daily_ai_tokens < 1:
        out.append("DAILY_BUDGET_NOT_SET")
    if not b.paid_execution_approved_by or not b.paid_execution_approved_on:
        out.append("COST_APPROVAL_NOT_RECORDED")
    return out
