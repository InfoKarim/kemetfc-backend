"""The daily run: validate, lock, resume or skip, discover (live only), optional AI draft (paid only when every gate is open),
retention, report. Offline is the default and does no network and no paid work.

Idempotent per (day, mode): a SUCCESS run is returned as it was and never repeated; a PARTIAL or FAILED run is continued by the
next invocation from its checkpoint with the same persisted limits, so a retry can neither double-spend nor quietly turn a bad day
into a good one. A temporary failure is always visible in the report; nothing is reported SUCCESS unless every planned step
finished with no error.
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from learning_engine.ai import AIDrafts, ProviderDisabled
from learning_engine.budget import Budget, BudgetExceeded, Pacer, engine_day, quota_day
from learning_engine.config import Config, paid_blockers, youtube_blockers
from learning_engine.discovery import Discovery
from learning_engine.http import TransportError, UrllibTransport
from learning_engine.retention import due_for_refresh, purge_expired, purge_old_reports
from learning_engine.store import ResearchStore, iso, utc_now
from learning_engine.youtube import YouTubeApiError, YouTubeClient

REPORT_SCHEMA = "kemet.learning_engine.report/1"
STATUSES = ("SUCCESS", "PARTIAL", "FAILED", "NOT_CONFIGURED", "OFFLINE_NO_DISCOVERY")


def _write_private(path: Path, text: str) -> None:
    if path.is_symlink():
        raise OSError("a symbolic link as a report file is refused")
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def run_daily(config: Config, state_dir: Path, *, env: dict | None = None, now: datetime | None = None, day: str | None = None,
              transport=None, ai_transport=None, fixture: bool = False, sleep=time.sleep, wall_clock=time.time,
              repo_root: Path | None = None) -> dict:
    env = os.environ if env is None else env
    now = now or utc_now()
    clock = lambda: now
    store = ResearchStore(state_dir, repo_root)
    try:
        store.acquire_run_lock()
        day = day or engine_day(now, config.timezone)
        mode = "fixture" if fixture else config.mode
        prior = store.db.execute("SELECT * FROM runs WHERE day=? AND mode=?", (day, mode)).fetchone()
        if prior and prior["status"] == "SUCCESS":
            stored = json.loads(prior["report_json"])
            stored["notes"] = stored.get("notes", []) + ["this day already finished successfully; nothing was repeated"]
            return stored
        attempt = (prior["attempt"] + 1) if prior else 1
        checkpoint = json.loads(prior["checkpoint_json"]) if prior else {}
        with store.transaction() as db:
            db.execute("INSERT INTO runs(day,mode,attempt,status,started_at,checkpoint_json) VALUES (?,?,?,?,?,?) "
                       "ON CONFLICT(day,mode) DO UPDATE SET attempt=excluded.attempt, status='RUNNING', started_at=excluded.started_at",
                       (day, mode, attempt, "RUNNING", iso(now), json.dumps(checkpoint)))
        report = _run(config, store, env, now, day, mode, attempt, checkpoint, transport, ai_transport, fixture, sleep, wall_clock, clock, Path(state_dir))
        with store.transaction() as db:
            db.execute("UPDATE runs SET status=?, finished_at=?, checkpoint_json=?, report_json=? WHERE day=? AND mode=?",
                       (report["status"], iso(clock()), json.dumps({} if report["status"] == "SUCCESS" else checkpoint),      # a finished day keeps no video ids
                        json.dumps(report, sort_keys=True), day, mode))
        reports = store.dir / "reports"
        reports.mkdir(mode=0o700, exist_ok=True)
        os.chmod(reports, 0o700)
        _write_private(reports / f"{day}-{mode}.json", json.dumps(report, indent=2, sort_keys=True))
        _write_private(reports / f"{day}-{mode}.md", render_markdown(report))
        return report
    finally:
        store.close()


def _run(config, store, env, now, day, mode, attempt, checkpoint, transport, ai_transport, fixture, sleep, wall_clock, clock, state_dir) -> dict:
    budget = Budget(store, config, clock)
    stale = budget.recover_stale()
    errors: list[dict] = []
    notes: list[str] = []
    counts = {"discovered": 0, "new": 0, "duplicate_by_video_id": 0, "duplicate_by_text_fingerprint": 0, "metadata_only": 0,
              "excluded": 0, "unavailable": 0, "invalid_items": 0}
    services = {"youtube": {"status": "skipped", "reasons": []}, "openai": {"status": "skipped", "reasons": []},
                "anthropic": {"status": "skipped", "reasons": []}}
    channels_done = 0
    fatal = False
    public_mode = config.youtube.discovery_mode == "public_keyword_search"
    planned = 1 if public_mode else len(config.youtube.channels)

    # ---- YouTube ------------------------------------------------------------------------------------------------------
    if mode == "offline":
        services["youtube"]["reasons"] = ["MODE_IS_OFFLINE"]
    else:
        blockers = youtube_blockers(config, env if not fixture else {config.youtube.api_key_env: "fixture"})
        if fixture:
            blockers = [b for b in blockers if b not in ("MODE_IS_OFFLINE", "NETWORK_DISABLED")]
        if blockers:
            services["youtube"]["reasons"] = blockers
        else:
            key = "fixture" if fixture else env[config.youtube.api_key_env]
            tr = transport or UrllibTransport()
            pacer = Pacer(store, config.youtube.min_interval_s, wall_clock, sleep)
            client = YouTubeClient(tr, key, config.youtube, before_attempt=budget.reserve_youtube, pace=pacer.wait, sleep=sleep)
            disc = Discovery(store, config, client, clock)
            try:
                if public_mode:
                    channels_done += 1 if disc.discover_public(now, counts, errors, checkpoint) else 0
                else:
                    for channel in config.youtube.channels:
                        if disc.discover_channel(channel.channel_id, now, counts, errors, checkpoint):
                            channels_done += 1
                new_ids = [i for st in checkpoint.values() if isinstance(st, dict) for i in st.get("ids", [])]
                pending = [r["video_id"] for r in store.db.execute("SELECT video_id FROM sources WHERE stage='discovered' AND availability='unknown' AND duplicate_of IS NULL ORDER BY video_id")]
                refresh = due_for_refresh(store, config, now)
                approved = [r["video_id"] for r in store.db.execute(          # re-checked every run: an approved source can go private or be deleted
                    "SELECT DISTINCT s.video_id FROM sources s JOIN review_items i ON i.source_video_id=s.video_id "
                    "WHERE i.review_status='approved' AND i.source_state='present' AND s.duplicate_of IS NULL ORDER BY s.video_id")]
                disc.verify(sorted(set(new_ids) | set(pending) | set(refresh) | set(approved)), now, counts, errors)
            except YouTubeApiError as exc:
                fatal = True
                errors.append({"where": "youtube", "code": exc.code})
            except BudgetExceeded as exc:
                errors.append({"where": "youtube_budget", "code": exc.reason})
                notes.append("the daily limit was reached; the run stops and can be continued after the limit resets")
            except Exception as exc:                          # type name only: a message could carry a secret
                fatal = True
                errors.append({"where": "youtube", "code": f"UNEXPECTED_{type(exc).__name__}"})
            services["youtube"] = {"status": "ok" if channels_done == planned and not errors else "partial" if channels_done else "failed",
                                   "reasons": [], "channels_done": channels_done, "channels_planned": planned,
                                   "request_attempts_this_run": client.attempts}

    # ---- AI drafts (every gate must be open; none is in the default configuration) -----------------------------------------
    for provider in ("openai", "anthropic"):
        if provider not in config.providers:
            services[provider]["reasons"] = ["PROVIDER_NOT_CONFIGURED"]
            continue
        blockers = paid_blockers(config, provider, env)
        if blockers or fixture and ai_transport is None:
            services[provider]["reasons"] = blockers or ["FIXTURE_RUN_HAS_NO_AI_TRANSPORT"]
            continue
        already = store.db.execute("SELECT COUNT(*) FROM ledger WHERE day=? AND kind='ai' AND provider=? AND status IN ('settled','uncertain','sent')", (day, provider)).fetchone()[0]
        if already:
            services[provider] = {"status": "skipped", "reasons": ["ALREADY_REQUESTED_TODAY_NOT_RETRIED"]}
            continue
        try:
            outcome = AIDrafts(store, config, budget, ai_transport or UrllibTransport(), env, clock, sleep, wall_clock).generate(provider)
            services[provider] = {"status": "ok", "reasons": [], "item": outcome["item_id"], "usage": outcome["usage"]}
        except (ProviderDisabled, BudgetExceeded) as exc:
            services[provider] = {"status": "skipped", "reasons": getattr(exc, "blockers", None) or [exc.reason]}
        except TransportError as exc:
            services[provider] = {"status": "failed", "reasons": [f"TRANSPORT_{exc.code}"]}
            errors.append({"where": provider, "code": f"TRANSPORT_{exc.code}"})
        except Exception as exc:                              # type name only: a message could carry a secret
            services[provider] = {"status": "failed", "reasons": [f"UNEXPECTED_{type(exc).__name__}"]}
            errors.append({"where": provider, "code": f"UNEXPECTED_{type(exc).__name__}"})

    # ---- retention ---------------------------------------------------------------------------------------------------------------
    retention = purge_expired(store, config, now)
    retention["reports_deleted"] = purge_old_reports(state_dir, config, now)

    # ---- status -------------------------------------------------------------------------------------------------------------------
    if mode == "offline":
        status = "OFFLINE_NO_DISCOVERY"
    elif services["youtube"]["status"] == "skipped":
        status = "NOT_CONFIGURED"
    elif errors and (channels_done == 0 or fatal):
        status = "FAILED" if channels_done == 0 else "PARTIAL"
    elif errors or services["youtube"]["status"] != "ok":
        status = "PARTIAL"
    else:
        status = "SUCCESS"

    db = store.db
    review = {s: db.execute("SELECT COUNT(*) FROM review_items WHERE review_status=?", (s,)).fetchone()[0] for s in ("pending", "approved", "rejected")}
    review["source_review_required"] = db.execute("SELECT COUNT(*) FROM review_items WHERE approval_state='review_required'").fetchone()[0]
    review["source_removed_after_retention"] = db.execute("SELECT COUNT(*) FROM review_items WHERE source_state='removed'").fetchone()[0]
    stages = {s: db.execute("SELECT COUNT(*) FROM review_items WHERE stage=?", (s,)).fetchone()[0] for s in ("discovered", "metadata_only", "ai_draft", "coach_reviewed")}
    spend = budget.report(day, quota_day(now))
    ai_calls = {p: spend[p]["requests_sent"] for p in ("openai", "anthropic")}
    return {
        "schema": REPORT_SCHEMA, "date": day, "timezone": config.timezone, "generated_at": iso(now),
        "mode": mode, "live": mode == "live", "fixture_data_not_live_results": fixture,
        "status": status, "attempt": attempt, "resumed_from_checkpoint": attempt > 1,
        "sources": dict(counts), "review": review, "stages": stages, "services": services, "errors": errors, "notes": notes
        + ([f"{stale} stale reservation(s) from an interrupted run were marked uncertain (still counted, never retried)"] if stale else []),
        "limits": {"youtube_search_calls_per_day": config.youtube.search_calls_per_day, "youtube_units_per_day": config.youtube.units_per_day,
                   "ai_requests_per_day": config.budget.daily_ai_requests, "ai_tokens_per_day": config.budget.daily_ai_tokens,
                   "ai_cost_usd_per_day": config.budget.daily_cost_usd, "youtube_retries": config.youtube.max_retries},
        "api_calls": {"youtube_search": spend["youtube"]["search_calls"], "youtube_other": spend["youtube"]["other_calls"], **ai_calls},
        "quota": spend["youtube"], "ai_requests": {p: {k: spend[p][k] for k in ("requests_attempted", "requests_sent", "requests_counted_against_cap", "settled", "uncertain",
                                                      "rate_limited", "in_flight", "released_not_sent")} for p in ("openai", "anthropic")},
        "tokens": {p: {"reserved": spend[p]["tokens_reserved"], "actual_reported": spend[p]["tokens_actual"],
                       "uncertain_calls": spend[p]["uncertain"]} for p in ("openai", "anthropic")},
        "cost_usd": {p: {"reserved": spend[p]["usd_reserved"], "actual_estimate_from_operator_prices": spend[p]["usd_actual"]} for p in ("openai", "anthropic")},
        "cost_note": "reserved and estimated from operator-supplied ceilings and prices: not a guarantee of any provider invoice",
        "retention": retention, "audit_chain_ok": store.verify_audit(),
        "discovery": {"mode": config.youtube.discovery_mode, "query": config.youtube.query, "queries_sent_this_run": 1 if public_mode and channels_done else 0,
                      "future_queries_prepared_not_sent": list(config.youtube.future_queries),
                      "class": "PUBLIC_METADATA_DISCOVERY" if public_mode else "ALLOWLISTED_CHANNEL",
                      "meaning": ("public metadata found by a keyword search: not an approved channel, not a trusted source, no permission from any owner, "
                                  "nothing downloaded or analysed, nothing about the drill or its suitability for children is known") if public_mode
                      else "found inside an allowlisted channel the operator chose"},
    }


def render_markdown(r: dict) -> str:
    s, rv = r["sources"], r["review"]
    lines = [f"# Learning engine report - {r['date']} ({r['timezone']})", "",
             f"- status: **{r['status']}** (mode: {r['mode']}{', FIXTURE DATA, not live results' if r['fixture_data_not_live_results'] else ''}), attempt {r['attempt']}",
             f"- sources: discovered {s['discovered']}, new {s['new']}, duplicates by video id {s['duplicate_by_video_id']}, "
             f"duplicates by exact text {s['duplicate_by_text_fingerprint']}, metadata-only {s['metadata_only']}, excluded {s['excluded']}, unavailable {s['unavailable']}",
             f"- review: pending {rv['pending']}, approved {rv['approved']}, rejected {rv['rejected']}",
             f"- stages: " + ", ".join(f"{k} {v}" for k, v in r["stages"].items()), "", "## Services", ""]
    for name, svc in r["services"].items():
        lines.append(f"- {name}: {svc['status']}" + (f" ({', '.join(svc['reasons'])})" if svc.get("reasons") else ""))
    d = r.get("discovery") or {}
    lines += ["", "## Discovery", "", f"- mode: {d.get('mode')} (class {d.get('class')}), query: {d.get('query')!r}, queries sent this run: {d.get('queries_sent_this_run')}",
              f"- meaning: {d.get('meaning')}"]
    lines += ["", "## Calls and limits", "", f"- API calls: {r['api_calls']}", f"- limits: {r['limits']}",
              f"- cost (reserved / estimated): {r['cost_usd']} - {r['cost_note']}", "", "## Errors", ""]
    lines += [f"- {e}" for e in r["errors"]] or ["- none"]
    lines += ["", "## Notes", ""] + ([f"- {n}" for n in r["notes"]] or ["- none"])
    lines += ["", f"Retention: {r['retention']}", f"Audit chain intact: {r['audit_chain_ok']}", ""]
    return "\n".join(lines)
