"""Daily limits, reserved BEFORE any request is sent, in the research database, safe under concurrency and restarts.

* YouTube: request counts and quota units are tracked separately (search.list has its own documented daily bucket).
  The quota day is the Pacific-time day, because that is when Google resets quota (determine_quota_cost page).
* AI: every call reserves its worst-case tokens and cost first. After the call the reservation is settled with the real
  usage if the provider reported it; if the outcome is uncertain (timeout, no usage) the reserved amount stays counted and
  the call is NEVER retried blindly. These figures come from operator-set ceilings and prices: they are a reservation
  and an estimate, not a guarantee of what a provider will invoice.
"""
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from learning_engine.config import Config
from learning_engine.store import ResearchStore, iso, utc_now

PACIFIC = ZoneInfo("America/Los_Angeles")


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def quota_day(now: datetime) -> str:
    """The Pacific-time date: YouTube quota resets at midnight Pacific Time."""
    return now.astimezone(PACIFIC).date().isoformat()


def engine_day(now: datetime, timezone_name: str) -> str:
    return now.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _up(value: float) -> float:
    return math.ceil(value * 1e8) / 1e8


@dataclass(frozen=True)
class Reservation:
    id: int
    provider: str
    tokens: int
    usd: float


class Budget:
    def __init__(self, store: ResearchStore, config: Config, clock=utc_now):
        self.store, self.config, self.clock = store, config, clock

    # ---- YouTube --------------------------------------------------------------------------------------------------------
    def reserve_youtube(self, kind: str) -> None:
        """Count one request attempt (cost 1 unit each, as documented) or refuse. kind: youtube_search | youtube_other."""
        now = self.clock()
        day = quota_day(now)
        yt = self.config.youtube
        with self.store.transaction() as db:
            searches = db.execute("SELECT COUNT(*) FROM ledger WHERE day=? AND kind='youtube_search' AND status<>'released'", (day,)).fetchone()[0]
            units = db.execute("SELECT COALESCE(SUM(units),0) FROM ledger WHERE day=? AND kind IN ('youtube_search','youtube_other') AND status<>'released'", (day,)).fetchone()[0]
            if kind == "youtube_search" and searches + 1 > yt.search_calls_per_day:
                raise BudgetExceeded("YOUTUBE_SEARCH_CALLS_PER_DAY_LIMIT")
            if units + 1 > yt.units_per_day:
                raise BudgetExceeded("YOUTUBE_UNITS_PER_DAY_LIMIT")
            db.execute("INSERT INTO ledger(day,kind,provider,requests,units,status,created_at,settled_at) VALUES (?,?,?,?,?,?,?,?)",
                       (day, kind, "youtube", 1, 1, "settled", iso(now), iso(now)))

    # ---- AI -------------------------------------------------------------------------------------------------------------------
    def _ai_totals(self, db, day: str) -> tuple[int, int, float]:
        rows = db.execute("SELECT status, requests, tokens_reserved, usd_reserved, tokens_actual, usd_actual FROM ledger "
                          "WHERE day=? AND kind='ai' AND status<>'released'", (day,)).fetchall()
        requests = sum(r["requests"] for r in rows)
        tokens = sum((r["tokens_actual"] if r["status"] == "settled" and r["tokens_actual"] is not None else r["tokens_reserved"]) for r in rows)
        usd = sum((r["usd_actual"] if r["status"] == "settled" and r["usd_actual"] is not None else r["usd_reserved"]) for r in rows)
        return requests, tokens, usd

    def reserve_ai(self, provider: str, est_input_tokens: int, max_output_tokens: int) -> Reservation:
        p = self.config.providers[provider]
        if p.pricing is None:
            raise BudgetExceeded("PRICING_NOT_CONFIGURED")
        if est_input_tokens > p.max_input_tokens or max_output_tokens > p.max_output_tokens:
            raise BudgetExceeded("TOKEN_LIMIT_PER_CALL")
        tokens = est_input_tokens + max_output_tokens
        usd = _up(est_input_tokens * p.pricing.input_usd_per_million / 1e6 + max_output_tokens * p.pricing.output_usd_per_million / 1e6)
        if usd > p.max_call_usd:
            raise BudgetExceeded("COST_PER_CALL_CEILING")
        now = self.clock()
        day = engine_day(now, self.config.timezone)
        b = self.config.budget
        with self.store.transaction() as db:
            requests, used_tokens, used_usd = self._ai_totals(db, day)
            if requests + 1 > b.daily_ai_requests:
                raise BudgetExceeded("AI_REQUESTS_PER_DAY_LIMIT")
            if used_tokens + tokens > b.daily_ai_tokens:
                raise BudgetExceeded("AI_TOKENS_PER_DAY_LIMIT")
            if used_usd + usd > b.daily_cost_usd + 1e-12:
                raise BudgetExceeded("AI_COST_PER_DAY_LIMIT")
            cur = db.execute("INSERT INTO ledger(day,kind,provider,requests,tokens_reserved,usd_reserved,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                             (day, "ai", provider, 1, tokens, usd, "reserved", iso(now)))
            return Reservation(cur.lastrowid, provider, tokens, usd)

    def settle_ai(self, reservation: Reservation, input_tokens: int | None, output_tokens: int | None) -> str:
        """Record the real usage. Without usage numbers the call is UNCERTAIN and keeps its reserved amount counted."""
        now = self.clock()
        p = self.config.providers[reservation.provider]
        with self.store.transaction() as db:
            if input_tokens is None or output_tokens is None or not isinstance(input_tokens, int) or not isinstance(output_tokens, int) or p.pricing is None:
                db.execute("UPDATE ledger SET status='uncertain', settled_at=?, note=? WHERE id=?", (iso(now), "provider did not report usage", reservation.id))
                return "uncertain"
            usd = _up(input_tokens * p.pricing.input_usd_per_million / 1e6 + output_tokens * p.pricing.output_usd_per_million / 1e6)
            db.execute("UPDATE ledger SET status='settled', tokens_actual=?, usd_actual=?, settled_at=? WHERE id=?",
                       (input_tokens + output_tokens, usd, iso(now), reservation.id))
            return "settled"

    def mark_uncertain(self, reservation: Reservation, note: str) -> None:
        with self.store.transaction() as db:
            db.execute("UPDATE ledger SET status='uncertain', settled_at=?, note=? WHERE id=?", (iso(self.clock()), note[:80], reservation.id))

    def release_ai(self, reservation: Reservation, note: str) -> None:
        """Only for a request that was DEFINITELY never sent."""
        with self.store.transaction() as db:
            db.execute("UPDATE ledger SET status='released', settled_at=?, note=? WHERE id=? AND status='reserved'",
                       (iso(self.clock()), note[:80], reservation.id))

    def recover_stale(self) -> int:
        """After a crash a reservation may be stuck as 'reserved'. We do not know whether the request went out, so it
        becomes 'uncertain': still counted, never retried automatically."""
        cutoff = iso(self.clock() - timedelta(minutes=self.config.budget.reservation_stale_minutes))
        with self.store.transaction() as db:
            cur = db.execute("UPDATE ledger SET status='uncertain', note='stale reservation after interruption' "
                             "WHERE kind='ai' AND status='reserved' AND created_at<?", (cutoff,))
            return cur.rowcount

    # ---- reporting -----------------------------------------------------------------------------------------------------------------
    def report(self, day: str, qday: str) -> dict:
        db = self.store.db
        searches = db.execute("SELECT COUNT(*) FROM ledger WHERE day=? AND kind='youtube_search' AND status<>'released'", (qday,)).fetchone()[0]
        others = db.execute("SELECT COUNT(*) FROM ledger WHERE day=? AND kind='youtube_other' AND status<>'released'", (qday,)).fetchone()[0]
        out = {"youtube": {"quota_day_pacific": qday, "search_calls": searches, "other_calls": others, "units_used": searches + others,
                           "search_calls_limit": self.config.youtube.search_calls_per_day, "units_limit": self.config.youtube.units_per_day}}
        for provider in ("openai", "anthropic"):
            rows = db.execute("SELECT status, tokens_reserved, usd_reserved, tokens_actual, usd_actual FROM ledger "
                              "WHERE day=? AND kind='ai' AND provider=? AND status<>'released'", (day, provider)).fetchall()
            out[provider] = {"requests": len(rows), "tokens_reserved": sum(r["tokens_reserved"] for r in rows),
                             "usd_reserved": round(sum(r["usd_reserved"] for r in rows), 8),
                             "tokens_actual": sum(r["tokens_actual"] or 0 for r in rows if r["status"] == "settled"),
                             "usd_actual": round(sum(r["usd_actual"] or 0 for r in rows if r["status"] == "settled"), 8),
                             "uncertain": sum(1 for r in rows if r["status"] == "uncertain")}
        return out


class Pacer:
    """Minimum gap between any two external requests, kept across restarts in the research database."""

    def __init__(self, store: ResearchStore, min_interval_s: float, clock=time.time, sleep=time.sleep):
        self.store, self.min_interval_s, self.clock, self.sleep = store, min_interval_s, clock, sleep

    def wait(self) -> None:
        row = self.store.db.execute("SELECT value FROM meta WHERE key='last_external_call'").fetchone()
        last = float(row["value"]) if row else 0.0
        gap = self.clock() - last
        if 0 <= gap < self.min_interval_s:
            self.sleep(self.min_interval_s - gap)
        with self.store.transaction() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('last_external_call',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (repr(self.clock()),))
