"""A read-only local page for the coach: queue, library, drafts, approved knowledge, recommendations, results, latest report.
One self-contained HTML file written where you say; no server, no network, no script. Every value is escaped."""
import html
import json
from datetime import datetime
from pathlib import Path

from learning_engine import library, outcomes, recommend
from learning_engine.review import ReviewService
from learning_engine.store import ResearchStore

CSS = ("body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:1.5rem auto;padding:0 1rem;color:#1d2a2f;background:#fbfaf7}"
       "h1{font-size:1.4rem}h2{margin-top:2rem;border-bottom:2px solid #c9d6cf;padding-bottom:.2rem}table{border-collapse:collapse;width:100%}"
       "td,th{border:1px solid #d6ddd8;padding:.3rem .5rem;text-align:left;vertical-align:top;font-size:.9rem}th{background:#eef3ef}"
       ".warn{background:#fff3cd;border:1px solid #e6c65c;padding:.5rem .8rem;border-radius:6px}.tag{background:#e3ebe6;border-radius:4px;padding:0 .35rem;font-size:.8rem}"
       ".fixture{background:#f8d7da}")


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "<p><em>nothing yet</em></p>"
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c if isinstance(c, _Raw) else _e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


class _Raw(str):
    pass


def build(store: ResearchStore, now: datetime, state_dir: Path | None = None) -> str:
    db = store.db
    svc = ReviewService(store)
    recommend.refresh(store, now)
    parts = [f"<h1>KEMET Learning Engine - coach view <small>({_e(now.strftime('%Y-%m-%d %H:%M UTC'))})</small></h1>",
             '<p class="warn">Read-only. Nothing here is published to players. Candidates found by public search are public metadata only: nobody watched or analysed them. '
             "A draft is the analyzer's suggestion, not a coach decision. A reviewer name typed in the command line is not a verified identity.</p>"]
    if db.execute("SELECT 1 FROM analyses WHERE is_fixture=1").fetchone() or db.execute("SELECT 1 FROM recommendations WHERE is_fixture=1").fetchone():
        parts.append('<p class="warn fixture"><strong>This state contains FIXTURE (test) data.</strong> It is not real analysis, not real players, and says nothing about training quality.</p>')
    q = db.execute("SELECT q.state, q.reason, q.attempts, q.last_error, q.state_changed_at, s.url, s.title, m.display_name FROM work_queue q LEFT JOIN sources s ON s.video_id=q.video_id "
                   "LEFT JOIN media_files m ON m.media_id=q.media_id ORDER BY q.state_changed_at, q.queue_id").fetchall()
    parts += ["<h2>1. Queue / الطابور</h2>", _table(["state", "source", "reason / last error", "attempts", "since"],
              [[r["state"], r["title"] or r["display_name"] or r["url"] or "(removed)", r["reason"] or r["last_error"], r["attempts"], r["state_changed_at"]] for r in q])]
    lib = library.entries(store)
    parts += ["<h2>2. Library: videos knowledge was extracted from / المكتبة</h2>",
              _table(["media", "source", "rights", "analyses", "drafts / approved"],
                     [[e["display_name"] or e["media_id"], f"{e['source']['kind']} {e['source'].get('link') or ''} {e['source'].get('title') or ''}".strip(),
                       f"{e['rights']['basis']} ({e['rights']['status']}), training not allowed",
                       "; ".join(f"v{a['version']} {a['analyzer_name']} {a['status']}{' FIXTURE' if a['is_fixture'] else ''} ({len(a['notes'])} timed notes)" for a in e["analyses"]),
                       "; ".join(f"{i['item_id']}: {i['review_status']}{'' if i['approval_state'] == 'current' else ' NEEDS REVIEW'}" for i in e["items"])] for e in lib])]
    drafts = [dict(r) for r in db.execute("SELECT item_id FROM review_items WHERE analysis_id IS NOT NULL AND review_status='pending' ORDER BY created_at, rowid")]
    parts.append("<h2>3. Drafts waiting for review / مسودات تنتظر المراجعة</h2>")
    rows = []
    for d in drafts:
        v = library.draft_view(store, d["item_id"])
        notes = "<br>".join(f"[{n['t_start_s']:.0f}-{n['t_end_s']:.0f}s] {_e(n['kind'])}: {_e(n['text'])}" for n in v["extracted_notes"][:8])
        rows.append([d["item_id"], _Raw(notes), json.dumps(v["analyzer_suggestion"], sort_keys=True)[:300], ("; ".join(v["content_problems"]) or "complete") + (f"; coach must confirm: {', '.join(v['unconfirmed_suggestions'])}" if v["unconfirmed_suggestions"] else ""), v["analysis"]["analyzer_name"] if v["analysis"] else ""])
    parts.append(_table(["item", "extracted notes (with video time)", "analyzer suggestion (unverified)", "still missing", "analyzer"], rows))
    approved = svc.approved_content()
    parts += ["<h2>4. Approved knowledge / المعرفة المعتمدة</h2>", _table(["item", "title", "basis", "source removed"],
              [[a["item_id"], a["content"].get("title"), a.get("review_basis"), a["source_removed"]] for a in approved])]
    recs = recommend.list_all(store, now)
    parts += ["<h2>5. Recommendations / التوصيات</h2>", _table(["rec", "player", "status", "why", "measure", "missing", "evidence"],
              [[r["rec_id"], r["player_ref"] + (" (FIXTURE)" if r["fixture"] else ""), f"{r['status']} {r['status_reason'] or ''}", (r["reason"].get("why") or r["status_reason"]),
                json.dumps(r["measure"]) if r["measure"] else "none", "; ".join(r["missing"]), json.dumps((r["evidence"] or {}).get("timed_notes", []))[:200]] for r in recs])]
    outs = outcomes.summaries(store)
    parts += ["<h2>6. Recorded session results / نتائج الجلسات</h2>", _table(["player", "drill", "sessions", "recorded changes", "suggestion"],
              [[o["player_ref"] + (" (FIXTURE)" if o["fixture"] else ""), o["item_id"], f"{o['sessions_done']} done / {o['sessions_recorded']} recorded",
                "; ".join(f"{s['date']}: {s.get('recorded_change')} {s.get('unit') or ''}" for s in o["sessions"] if "before" in s), o["suggestion"] or ""] for o in outs]),
              f"<p><em>{_e(outcomes.CAUSE_NOTE)}</em></p>"]
    if state_dir is not None:
        reports = sorted((Path(state_dir) / "reports").glob("*.md")) if (Path(state_dir) / "reports").is_dir() else []
        if reports:
            parts += ["<h2>7. Latest daily report / التقرير اليومي</h2>", f"<pre>{_e(reports[-1].read_text())}</pre>"]
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>KEMET Learning Engine</title><style>{CSS}</style></head><body>" + "\n".join(parts) + "</body></html>"
