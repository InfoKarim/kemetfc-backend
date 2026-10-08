"""Command line. Read the notice: a reviewer name typed here is NOT authentication."""
import argparse
import json
import os
import sys
from pathlib import Path

from learning_engine.config import ConfigError, load_config
from learning_engine.daily import run_daily
from learning_engine.review import AUTH_CLI, ReviewError, ReviewService, Reviewer
from learning_engine.store import ResearchStore, StoreError
from learning_engine.media import MediaError
from learning_engine.outcomes import OutcomeError
from learning_engine.recommend import RecommendError

NOTICE = ("NOTE: the reviewer id you type is recorded as typed. It is not authentication, so this is not a multi-user review system. "
          "Use the backend review API (an authenticated coach or admin account) for a real audit trail.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="learning_engine", description="Local Dribbling research sidecar. Offline unless configured and approved.")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--state", required=True, type=Path, help="private directory for the research database and reports")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-config")
    run = sub.add_parser("run")
    run.add_argument("--day", help="YYYY-MM-DD (default: today in the configured timezone)")
    sub.add_parser("status")
    pend = sub.add_parser("list")
    pend.add_argument("--status", choices=["pending", "approved", "rejected"])
    pend.add_argument("--needs-source-review", action="store_true", help="approved items whose source is no longer valid and must be reviewed again")
    show = sub.add_parser("show")
    show.add_argument("item_id")
    save = sub.add_parser("save-content")
    save.add_argument("item_id"); save.add_argument("--file", required=True, type=Path); save.add_argument("--reviewer-id", required=True)
    dec = sub.add_parser("decide")
    dec.add_argument("item_id"); dec.add_argument("--version", required=True, type=int)
    dec.add_argument("--decision", required=True, choices=["approved", "rejected"])
    dec.add_argument("--reviewer-id", required=True); dec.add_argument("--reasons", required=True)
    dec.add_argument("--source-checked", action="append", default=[])
    sub.add_parser("export-approved")
    # ---- knowledge library, queue, recommendations, results (all local; no network) ----
    sub.add_parser("queue", help="the work queue and the state of every source")
    reg = sub.add_parser("media-register", help="register a LOCAL file you have the right to analyse")
    reg.add_argument("file", type=Path); reg.add_argument("--rights-basis", required=True, choices=["OWN_RECORDING", "OWNER_PERMISSION", "LICENSED"])
    reg.add_argument("--grantor", required=True); reg.add_argument("--minors", required=True, choices=["no", "yes", "unknown"])
    reg.add_argument("--recorded-by", required=True); reg.add_argument("--note"); reg.add_argument("--name"); reg.add_argument("--queue-id")
    wd = sub.add_parser("rights-withdraw"); wd.add_argument("media_id"); wd.add_argument("--reason", required=True); wd.add_argument("--by", required=True)
    sub.add_parser("pipeline-run", help="one bounded local pass over the queue (shows ANALYZER_NOT_CONFIGURED when no analyzer is plugged in)")
    ra = sub.add_parser("reanalyze"); ra.add_argument("media_id")
    lib = sub.add_parser("library"); lib.add_argument("--media")
    sub.add_parser("drafts", help="analysis drafts waiting for review, in one list")
    dr = sub.add_parser("draft"); dr.add_argument("item_id")
    rc = sub.add_parser("recommend"); rc.add_argument("--context", required=True, type=Path); rc.add_argument("--requested-by", required=True)
    sub.add_parser("rec-list")
    rs = sub.add_parser("rec-show"); rs.add_argument("rec_id")
    rd = sub.add_parser("rec-decide"); rd.add_argument("rec_id"); rd.add_argument("--decision", required=True, choices=["approve", "reject"])
    rd.add_argument("--reviewer-id", required=True); rd.add_argument("--reasons", required=True)
    oa = sub.add_parser("outcome-add")
    oa.add_argument("--player", required=True); oa.add_argument("--date", required=True); oa.add_argument("--executed", required=True, choices=["yes", "no"])
    oa.add_argument("--recorded-by", required=True); oa.add_argument("--rec-id"); oa.add_argument("--item-id"); oa.add_argument("--measure"); oa.add_argument("--unit")
    oa.add_argument("--before", type=float); oa.add_argument("--after", type=float); oa.add_argument("--method-before"); oa.add_argument("--method-after")
    oa.add_argument("--conditions"); oa.add_argument("--notes"); oa.add_argument("--fixture", action="store_true")
    sub.add_parser("outcomes")
    db = sub.add_parser("dashboard", help="write a read-only HTML page for the coach"); db.add_argument("--out", required=True, type=Path)
    return p


