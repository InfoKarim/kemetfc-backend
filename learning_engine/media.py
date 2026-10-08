"""Local media files that may be analysed, with the rights record that allows it.

A file is registered only with a written rights basis (own recording, the owner's permission, or a licence), who granted it and who recorded
it. That permission is for ANALYSIS only: `analysis_allowed` is always 1 and `training_allowed` is always 0 (the database refuses anything
else), so nothing registered here can be used to train a model. The file stays where it is: the engine records its path, size and SHA-256
and re-checks them before every analysis. A copy of a YouTube video is not a rights basis; YouTube's terms forbid downloading it, and
nothing here downloads anything."""
import hashlib
import secrets
from datetime import datetime
from pathlib import Path

from learning_engine.store import ResearchStore, iso

RIGHTS_BASES = ("OWN_RECORDING", "OWNER_PERMISSION", "LICENSED")
MINORS = ("no", "yes", "unknown")


class MediaError(ValueError):
    pass


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain_file(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise MediaError("give the absolute path of the media file")
    if path.is_symlink() or not path.is_file():
        raise MediaError("the media path must be a regular file, not a link or a folder")
    return path


def register_media(store: ResearchStore, path: Path, *, rights_basis: str, rights_grantor: str, rights_note: str | None, minors_visible: str,
                   recorded_by: str, now: datetime, max_file_mb: int, display_name: str | None = None, queue_id: str | None = None) -> dict:
    """Register one local file. The same file (same SHA-256) is never registered twice: the existing record is returned.
    With `queue_id` the file is attached to that queue row (a discovered YouTube candidate waiting for media); otherwise a new queue row is made."""
    path = _plain_file(path)
    if rights_basis not in RIGHTS_BASES:
        raise MediaError("rights_basis must be one of " + ", ".join(RIGHTS_BASES))
    if not isinstance(rights_grantor, str) or len(rights_grantor.strip()) < 3:
        raise MediaError("say who granted the permission (rights_grantor)")
    if minors_visible not in MINORS:
        raise MediaError("minors_visible must be yes, no or unknown")
    if not isinstance(recorded_by, str) or len(recorded_by.strip()) < 2:
        raise MediaError("say who recorded this permission (recorded_by)")
    size = path.stat().st_size
    if size <= 0 or size > max_file_mb * 1024 * 1024:
        raise MediaError(f"the file is empty or larger than the {max_file_mb} MB limit")
    digest = sha256_file(path)
    with store.transaction() as db:
        existing = db.execute("SELECT media_id FROM media_files WHERE sha256=?", (digest,)).fetchone()
        if existing:
            return {"media_id": existing["media_id"], "duplicate": True}
        media_id = "MED-" + secrets.token_hex(8)
        db.execute("INSERT INTO media_files(media_id,sha256,local_path,display_name,size_bytes,rights_basis,rights_grantor,rights_note,rights_recorded_by,"
                   "rights_recorded_at,minors_visible,registered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (media_id, digest, str(path), (display_name or "")[:120] or None, size, rights_basis, rights_grantor.strip()[:120],
                    (rights_note or "")[:300] or None, recorded_by.strip()[:80], iso(now), minors_visible, iso(now)))
        if queue_id is not None:
            row = db.execute("SELECT kind, state, video_id FROM work_queue WHERE queue_id=?", (queue_id,)).fetchone()
            if row is None or row["kind"] != "YOUTUBE_METADATA" or row["state"] not in ("discovered", "awaiting_media"):
                raise MediaError("that queue entry cannot take a media file (it must be a YouTube candidate in discovered or awaiting_media)")
            db.execute("UPDATE work_queue SET media_id=?, state='ready', reason=NULL, state_changed_at=?, updated_at=? WHERE queue_id=?", (media_id, iso(now), iso(now), queue_id))
            db.execute("UPDATE review_items SET media_id=? WHERE source_video_id=? AND media_id IS NULL AND analysis_id IS NULL", (media_id, row["video_id"]))
        else:
            qid = "Q-" + secrets.token_hex(8)
            db.execute("INSERT INTO work_queue(queue_id,kind,media_id,state,created_at,updated_at,state_changed_at) VALUES (?,?,?,?,?,?,?)",
                       (qid, "LOCAL_MEDIA", media_id, "ready", iso(now), iso(now), iso(now)))
        store.audit("MEDIA_REGISTERED", recorded_by.strip()[:80], {"media_id": media_id, "rights_basis": rights_basis, "minors_visible": minors_visible,
                                                                    "analysis_allowed": True, "training_allowed": False}, now)
    return {"media_id": media_id, "duplicate": False}


def withdraw_rights(store: ResearchStore, media_id: str, *, reason: str, by: str, now: datetime) -> int:
    """The permission was withdrawn. The media can no longer be analysed, and every approved item made from it needs a new decision (the
    decision, content and history are kept). Returns how many approved items were suspended."""
    if not isinstance(reason, str) or len(reason.strip()) < 5:
        raise MediaError("a reason is required")
    with store.transaction() as db:
        row = db.execute("SELECT status FROM media_files WHERE media_id=?", (media_id,)).fetchone()
        if row is None:
            raise MediaError("no such media")
        db.execute("UPDATE media_files SET status='rights_withdrawn', status_at=?, status_reason=? WHERE media_id=?", (iso(now), reason.strip()[:200], media_id))
        n = db.execute("UPDATE review_items SET approval_state='review_required', source_issue='RIGHTS_WITHDRAWN', source_issue_at=?, updated_at=? "
                       "WHERE media_id=? AND review_status='approved' AND approval_state='current'", (iso(now), iso(now), media_id)).rowcount
        db.execute("UPDATE work_queue SET state='skipped', reason='RIGHTS_WITHDRAWN', state_changed_at=?, updated_at=? WHERE media_id=? AND state IN ('ready','awaiting_media','discovered')",
                   (iso(now), iso(now), media_id))
        store.audit("MEDIA_RIGHTS_WITHDRAWN", by.strip()[:80] or "unknown", {"media_id": media_id, "approved_items_suspended": n}, now)
    return n


def media_problems(row, max_file_mb: int) -> list[str]:
    """Why this registered file may not be analysed right now: rights withdrawn, file gone or changed, or too large."""
    problems = []
    if row["status"] != "active":
        problems.append("RIGHTS_WITHDRAWN")
        return problems
    path = Path(row["local_path"])
    if path.is_symlink() or not path.is_file():
        return ["MEDIA_FILE_MISSING"]
    size = path.stat().st_size
    if size > max_file_mb * 1024 * 1024:
        return ["FILE_TOO_LARGE"]
    if size != row["size_bytes"] or sha256_file(path) != row["sha256"]:
        problems.append("MEDIA_FILE_CHANGED")
    return problems
