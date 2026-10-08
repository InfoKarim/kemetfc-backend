"""The knowledge library: one permanent record per analysed media file, tied to the analyses, the timed notes, the drafts and the coach's
decisions made from it.

* A source counts as "knowledge extracted" only if an analysis finished with status knowledge_extracted. A source whose analysis produced
  nothing is recorded as `no_knowledge` and is not counted.
* Saving a record approves nothing. Every analysis makes a PENDING draft; a re-analysis makes a NEW draft and never touches approved content.
* Identifiers that come from the YouTube API (video id, link, title, channel) are NOT copied here: for a YouTube-linked file they are read
  from the sources table, which keeps the 30-day refresh/delete rule. After that window the entry shows that the source was removed; the
  analysis, the timed notes and the coach's decisions stay, because they come from the local file and the coach, not from the API."""
import json
import secrets
from datetime import datetime

from learning_engine.analyzer import AnalysisOutput, draft_content, validate_output
from learning_engine.review import content_problems
from learning_engine.store import ResearchStore, iso
from learning_engine.discovery import new_item_id


def record_analysis(store: ResearchStore, queue: dict, media: dict, analyzer, out: AnalysisOutput, *, started: datetime, now: datetime,
                    lease_owner: str) -> dict:
    """Everything one analysis produces, in ONE transaction: the analysis row, its notes, the pending draft and the queue state. A crash
    before the commit leaves the claim in `processing` (it is recovered and retried); a crash after leaves a finished record: never a duplicate."""
    notes, rejected = validate_output(out)
    status = "knowledge_extracted" if notes else "no_knowledge"
    limits = list(out.limits or []) + ["knowledge comes only from notes that carry a video time; other statements were discarded"]
    uncertain = list(out.uncertainties or [])
    if rejected:
        uncertain.append(f"{rejected} extracted statement(s) were discarded: unknown kind, unreadable text, or no valid video time")
    with store.transaction() as db:
        lease = db.execute("SELECT 1 FROM work_queue WHERE queue_id=? AND state='processing' AND lease_owner=?", (queue["queue_id"], lease_owner)).fetchone()
        if lease is None:
            return {"status": "lost_lease"}
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM analyses WHERE media_id=?", (media["media_id"],)).fetchone()[0]
        analysis_id = "AN-" + secrets.token_hex(8)
        db.execute("INSERT INTO analyses(analysis_id,media_id,version,analyzer_name,analyzer_version,method,is_fixture,status,started_at,finished_at,input_sha256,duration_s,"
                   "rejected_notes,uncertainties_json,limits_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (analysis_id, media["media_id"], version, analyzer.name, analyzer.version, analyzer.method, int(bool(analyzer.fixture)), status, iso(started), iso(now),
                    media["sha256"], out.duration_s, rejected, json.dumps(uncertain), json.dumps(limits)))
        for n in notes:
            db.execute("INSERT INTO knowledge_notes(analysis_id,kind,text,t_start_s,t_end_s,confidence) VALUES (?,?,?,?,?,?)",
                       (analysis_id, n["kind"], n["text"], n["t_start_s"], n["t_end_s"], n["confidence"]))
        item_id = None
        if status == "knowledge_extracted":
            item_id = _make_draft(store, db, queue, media, analyzer, analysis_id, notes, out.suggestion, now)
            db.execute("UPDATE work_queue SET state='draft_ready', analysis_id=?, reason=NULL, last_error=NULL, lease_owner=NULL, lease_until=NULL, state_changed_at=?, updated_at=? WHERE queue_id=?",
                       (analysis_id, iso(now), iso(now), queue["queue_id"]))
        else:
            db.execute("UPDATE work_queue SET state='skipped', analysis_id=?, reason='NO_KNOWLEDGE_EXTRACTED', lease_owner=NULL, lease_until=NULL, state_changed_at=?, updated_at=? WHERE queue_id=?",
                       (analysis_id, iso(now), iso(now), queue["queue_id"]))
        store.audit("ANALYSIS_RECORDED", f"{analyzer.name}:{analyzer.version}", {"analysis_id": analysis_id, "media_id": media["media_id"], "version": version,
                                                                                  "status": status, "notes": len(notes), "item_id": item_id, "fixture": bool(analyzer.fixture)}, now)
    return {"status": status, "analysis_id": analysis_id, "item_id": item_id, "notes": len(notes), "version": version}


