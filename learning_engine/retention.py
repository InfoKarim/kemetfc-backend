"""Retention for data that came from the YouTube API.

Official rule (YouTube API Services Developer Policies, III.E.4.d, developers.google.com/youtube/terms/developer-policies):
non-authorized API data may be stored only temporarily, and "after 30 calendar days, the API Client must either delete or
refresh the stored data"; stored data should stay consistent with the current data (III.E.4.e). This module applies that:
  * sources not verified within the window are REFRESHED (live runs, through videos.list) or DELETED;
  * when a source is not refreshed in time, EVERYTHING that came from the API about it is deleted: title, dates, duration,
    fingerprint, and also the video id, channel id and link. I could not confirm that the policy allows keeping the bare
    video id or link past the window, and the safe reading is that it does not, so they go;
  * a coach's own work does not depend on the API: an item that has coach-written content or a recorded decision is KEPT, with
    its content, versions and decision history, flagged `source_state='removed'` and `source_removed_at`, so the page can say
    "the source was removed after the retention window" instead of silently dropping the drill or pretending it still has a
    source. Deleting the source data NEVER changes whether the approval is valid: `approval_state`, the invalidation reason
    and its date are not touched, so an item whose source had gone bad stays out of the valid export until a person decides again.
    An item that was never invalidated keeps its current approval, flagged `source_removed` in the export (retention alone is not
    evidence that the content went bad). An item with no coach content and no decision (a bare candidate) is deleted with its source;
  * the link a coach recorded as inspected is part of the decision record; it is replaced by a marker, because it is the same
    API-derived identifier. A coach who pasted the link into their own notes wrote it themselves; those notes are not scanned
    or altered.
Not removed and not removable here: audit entries written by version 1 of the engine, whose item ids embedded the video id (the
audit chain is append-only and hash-linked). Item ids made since version 2 are random. No live run has existed, so this concerns
test data only.
Nothing here ever trains anything, and nothing stored here may be used to train a model (the policy forbids deriving new data
from API data without permission; this engine does not).
"""
from datetime import datetime, timedelta

from learning_engine.config import Config
from learning_engine.discovery import new_item_id
from learning_engine.store import ResearchStore, iso


def due_for_refresh(store: ResearchStore, config: Config, now: datetime) -> list[str]:
    """Videos whose last verification is within `refresh_window_days` of the limit (or past it)."""
    limit = iso(now - timedelta(days=config.retention.youtube_data_days - config.retention.refresh_window_days))
    rows = store.db.execute("SELECT video_id FROM sources WHERE duplicate_of IS NULL AND availability IN ('available','unknown') "
                            "AND COALESCE(verified_at, discovered_at) < ? ORDER BY video_id", (limit,)).fetchall()
    return [r["video_id"] for r in rows]


REMOVED_MARKER = "SOURCE_REMOVED_AFTER_RETENTION_WINDOW"


def purge_expired(store: ResearchStore, config: Config, now: datetime) -> dict:
    """Apply the window now. Returns counts. Safe to run repeatedly."""
    cutoff = iso(now - timedelta(days=config.retention.youtube_data_days))
    removed = {"sources_deleted": 0, "items_deleted": 0, "items_kept_with_source_removed": 0}
    with store.transaction() as db:
        db.execute("PRAGMA defer_foreign_keys=ON")
        stale = [r["video_id"] for r in db.execute("SELECT video_id FROM sources WHERE COALESCE(verified_at, discovered_at) < ? ORDER BY video_id", (cutoff,)).fetchall()]
        for vid in stale:
            for item in db.execute("SELECT item_id FROM review_items WHERE source_video_id=?", (vid,)).fetchall():
                item_id = item["item_id"]
                has_work = db.execute("SELECT 1 FROM item_versions WHERE item_id=? AND author_kind IN ('COACH','ANALYZER') LIMIT 1", (item_id,)).fetchone() or \
                    db.execute("SELECT 1 FROM reviews WHERE item_id=? LIMIT 1", (item_id,)).fetchone()
                if has_work:
                    new_id = item_id
                    if vid in item_id:                                       # a version-1 id that embeds the video id
                        new_id = new_item_id()
                        db.execute("UPDATE item_versions SET item_id=? WHERE item_id=?", (new_id, item_id))
                        db.execute("UPDATE reviews SET item_id=? WHERE item_id=?", (new_id, item_id))
                    db.execute("UPDATE review_items SET item_id=?, source_video_id=NULL, source_state='removed', "
                               "source_removed_at=?, updated_at=? WHERE item_id=?", (new_id, iso(now), iso(now), item_id))
                    db.execute("UPDATE reviews SET sources_json=? WHERE item_id=?", (f'["{REMOVED_MARKER}"]', new_id))
                    removed["items_kept_with_source_removed"] += 1
                else:
                    db.execute("DELETE FROM item_versions WHERE item_id=?", (item_id,))
                    db.execute("DELETE FROM review_items WHERE item_id=?", (item_id,))
                    removed["items_deleted"] += 1
            db.execute("DELETE FROM work_queue WHERE video_id=? AND media_id IS NULL", (vid,))     # a YouTube candidate nobody attached media to: nothing to keep
            db.execute("DELETE FROM work_queue WHERE video_id IN (SELECT video_id FROM sources WHERE duplicate_of=?) AND media_id IS NULL", (vid,))
            db.execute("UPDATE work_queue SET video_id=NULL WHERE video_id=?", (vid,))             # with media: the local analysis stays, the YouTube identifier goes
            db.execute("DELETE FROM sources WHERE duplicate_of=?", (vid,))       # its duplicates carry the same stale text
            db.execute("DELETE FROM sources WHERE video_id=?", (vid,))
            db.execute("UPDATE sources SET fingerprint_conflict_with=NULL WHERE fingerprint_conflict_with=?", (vid,))      # no stale pointer to a deleted id
            removed["sources_deleted"] += 1
        db.execute("UPDATE runs SET checkpoint_json='{}' WHERE started_at < ? AND checkpoint_json<>'{}'", (cutoff,))        # unfinished-run checkpoints hold video ids
        if removed["sources_deleted"]:
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
