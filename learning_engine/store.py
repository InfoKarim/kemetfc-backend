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

SCHEMA_VERSION = 1
DB_NAME = "learning_engine.sqlite3"
LOCK_NAME = "run.lock"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sources (
  video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, url TEXT NOT NULL,
  title TEXT, published_at TEXT, duration_s INTEGER, made_for_kids INTEGER, privacy_status TEXT,
  discovered_at TEXT NOT NULL, verified_at TEXT, query_version TEXT NOT NULL,
  text_fingerprint TEXT, duplicate_of TEXT REFERENCES sources(video_id),
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only')),
  availability TEXT NOT NULL DEFAULT 'unknown' CHECK (availability IN ('available','unavailable','unknown','excluded')),
  exclusion_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS sources_fingerprint_canonical ON sources(text_fingerprint) WHERE duplicate_of IS NULL AND text_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS review_items (
  item_id TEXT PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT')),
  source_video_id TEXT REFERENCES sources(video_id),
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only','ai_draft','coach_reviewed')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
  CHECK ((origin = 'YOUTUBE_SOURCE' AND source_video_id IS NOT NULL) OR (origin = 'AI_GENERAL_DRAFT' AND source_video_id IS NULL))
);

CREATE TABLE IF NOT EXISTS item_versions (
  item_id TEXT NOT NULL REFERENCES review_items(item_id), version INTEGER NOT NULL,
  content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
  author_kind TEXT NOT NULL CHECK (author_kind IN ('NONE','AI_DRAFT','COACH')), author_ref TEXT,
  created_at TEXT NOT NULL, PRIMARY KEY (item_id, version)
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  reviewer_id TEXT NOT NULL, auth_mode TEXT NOT NULL, reasons TEXT NOT NULL, sources_json TEXT NOT NULL, at TEXT NOT NULL
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
  status TEXT NOT NULL CHECK (status IN ('reserved','settled','uncertain','released')),
  tokens_actual INTEGER, usd_actual REAL, created_at TEXT NOT NULL, settled_at TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS ledger_day ON ledger(day, kind, provider);

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
        if row is not None and int(row["value"]) > SCHEMA_VERSION:
            raise StoreError("this database was made by a newer version of the engine")
        with self.transaction():
            for statement in _statements(SCHEMA):
                self.db.execute(statement)
            self.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))

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