def _make_draft(store, db, queue: dict, media: dict, analyzer, analysis_id: str, notes: list[dict], suggestion: dict, now: datetime) -> str:
    content = draft_content(notes, suggestion)
    body = json.dumps(content, sort_keys=True)
    digest = __import__("hashlib").sha256(body.encode()).hexdigest()
    author = f"{analyzer.name}:{analyzer.version}"
    # the first analysis of a YouTube candidate fills that candidate's empty item; any other analysis makes a NEW pending item that points to
    # the item it could replace, so approved content is never changed by a re-analysis
    existing = None
    if queue["video_id"]:
        existing = db.execute("SELECT item_id, current_version, review_status, analysis_id FROM review_items WHERE source_video_id=? ORDER BY created_at LIMIT 1", (queue["video_id"],)).fetchone()
    if existing and existing["analysis_id"] is None and existing["review_status"] == "pending" and existing["current_version"] == 1:
        item_id, version = existing["item_id"], 2
        db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)", (item_id, version, body, digest, "ANALYZER", author, iso(now)))
        db.execute("UPDATE review_items SET current_version=2, stage='analysis_draft', media_id=?, analysis_id=?, updated_at=? WHERE item_id=?", (media["media_id"], analysis_id, iso(now), item_id))
        return item_id
    item_id = new_item_id()
    origin = "YOUTUBE_SOURCE" if queue["video_id"] else "LOCAL_MEDIA"
    prior = db.execute("SELECT item_id FROM review_items WHERE media_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (media["media_id"],)).fetchone()
    db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,media_id,analysis_id,supersedes_item_id) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (item_id, origin, queue["video_id"], "analysis_draft", "pending", 1, iso(now), iso(now), media["media_id"], analysis_id,
                                                   prior["item_id"] if prior else None))
    db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)", (item_id, 1, body, digest, "ANALYZER", author, iso(now)))
    return item_id


def draft_view(store: ResearchStore, item_id: str) -> dict:
    """What a coach needs to review one draft, with the three layers kept apart: what was extracted (timed notes), what the analyzer suggested
    (the analyzer-authored version) and what a coach decided (coach versions and decisions)."""
    db = store.db
    item = db.execute("SELECT * FROM review_items WHERE item_id=?", (item_id,)).fetchone()
    if item is None:
        raise KeyError(item_id)
    analysis = db.execute("SELECT * FROM analyses WHERE analysis_id=?", (item["analysis_id"],)).fetchone() if item["analysis_id"] else None
    notes = [dict(r) for r in db.execute("SELECT note_id, kind, text, t_start_s, t_end_s, confidence FROM knowledge_notes WHERE analysis_id=? ORDER BY t_start_s, note_id", (item["analysis_id"],))] if analysis else []
    versions = [dict(r) for r in db.execute("SELECT version, author_kind, author_ref, created_at, content_json FROM item_versions WHERE item_id=? ORDER BY version", (item_id,))]
    suggestion = next((json.loads(v["content_json"]) for v in versions if v["author_kind"] == "ANALYZER"), None)
    current = json.loads(next(v["content_json"] for v in versions if v["version"] == item["current_version"]))
    decisions = [dict(r) for r in db.execute("SELECT version, decision, reviewer_id, auth_mode, review_basis, reasons, at FROM reviews WHERE item_id=? ORDER BY review_id", (item_id,))]
    return {"item_id": item_id, "review_status": item["review_status"], "current_version": item["current_version"], "approval_state": item["approval_state"],
            "supersedes_item_id": item["supersedes_item_id"], "media_id": item["media_id"],
            "analysis": None if analysis is None else {k: analysis[k] for k in ("analysis_id", "version", "analyzer_name", "analyzer_version", "method", "is_fixture", "status",
                                                                                "finished_at", "rejected_notes")} | {"uncertainties": json.loads(analysis["uncertainties_json"]),
                                                                                                                     "limits": json.loads(analysis["limits_json"])},
            "extracted_notes": notes, "analyzer_suggestion": suggestion, "current_content": current, "content_problems": content_problems(current),
            "unconfirmed_suggestions": list(current.get("unverified_fields") or []),
            "coach_versions": [{k: v[k] for k in ("version", "author_ref", "created_at")} for v in versions if v["author_kind"] == "COACH"],
            "coach_decisions": decisions}


