"""The persistent work queue. One row per source; states: discovered, awaiting_media, ready, processing, draft_ready, skipped, failed.

* A YouTube candidate can never reach `ready` by itself: YouTube's developer policies forbid downloading its audio/video (III.E.1) and
  accessing audiovisual content by any means other than the API (III.I.14), and the embedded player API offers playback control only, so
  the only way to analyse such a source is a local file someone is allowed to analyse. Until one is attached it is `awaiting_media`.
* A row is claimed with a lease inside one write transaction, so two workers cannot take the same source. A lease that expires (a crash)
  is recovered and counts as an attempt.
* A skipped or failed source never stops the others. Retries use exponential backoff and stop at `max_attempts`."""
import secrets
from datetime import datetime, timedelta

from learning_engine.store import ResearchStore, iso

STATES = ("discovered", "awaiting_media", "ready", "processing", "draft_ready", "skipped", "failed")
NO_MEDIA = "NO_AUTHORIZED_MEDIA_YOUTUBE_CONTENT_NOT_ACCESSIBLE_BY_PERMITTED_MEANS"


def sync_discovered(store: ResearchStore, now: datetime) -> int:
    """Every canonical YouTube candidate gets a queue row (state `discovered`) once."""
    with store.transaction() as db:
        rows = db.execute("SELECT video_id FROM sources s WHERE duplicate_of IS NULL AND NOT EXISTS (SELECT 1 FROM work_queue q WHERE q.video_id=s.video_id) ORDER BY video_id").fetchall()
        for r in rows:
            db.execute("INSERT INTO work_queue(queue_id,kind,video_id,state,created_at,updated_at,state_changed_at) VALUES (?,?,?,?,?,?,?)",
                       ("Q-" + secrets.token_hex(8), "YOUTUBE_METADATA", r["video_id"], "discovered", iso(now), iso(now), iso(now)))
        return len(rows)


def triage(store: ResearchStore, now: datetime) -> dict:
    """discovered / awaiting_media YouTube candidates: an excluded or unavailable source is skipped with its reason; an available one waits
    for authorised media; one not yet verified stays `discovered`."""
    moved = {"skipped": 0, "awaiting_media": 0}
    with store.transaction() as db:
        for q in db.execute("SELECT q.queue_id, q.state, s.availability, s.exclusion_reason FROM work_queue q JOIN sources s ON s.video_id=q.video_id "
                            "WHERE q.kind='YOUTUBE_METADATA' AND q.state IN ('discovered','awaiting_media')").fetchall():
            if q["availability"] in ("excluded", "unavailable"):
                state, reason = "skipped", "SOURCE_" + (q["exclusion_reason"] or "UNAVAILABLE")
            elif q["availability"] == "available":
                state, reason = "awaiting_media", NO_MEDIA
            else:
                continue
            if q["state"] != state:
                db.execute("UPDATE work_queue SET state=?, reason=?, state_changed_at=?, updated_at=? WHERE queue_id=?", (state, reason, iso(now), iso(now), q["queue_id"]))
                moved[state] += 1
    return moved


def claim_next(store: ResearchStore, now: datetime, *, owner: str, lease_minutes: int):
    """The oldest `ready` row whose retry time has come, now `processing` under this owner's lease; None if there is none."""
    with store.transaction() as db:
        row = db.execute("SELECT * FROM work_queue WHERE state='ready' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY created_at, queue_id LIMIT 1", (iso(now),)).fetchone()
        if row is None:
            return None
        cur = db.execute("UPDATE work_queue SET state='processing', lease_owner=?, lease_until=?, state_changed_at=?, updated_at=? WHERE queue_id=? AND state='ready'",
                         (owner, iso(now + timedelta(minutes=lease_minutes)), iso(now), iso(now), row["queue_id"]))
        return dict(row) if cur.rowcount == 1 else None


def release_unchanged(store: ResearchStore, queue_id: str, owner: str, now: datetime, note: str) -> None:
    """Give a claimed row back without counting an attempt (nothing was tried, for example no analyzer is configured)."""
    with store.transaction() as db:
        db.execute("UPDATE work_queue SET state='ready', lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=? WHERE queue_id=? AND state='processing' AND lease_owner=?",
                   (note[:80], iso(now), queue_id, owner))


def skip(store: ResearchStore, queue_id: str, owner: str, now: datetime, reason: str) -> None:
    with store.transaction() as db:
        db.execute("UPDATE work_queue SET state='skipped', reason=?, lease_owner=NULL, lease_until=NULL, state_changed_at=?, updated_at=? WHERE queue_id=? AND state='processing' AND lease_owner=?",
                   (reason[:120], iso(now), iso(now), queue_id, owner))


def fail(store: ResearchStore, queue_id: str, owner: str, now: datetime, error: str, *, max_attempts: int, backoff_base_s: int, backoff_max_s: int) -> str:
    """One more failed attempt: back to `ready` after an exponential backoff, or `failed` once max_attempts is reached. Returns the new state."""
    with store.transaction() as db:
        row = db.execute("SELECT attempts FROM work_queue WHERE queue_id=? AND state='processing' AND lease_owner=?", (queue_id, owner)).fetchone()
        if row is None:
            return "lost_lease"
        attempts = row["attempts"] + 1
        if attempts >= max_attempts:
            db.execute("UPDATE work_queue SET state='failed', attempts=?, last_error=?, reason='MAX_ATTEMPTS_REACHED', lease_owner=NULL, lease_until=NULL, state_changed_at=?, updated_at=? WHERE queue_id=?",
                       (attempts, error[:80], iso(now), iso(now), queue_id))
            return "failed"
        wait = min(backoff_max_s, backoff_base_s * (2 ** (attempts - 1)))
        db.execute("UPDATE work_queue SET state='ready', attempts=?, last_error=?, next_attempt_at=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE queue_id=?",
                   (attempts, error[:80], iso(now + timedelta(seconds=wait)), iso(now), queue_id))
        return "ready"


def recover_stale(store: ResearchStore, now: datetime, *, max_attempts: int, backoff_base_s: int, backoff_max_s: int) -> int:
    """A worker that died holding a lease: the attempt counts, and the row is retried (or failed) like any other failure."""
    with store.transaction() as db:
        stale = db.execute("SELECT queue_id, lease_owner FROM work_queue WHERE state='processing' AND lease_until < ?", (iso(now),)).fetchall()
    for r in stale:
        fail(store, r["queue_id"], r["lease_owner"], now, "LEASE_EXPIRED", max_attempts=max_attempts, backoff_base_s=backoff_base_s, backoff_max_s=backoff_max_s)
    return len(stale)


def request_reanalysis(store: ResearchStore, media_id: str, now: datetime) -> bool:
    """Queue the media for another analysis. The previous analysis, its draft and any approved content stay exactly as they are; the new
    analysis becomes a NEW pending draft."""
    with store.transaction() as db:
        cur = db.execute("UPDATE work_queue SET state='ready', reason=NULL, attempts=0, last_error=NULL, next_attempt_at=NULL, state_changed_at=?, updated_at=? "
                         "WHERE media_id=? AND state IN ('draft_ready','skipped','failed')", (iso(now), iso(now), media_id))
        return cur.rowcount == 1


def counts(store: ResearchStore) -> dict:
    out = {s: 0 for s in STATES}
    for r in store.db.execute("SELECT state, COUNT(*) n FROM work_queue GROUP BY state"):
        out[r["state"]] = r["n"]
    return out
