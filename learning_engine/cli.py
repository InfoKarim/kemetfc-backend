"""Command line. Read the notice: a reviewer name typed here is NOT authentication."""
import argparse
import json
import sys
from pathlib import Path

from learning_engine.config import ConfigError, load_config
from learning_engine.daily import run_daily
from learning_engine.review import AUTH_CLI, ReviewError, ReviewService, Reviewer
from learning_engine.store import ResearchStore, StoreError

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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    if args.command == "validate-config":
        print(json.dumps({"valid": True, "mode": config.mode, "network_enabled": config.network_enabled,
                          "youtube_channels_allowlisted": len(config.youtube.channels),
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
                                  "rejected": len(service.list_items("rejected"))}, indent=2))
            elif args.command == "list":
                for item in service.list_items(args.status):
                    print(f"{item['item_id']}\t{item['review_status']}\t{item['stage']}\tv{item['version']}\t{(item['source'] or {}).get('title') or item['origin']}")
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
        finally:
            store.close()
    except (StoreError, ReviewError, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0