def entries(store: ResearchStore) -> list[dict]:
    """The library: media files with at least one analysis that EXTRACTED knowledge."""
    out = []
    for m in store.db.execute("SELECT DISTINCT m.media_id FROM media_files m JOIN analyses a ON a.media_id=m.media_id WHERE a.status='knowledge_extracted' ORDER BY m.registered_at, m.media_id"):
        out.append(entry(store, m["media_id"]))
    return out


def entry(store: ResearchStore, media_id: str) -> dict:
    db = store.db
    m = db.execute("SELECT * FROM media_files WHERE media_id=?", (media_id,)).fetchone()
    if m is None:
        raise KeyError(media_id)
    linked = db.execute("SELECT q.video_id, s.url, s.title, s.channel_id, s.availability, s.exclusion_reason FROM work_queue q LEFT JOIN sources s ON s.video_id=q.video_id WHERE q.media_id=?", (media_id,)).fetchone()
    source = {"kind": "LOCAL_FILE", "link": None, "title": None, "channel": None, "status": None}
    if linked and linked["video_id"]:
        source = {"kind": "YOUTUBE_LINKED_LOCAL_FILE", "link": linked["url"], "title": linked["title"], "channel": linked["channel_id"], "status": linked["availability"],
                  "reason": linked["exclusion_reason"]}
    elif linked:
        source["note"] = "the linked YouTube source identifiers were removed after the retention window (or never existed); the analysis and decisions remain"
    analyses = [dict(r) for r in db.execute("SELECT analysis_id, version, analyzer_name, analyzer_version, method, is_fixture, status, finished_at, rejected_notes, uncertainties_json "
                                            "FROM analyses WHERE media_id=? ORDER BY version", (media_id,))]
    for a in analyses:
        a["uncertainties"] = json.loads(a.pop("uncertainties_json"))
        a["notes"] = [dict(r) for r in db.execute("SELECT note_id, kind, text, t_start_s, t_end_s, confidence FROM knowledge_notes WHERE analysis_id=? ORDER BY t_start_s", (a["analysis_id"],))]
    items = [dict(r) for r in db.execute("SELECT item_id, review_status, approval_state, current_version, analysis_id, supersedes_item_id FROM review_items WHERE media_id=? ORDER BY created_at, rowid", (media_id,))]
    for i in items:
        i["decisions"] = [dict(r) for r in db.execute("SELECT version, decision, reviewer_id, auth_mode, at FROM reviews WHERE item_id=? ORDER BY review_id", (i["item_id"],))]
        i["valid_for_use"] = i["review_status"] == "approved" and i["approval_state"] == "current" and m["status"] == "active"
    return {"media_id": media_id, "display_name": m["display_name"], "source": source, "rights": {"basis": m["rights_basis"], "grantor": m["rights_grantor"], "recorded_by": m["rights_recorded_by"],
            "recorded_at": m["rights_recorded_at"], "analysis_allowed": True, "training_allowed": False, "status": m["status"], "minors_visible": m["minors_visible"]},
            "analyses": analyses, "items": items}
