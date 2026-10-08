"""The research database: separate from the application database, private to this engine, versioned and transactional.

Safety rules enforced here: the state directory and every file in it are created private (0700 / 0600), a symbolic
link anywhere on the way is refused, the directory is refused if it looks like it holds production data, and only one run
can hold the lock at a time. Nothing in this database ever feeds a training dataset.
"""
import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 5
DB_NAME = "learning_engine.sqlite3"
LOCK_NAME = "run.lock"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sources (
  video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, url TEXT NOT NULL,
  title TEXT, published_at TEXT, duration_s INTEGER, made_for_kids INTEGER, privacy_status TEXT,
  discovered_at TEXT NOT NULL, verified_at TEXT, query_version TEXT NOT NULL,
  text_fingerprint TEXT, duplicate_of TEXT REFERENCES sources(video_id), fingerprint_conflict_with TEXT,
  discovery_class TEXT NOT NULL DEFAULT 'ALLOWLISTED_CHANNEL' CHECK (discovery_class IN ('ALLOWLISTED_CHANNEL','PUBLIC_METADATA_DISCOVERY')),
  discovery_query TEXT,
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only')),
  availability TEXT NOT NULL DEFAULT 'unknown' CHECK (availability IN ('available','unavailable','unknown','excluded')),
  exclusion_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS sources_fingerprint_canonical ON sources(text_fingerprint) WHERE duplicate_of IS NULL AND text_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS media_files (
  media_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, local_path TEXT NOT NULL, display_name TEXT, size_bytes INTEGER NOT NULL,
  rights_basis TEXT NOT NULL CHECK (rights_basis IN ('OWN_RECORDING','OWNER_PERMISSION','LICENSED')),
  rights_grantor TEXT NOT NULL, rights_note TEXT, rights_recorded_by TEXT NOT NULL, rights_recorded_at TEXT NOT NULL,
  analysis_allowed INTEGER NOT NULL DEFAULT 1 CHECK (analysis_allowed = 1),
  training_allowed INTEGER NOT NULL DEFAULT 0 CHECK (training_allowed = 0),
  minors_visible TEXT NOT NULL CHECK (minors_visible IN ('no','yes','unknown')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','rights_withdrawn')), status_at TEXT, status_reason TEXT,
  registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
  analysis_id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_files(media_id), version INTEGER NOT NULL,
  analyzer_name TEXT NOT NULL, analyzer_version TEXT NOT NULL, method TEXT NOT NULL, is_fixture INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('knowledge_extracted','no_knowledge','failed')),
  started_at TEXT NOT NULL, finished_at TEXT NOT NULL, input_sha256 TEXT NOT NULL, duration_s REAL,
  rejected_notes INTEGER NOT NULL DEFAULT 0, uncertainties_json TEXT NOT NULL DEFAULT '[]', limits_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (media_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_notes (
  note_id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
  kind TEXT NOT NULL CHECK (kind IN ('observation','step','equipment','common_error','safety_note','measure_hint')),
  text TEXT NOT NULL, t_start_s REAL NOT NULL, t_end_s REAL NOT NULL, confidence REAL,
  origin TEXT NOT NULL DEFAULT 'ANALYZER_EXTRACTED' CHECK (origin = 'ANALYZER_EXTRACTED'),
  CHECK (t_start_s >= 0 AND t_end_s >= t_start_s)
);

CREATE TABLE IF NOT EXISTS review_items (
  item_id TEXT PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT','LOCAL_MEDIA')),
  source_video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL,
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only','ai_draft','analysis_draft','coach_reviewed')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
  source_state TEXT NOT NULL DEFAULT 'present' CHECK (source_state IN ('present','removed')),
  approval_state TEXT NOT NULL DEFAULT 'current' CHECK (approval_state IN ('current','review_required')),
  source_issue TEXT, source_issue_at TEXT, source_removed_at TEXT,
  media_id TEXT REFERENCES media_files(media_id), analysis_id TEXT, supersedes_item_id TEXT,
  CHECK ((origin = 'YOUTUBE_SOURCE' AND (source_video_id IS NOT NULL OR source_state = 'removed')) OR (origin = 'AI_GENERAL_DRAFT' AND source_video_id IS NULL AND media_id IS NULL)
         OR (origin = 'LOCAL_MEDIA' AND source_video_id IS NULL AND media_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS item_versions (
  item_id TEXT NOT NULL REFERENCES review_items(item_id), version INTEGER NOT NULL,
  content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
  author_kind TEXT NOT NULL CHECK (author_kind IN ('NONE','AI_DRAFT','ANALYZER','COACH')), author_ref TEXT,
  created_at TEXT NOT NULL, PRIMARY KEY (item_id, version)
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  reviewer_id TEXT NOT NULL, auth_mode TEXT NOT NULL, reasons TEXT NOT NULL, sources_json TEXT NOT NULL, at TEXT NOT NULL,
  review_basis TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  n INTEGER PRIMARY KEY, at TEXT NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL, data_json TEXT NOT NULL,
  previous TEXT NOT NULL, hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ai','youtube_search','youtube_other')), provider TEXT NOT NULL,
  requests INTEGER NOT NULL DEFAULT 1, units INTEGER NOT NULL DEFAULT 0,
  tokens_reserved INTEGER NOT NULL DEFAULT 0, usd_reserved REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('reserved','sent','settled','uncertain','rate_limited','released')),
  tokens_actual INTEGER, usd_actual REAL, created_at TEXT NOT NULL, sent_at TEXT, settled_at TEXT, note TEXT,
  CHECK (status <> 'released' OR sent_at IS NULL)
);
CREATE INDEX IF NOT EXISTS ledger_day ON ledger(day, kind, provider);

CREATE TABLE IF NOT EXISTS work_queue (
  queue_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('YOUTUBE_METADATA','LOCAL_MEDIA')),
  video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL, media_id TEXT REFERENCES media_files(media_id),
  state TEXT NOT NULL CHECK (state IN ('discovered','awaiting_media','ready','processing','draft_ready','skipped','failed')),
  reason TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, next_attempt_at TEXT,
  lease_owner TEXT, lease_until TEXT, analysis_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state_changed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS work_queue_video ON work_queue(video_id) WHERE video_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_queue_media ON work_queue(media_id) WHERE media_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS recommendations (
  rec_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, player_ref TEXT NOT NULL, is_fixture INTEGER NOT NULL DEFAULT 0,
  request_json TEXT NOT NULL, item_id TEXT, item_version INTEGER, content_hash TEXT, personalization TEXT NOT NULL,
  reason_json TEXT NOT NULL, evidence_json TEXT NOT NULL, measure_json TEXT, missing_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('awaiting_coach_review','coach_approved_for_use','coach_rejected','needs_rereview','not_generated')),
  status_reason TEXT, status_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, rec_id TEXT NOT NULL REFERENCES recommendations(rec_id), at TEXT NOT NULL, event TEXT NOT NULL,
  actor TEXT NOT NULL, detail TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, rec_id TEXT REFERENCES recommendations(rec_id), player_ref TEXT NOT NULL, item_id TEXT,
  is_fixture INTEGER NOT NULL DEFAULT 0, session_date TEXT NOT NULL, executed INTEGER NOT NULL CHECK (executed IN (0,1)),
  measure_name TEXT, unit TEXT, before_value REAL, after_value REAL, method_before TEXT, method_after TEXT,
  conditions TEXT, coach_notes TEXT, recorded_by TEXT NOT NULL, auth_mode TEXT NOT NULL, recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  day TEXT NOT NULL, mode TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, checkpoint_json TEXT NOT NULL DEFAULT '{}', report_json TEXT,
  PRIMARY KEY (day, mode)
);
"""


class StoreError(RuntimeError):
    pass


class AlreadyRunning(StoreError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refuse_symlinks(path: Path) -> None:
    probe = Path(path.anchor)
    for part in path.parts[1:]:
        probe = probe / part
        # a symlink made by a user is refused; the operating system's own (owned by root, such as /var -> /private/var) is not
        if probe.is_symlink() and probe.lstat().st_uid != 0:
            raise StoreError("a symbolic link on the state path is refused")


def validate_state_dir(path: Path, repo_root: Path | None = None) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise StoreError("the state directory must be an absolute path")
    _refuse_symlinks(path)
    resolved = path.resolve()
    for name in ("cloud_platform.db", "uploads", "soccer_ai.db", "alembic.ini"):
        if (resolved / name).exists():
            raise StoreError("this directory looks like the application's data: the research state must live in its own directory")
    if repo_root is not None:
        root = Path(repo_root).resolve()
        if resolved == root or root in resolved.parents:
            raise StoreError("the research state must live outside the application repository")
    return resolved


class ResearchStore:
    def __init__(self, state_dir: Path, repo_root: Path | None = None):
        self.dir = validate_state_dir(state_dir, repo_root)
        old = os.umask(0o077)
        try:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        finally:
            os.umask(old)
        os.chmod(self.dir, 0o700)
        self.db_path = self.dir / DB_NAME
        if self.db_path.is_symlink():
            raise StoreError("a symbolic link as the database is refused")
        old = os.umask(0o077)
        try:
            self.db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        finally:
            os.umask(old)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        self._migrate()
        self._tighten()
        self._lock_fd = None

    def _tighten(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(self.db_path) + suffix)
            if f.exists() and not f.is_symlink():
                os.chmod(f, 0o600)

    def _migrate(self) -> None:
        row = None
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:
            pass
        version = int(row["value"]) if row is not None else None
        if version is not None and version > SCHEMA_VERSION:
            raise StoreError("this database was made by a newer version of the engine")
        while version is not None and version < SCHEMA_VERSION:
            {1: self._upgrade_from_1, 2: self._upgrade_2_to_3, 3: self._upgrade_3_to_4, 4: self._upgrade_4_to_5}[version]()
            version = self.schema_version()
        if version is not None:
            return
        with self.transaction():
            for statement in _statements(SCHEMA):
                self.db.execute(statement)
            self.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))

    def _upgrade_from_1(self) -> None:
        """Version 1 -> current (3, built directly in the final shape). Conservative about approvals (see below) and about money and requests: in version 1 a 'released' AI reservation could follow a request that
        WAS sent (the defect this version fixes), so every version-1 AI row that is not plainly settled becomes 'uncertain' and keeps
        counting. Nothing is deleted."""
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("PRAGMA legacy_alter_table=ON")      # a rename must not rewrite the child tables' REFERENCES to the old name
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("ALTER TABLE sources ADD COLUMN fingerprint_conflict_with TEXT")
                self.db.execute("ALTER TABLE ledger RENAME TO ledger_v1")
                self.db.execute("DROP INDEX IF EXISTS ledger_day")
                for statement in _statements(SCHEMA):
                    if statement.lstrip().startswith(("CREATE TABLE IF NOT EXISTS ledger", "CREATE INDEX IF NOT EXISTS ledger_day")):
                        self.db.execute(statement)
                self.db.execute("""INSERT INTO ledger(id,day,kind,provider,requests,units,tokens_reserved,usd_reserved,status,tokens_actual,usd_actual,created_at,sent_at,settled_at,note)
                    SELECT id,day,kind,provider,requests,units,tokens_reserved,usd_reserved,
                           CASE WHEN kind='ai' AND status IN ('reserved','released') THEN 'uncertain' ELSE status END,
                           tokens_actual,usd_actual,created_at,
                           CASE WHEN kind='ai' THEN created_at ELSE NULL END, settled_at,
                           CASE WHEN kind='ai' AND status IN ('reserved','released') THEN 'migrated from version 1: may have been sent' ELSE note END
                    FROM ledger_v1""")
                self.db.execute("DROP TABLE ledger_v1")
                self.db.execute("ALTER TABLE review_items RENAME TO review_items_v1")
                for statement in _statements(SCHEMA):
                    if statement.lstrip().startswith("CREATE TABLE IF NOT EXISTS review_items"):
                        self.db.execute(statement)
                self.db.execute("""INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published)
                    SELECT item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published FROM review_items_v1""")
                self.db.execute("DROP TABLE review_items_v1")
                self.db.execute("ALTER TABLE reviews ADD COLUMN review_basis TEXT")
                # Version 1 let an approved item keep its approval after its source was excluded or lost, and nothing recorded that the
                # approval had been suspended. So an approval is carried over as valid ONLY if the source is recorded as available. A
                # source that is excluded, unavailable, unknown or missing suspends it (`review_required`) with the recorded reason and
                # date, or UNDETERMINABLE_LEGACY_STATE when none was recorded. An old REVIEW_APPROVED audit event proves nothing about
                # the source and never overrides this. Nothing is deleted.
                suspended = self.db.execute(
                    "UPDATE review_items SET approval_state='review_required', "
                    "source_issue=COALESCE((SELECT NULLIF(s.exclusion_reason,'') FROM sources s WHERE s.video_id=review_items.source_video_id), 'UNDETERMINABLE_LEGACY_STATE'), "
                    "source_issue_at=(SELECT s.verified_at FROM sources s WHERE s.video_id=review_items.source_video_id) "
                    "WHERE review_status='approved' AND origin='YOUTUBE_SOURCE' AND (source_video_id IS NULL OR NOT EXISTS "
                    "(SELECT 1 FROM sources s WHERE s.video_id=review_items.source_video_id AND s.availability='available'))").rowcount
                if suspended:
                    self.audit("LEGACY_APPROVALS_SUSPENDED_AT_UPGRADE", "system", {"from_schema": 1, "count": suspended})
                self.db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
                violations = self.db.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise StoreError("the upgrade would leave broken references; it was not applied")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        finally:
            self.db.execute("PRAGMA legacy_alter_table=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")

    def _upgrade_2_to_3(self) -> None:
        """Version 2 -> 3. Splits two things version 2 had put in one column: whether the coach's APPROVAL is still valid
        (`approval_state`) and whether the source DATA is still held (`source_state`). In version 2 a retention purge overwrote
        'review_required' with 'removed', so an item whose source had been invalidated could look approved again. For every
        version-2 item in 'removed' state the audit log decides: if it shows an invalidation after the last approval the item stays
        `review_required` with that reason and date; if the audit log cannot show that (chain broken, or nothing about the item), the
        item is `review_required` with reason UNDETERMINABLE_LEGACY_STATE. It is never assumed valid. Nothing is deleted."""
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("PRAGMA legacy_alter_table=ON")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("ALTER TABLE review_items RENAME TO review_items_v2")
                for statement in _statements(SCHEMA):
                    if statement.lstrip().startswith("CREATE TABLE IF NOT EXISTS review_items"):
                        self.db.execute(statement)
                self.db.execute("""INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published,
                                                            source_state,approval_state,source_issue,source_issue_at,source_removed_at)
                    SELECT item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published,
                           CASE WHEN source_state='removed' THEN 'removed' ELSE 'present' END,
                           CASE WHEN source_state='review_required' THEN 'review_required' ELSE 'current' END,
                           source_issue,source_issue_at,source_removed_at FROM review_items_v2""")
                chain_ok = self.verify_audit()
                events = [dict(r, data=json.loads(r["data_json"])) for r in self.db.execute("SELECT n, event, data_json FROM audit ORDER BY n")]
                for item in self.db.execute("SELECT item_id, review_status, source_issue FROM review_items WHERE source_state='removed' AND approval_state='current' "
                                            "AND review_status='approved'").fetchall():
                    mine = [e for e in events if e["data"].get("item_id") == item["item_id"]]
                    approvals = [e["n"] for e in mine if e["event"] == "REVIEW_APPROVED"]
                    invalid = [e for e in mine if e["event"] == "SOURCE_INVALIDATED_REVIEW_REQUIRED" and approvals and e["n"] > max(approvals)]
                    if invalid:
                        reason = invalid[-1]["data"].get("reason") or "UNDETERMINABLE_LEGACY_STATE"
                        at = self.db.execute("SELECT at FROM audit WHERE n=?", (invalid[-1]["n"],)).fetchone()["at"]
                    elif chain_ok and approvals:
                        continue                                      # the audit log shows an approval and no later invalidation
                    else:
                        reason, at = "UNDETERMINABLE_LEGACY_STATE", None
                    self.db.execute("UPDATE review_items SET approval_state='review_required', source_issue=?, source_issue_at=? WHERE item_id=?",
                                    (reason, at, item["item_id"]))
                self.db.execute("DROP TABLE review_items_v2")
                self.db.execute("ALTER TABLE reviews ADD COLUMN review_basis TEXT")
                self.db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
                if self.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise StoreError("the upgrade would leave broken references; it was not applied")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        finally:
            self.db.execute("PRAGMA legacy_alter_table=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")

    def _upgrade_3_to_4(self) -> None:
        """Version 3 -> 4: records HOW each source was found. Every existing source came from an allowlisted channel, which is the default
        class. Two added columns, one transaction."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("ALTER TABLE sources ADD COLUMN discovery_class TEXT NOT NULL DEFAULT 'ALLOWLISTED_CHANNEL' "
                            "CHECK (discovery_class IN ('ALLOWLISTED_CHANNEL','PUBLIC_METADATA_DISCOVERY'))")
            self.db.execute("ALTER TABLE sources ADD COLUMN discovery_query TEXT")
            self.db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _upgrade_4_to_5(self) -> None:
        """Version 4 -> 5: the knowledge library, work queue, recommendations and outcomes (new tables), and review_items / item_versions
        rebuilt so that an item can come from local media and a version can be authored by an analyzer. Every old row is copied unchanged;
        nothing is deleted. One transaction; any broken reference rolls everything back."""
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("PRAGMA legacy_alter_table=ON")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("ALTER TABLE review_items RENAME TO review_items_v4")
                self.db.execute("ALTER TABLE item_versions RENAME TO item_versions_v4")
                for statement in _statements(SCHEMA):
                    head = statement.lstrip()
                    if head.startswith("CREATE TABLE IF NOT EXISTS") and not head.startswith(("CREATE TABLE IF NOT EXISTS meta", "CREATE TABLE IF NOT EXISTS sources",
                                                                                              "CREATE TABLE IF NOT EXISTS reviews", "CREATE TABLE IF NOT EXISTS audit",
                                                                                              "CREATE TABLE IF NOT EXISTS ledger", "CREATE TABLE IF NOT EXISTS runs")):
                        self.db.execute(statement)
                    elif head.startswith("CREATE UNIQUE INDEX IF NOT EXISTS work_queue"):
                        self.db.execute(statement)
                self.db.execute("""INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published,
                                                            source_state,approval_state,source_issue,source_issue_at,source_removed_at)
                    SELECT item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published,
                           source_state,approval_state,source_issue,source_issue_at,source_removed_at FROM review_items_v4""")
                self.db.execute("INSERT INTO item_versions SELECT * FROM item_versions_v4")
                self.db.execute("DROP TABLE item_versions_v4")
                self.db.execute("DROP TABLE review_items_v4")
                self.db.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
                if self.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise StoreError("the upgrade would leave broken references; it was not applied")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        finally:
            self.db.execute("PRAGMA legacy_alter_table=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def schema_version(self) -> int:
        return int(self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"])

    # -- single instance ------------------------------------------------------------------------------------------------
    def acquire_run_lock(self) -> None:
        path = self.dir / LOCK_NAME
        if path.is_symlink():
            raise StoreError("a symbolic link as the lock file is refused")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise AlreadyRunning("another run holds the lock") from exc
        self._lock_fd = fd

    def release_run_lock(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def close(self) -> None:
        self.release_run_lock()
        self.db.close()

    # -- audit (tamper-evident hash chain) -----------------------------------------------------------------------------
    def audit(self, event: str, actor: str, data: dict, now: datetime | None = None) -> dict:
        last = self.db.execute("SELECT n, hash FROM audit ORDER BY n DESC LIMIT 1").fetchone()
        n, previous = (last["n"] + 1, last["hash"]) if last else (1, "0" * 64)
        at = iso(now or utc_now())
        body = {"n": n, "at": at, "event": event, "actor": actor, "data": data, "previous": previous}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", (n, at, event, actor, json.dumps(data, sort_keys=True), previous, digest))
        return {**body, "hash": digest}

    def verify_audit(self) -> bool:
        previous = "0" * 64
        for row in self.db.execute("SELECT * FROM audit ORDER BY n"):
            body = {"n": row["n"], "at": row["at"], "event": row["event"], "actor": row["actor"],
                    "data": json.loads(row["data_json"]), "previous": row["previous"]}
            if row["previous"] != previous or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != row["hash"]:
                return False
            previous = row["hash"]
        return True


def _statements(script: str) -> list[str]:
    """Split the schema into single statements (sqlite3's executescript commits first, which would break our transaction)."""
    out, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        if line.rstrip().endswith(";"):
            out.append("\n".join(buf))
            buf = []
    return [s for s in out if s.strip()]