def _knowledge_command(args, store, config) -> None:
    from datetime import datetime  # noqa: F401
    from learning_engine import dashboard, library, media, outcomes, recommend, workqueue
    from learning_engine.analyzer import NoAnalyzer
    from learning_engine.pipeline import run_pass
    from learning_engine.store import utc_now
    now = utc_now()
    out = lambda obj: print(json.dumps(obj, indent=2, default=str))        # noqa: E731
    if args.command == "queue":
        workqueue.sync_discovered(store, now); workqueue.triage(store, now)
        out({"counts": workqueue.counts(store), "rows": [dict(r) for r in store.db.execute(
            "SELECT queue_id, kind, state, reason, attempts, last_error, next_attempt_at, state_changed_at, media_id FROM work_queue ORDER BY state_changed_at")]})
    elif args.command == "media-register":
        out(media.register_media(store, args.file, rights_basis=args.rights_basis, rights_grantor=args.grantor, rights_note=args.note, minors_visible=args.minors,
                                 recorded_by=args.recorded_by, now=now, max_file_mb=config.pipeline.max_file_mb, display_name=args.name, queue_id=args.queue_id))
    elif args.command == "rights-withdraw":
        out({"approved_items_suspended": media.withdraw_rights(store, args.media_id, reason=args.reason, by=args.by, now=now)})
    elif args.command == "pipeline-run":
        res = run_pass(store, config.pipeline, NoAnalyzer(), now)
        out({"result": res.__dict__, "note": "no analyzer is built in: sources with media stay `ready` until one is plugged in by code"})
    elif args.command == "reanalyze":
        out({"queued": workqueue.request_reanalysis(store, args.media_id, now)})
    elif args.command == "library":
        out(library.entry(store, args.media) if args.media else library.entries(store))
    elif args.command == "drafts":
        rows = store.db.execute("SELECT item_id FROM review_items WHERE analysis_id IS NOT NULL AND review_status='pending' ORDER BY created_at, rowid").fetchall()
        for r in rows:
            v = library.draft_view(store, r["item_id"])
            print(f"{r['item_id']}\tv{v['current_version']}\t{len(v['extracted_notes'])} timed notes\tmissing: {', '.join(v['content_problems']) or 'nothing'}\tunconfirmed suggestions: {len(v['unconfirmed_suggestions'])}"
                  f"\t{v['analysis']['analyzer_name'] if v['analysis'] else ''}{' FIXTURE' if v['analysis'] and v['analysis']['is_fixture'] else ''}")
    elif args.command == "draft":
        out(library.draft_view(store, args.item_id))
    elif args.command == "recommend":
        print(NOTICE, file=sys.stderr)
        out(recommend.recommend(store, json.loads(args.context.read_text()), now=now, actor=args.requested_by))
    elif args.command == "rec-list":
        out(recommend.list_all(store, now))
    elif args.command == "rec-show":
        recommend.refresh(store, now); out(recommend.get(store, args.rec_id))
    elif args.command == "rec-decide":
        print(NOTICE, file=sys.stderr)
        out(recommend.decide(store, args.rec_id, args.decision, reviewer=args.reviewer_id, reasons=args.reasons, now=now))
    elif args.command == "outcome-add":
        print(NOTICE, file=sys.stderr)
        out({"outcome_id": outcomes.add_outcome(store, player_ref=args.player, session_date=args.date, executed=args.executed == "yes", recorded_by=args.recorded_by,
                                                auth_mode=AUTH_CLI, now=now, rec_id=args.rec_id, item_id=args.item_id, measure_name=args.measure, unit=args.unit,
                                                before_value=args.before, after_value=args.after, method_before=args.method_before, method_after=args.method_after,
                                                conditions=args.conditions, coach_notes=args.notes, fixture=args.fixture)})
    elif args.command == "outcomes":
        out(outcomes.summaries(store))
    elif args.command == "dashboard":
        page = dashboard.build(store, now, store.dir)
        if args.out.is_symlink():
            raise OSError("the output path must not be a link")
        args.out.write_text(page)
        os.chmod(args.out, 0o600)
        print(f"written: {args.out}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    if args.command == "validate-config":
        print(json.dumps({"valid": True, "mode": config.mode, "network_enabled": config.network_enabled,
                          "discovery_mode": config.youtube.discovery_mode, "youtube_channels_allowlisted": len(config.youtube.channels),
                          "providers_enabled": [n for n, p in config.providers.items() if p.enabled]}, indent=2))
        return 0
    try:
        if args.command == "run":
            report = run_daily(config, args.state, day=args.day)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["status"] in ("SUCCESS", "OFFLINE_NO_DISCOVERY") else 1
        store = ResearchStore(args.state)
        try:
            service = ReviewService(store)
            if args.command == "status":
                print(json.dumps({"schema_version": store.schema_version(), "audit_chain_ok": store.verify_audit(),
                                  "pending": len(service.list_items("pending")), "approved": len(service.list_items("approved")),
                                  "rejected": len(service.list_items("rejected")),
                                  "source_review_required": len(service.list_items(needs_source_review=True))}, indent=2))
            elif args.command == "list":
                for item in service.list_items(args.status, args.needs_source_review):
                    flag = ("\tNEEDS_REVIEW" + (f"({item['source_issue']})" if item["source_issue"] else "")) if item["needs_source_review"] else ""
                    flag += "\tSOURCE_REMOVED" if item["source_state"] == "removed" else ""
                    print(f"{item['item_id']}\t{item['review_status']}\t{item['stage']}\tv{item['version']}\t{(item['source'] or {}).get('title') or item['origin']}{flag}")
            elif args.command == "show":
                print(json.dumps({**service.get(args.item_id), "history": service.history(args.item_id)}, indent=2, default=str))
            elif args.command == "save-content":
                print(NOTICE, file=sys.stderr)
                print(json.dumps(service.save_content(args.item_id, json.loads(args.file.read_text()), Reviewer(args.reviewer_id, AUTH_CLI)), indent=2))
            elif args.command == "decide":
                print(NOTICE, file=sys.stderr)
                print(json.dumps(service.decide(args.item_id, args.version, args.decision, Reviewer(args.reviewer_id, AUTH_CLI), args.reasons, args.source_checked), indent=2))
            elif args.command == "export-approved":
                print(json.dumps(service.approved_content(), indent=2))
            else:
                _knowledge_command(args, store, config)
        finally:
            store.close()
    except (StoreError, ReviewError, MediaError, OutcomeError, RecommendError, OSError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0
