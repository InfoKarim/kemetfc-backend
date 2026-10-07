"""Retention for data that came from the YouTube API.

Official rule (YouTube API Services Developer Policies, III.E.4.d, developers.google.com/youtube/terms/developer-policies):
non-authorized API data may be stored only temporarily, and "after 30 calendar days, the API Client must either delete or
refresh the stored data"; stored data should stay consistent with the current data (III.E.4.e). This module applies that:
  * sources not verified within the window are REFRESHED (live runs, through videos.list) or DELETED;
  * a coach-approved drill is the coach's OWN content and stays, but the API-derived fields of its source (title, dates,
    duration) are removed when they are not refreshed; only the pointer (video id, channel id, link) is kept.
What I cannot confirm: whether keeping the bare video id and link beyond 30 days is itself covered by the policy. It is
kept only so an approved item can still point to what the coach inspected; whoever owns policy compliance should decide.
Nothing here ever trains anything, and nothing stored here may be used to train a model (the policy forbids deriving new
data from API data without permission; this engine does not).
"""
from datetime import datetime, timedelta

from learning_engine.config import Config
from learning_engine.store import ResearchStore, iso


def due_for_refresh(store: ResearchStore, config: Config, now: datetime) -> list[str]:
    """Videos whose last verification is within `refresh_window_days` of the limit (or past it)."""
    limit = iso(now - timedelta(days=config.retention.youtube_data_days - config.retention.refresh_window_days))
    rows = store.db.execute("SELECT video_id FROM sources WHERE duplicate_of IS NULL AND availability IN ('available','unknown') "
                            "AND COALESCE(verified_at, discovered_at) < ? ORDER BY video_id", (limit,)).fetchall()
    return [r["video_id"] for r in rows]


def purge_expired(store: ResearchStore, config: Config, now: datetime) -> dict:
    """Apply the window now. Returns counts. Safe to run repeatedly."""
    cutoff = iso(now - timedelta(days=config.retention.youtube_data_days))
    removed = {"sources_deleted": 0, "items_deleted": 0, "api_fields_cleared_on_approved": 0}
    with store.transaction() as db:
        stale = db.execute("SELECT video_id FROM sources WHERE COALESCE(verified_at, discovered_at) < ?", (cutoff,)).fetchall()
        for row in stale:
            vid = row["video_id"]
            items = db.execute("SELECT item_id, review_status FROM review_items WHERE source_video_id=?", (vid,)).fetchall()
            if any(i["review_status"] == "approved" for i in items):
                db.execute("UPDATE sources SET title=NULL, published_at=NULL, duration_s=NULL, made_for_kids=NULL, privacy_status=NULL, "
                           "text_fingerprint=NULL, availability='unknown' WHERE video_id=?", (vid,))
                removed["api_fields_cleared_on_approved"] += 1
                continue
            for item in items:
                db.execute("DELETE FROM item_versions WHERE item_id=?", (item["item_id"],))
                db.execute("DELETE FROM reviews WHERE item_id=?", (item["item_id"],))
                db.execute("DELETE FROM review_items WHERE item_id=?", (item["item_id"],))
                removed["items_deleted"] += 1
            db.execute("DELETE FROM sources WHERE duplicate_of=?", (vid,))       # its duplicates carry the same stale text
            db.execute("DELETE FROM sources WHERE video_id=?", (vid,))
            removed["sources_deleted"] += 1
        if removed["sources_deleted"] or removed["api_fields_cleared_on_approved"]:
            store.audit("RETENTION_PURGE", "system", removed, now)
    return removed


def purge_old_reports(state_dir, config: Config, now: datetime) -> int:
    from pathlib import Path
    cutoff = now - timedelta(days=config.retention.reports_days)
    count = 0
    reports = Path(state_dir) / "reports"
    if reports.is_dir() and not reports.is_symlink():
        for f in reports.iterdir():
            if f.is_file() and not f.is_symlink() and datetime.fromtimestamp(f.stat().st_mtime, tz=now.tzinfo) < cutoff:
                f.unlink()
                count += 1
    return count
