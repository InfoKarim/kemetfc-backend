"""Test-session safety: fail closed instead of touching real operational data.

Before any application module is imported, this file points every file or
database location the application reads from the environment at a throwaway
directory, and refuses to run if the environment points inside the repository
(where the real ``uploads/`` folders and ``cloud_platform.db`` live). At the end
of the session it checks that those real locations are byte-for-byte and
timestamp-for-timestamp what they were at the start.

Why: an early baseline run wrote synthetic test files into the real
``uploads/videos`` folder, and the avatar tests wrote into ``uploads/avatars``
because that path is a module constant, not an environment setting.
"""
import os
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_ROOT = Path(tempfile.mkdtemp(prefix="kemet-pytest-"))

# Environment settings that name a directory the application may write to.
_DIRECTORY_SETTINGS = {
    "PLAYER_VIDEO_UPLOAD_DIR": "videos",
    "DRILL_UPLOAD_DIR": "drills",
    "VIDEO_ANALYSIS_OUTPUT_DIR": "analysis_results",
}

# Real locations that must be unchanged when the session ends.
_GUARDED_PATHS = [
    REPO_ROOT / "uploads",
    REPO_ROOT / "analysis" / "results",
    REPO_ROOT / "cloud_platform.db",
]


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def _refuse(message: str) -> None:
    raise pytest.UsageError(f"Refusing to run tests against real data: {message}")


for _name, _folder in _DIRECTORY_SETTINGS.items():
    _value = os.environ.get(_name)
    if _value is None:
        os.environ[_name] = str(SCRATCH_ROOT / _folder)
    elif _inside_repo(Path(_value).expanduser() if Path(_value).is_absolute() else REPO_ROOT / _value):
        _refuse(f"{_name} points inside the repository ({_value}).")

def sqlite_file(url: str) -> Path | None:
    """The file a SQLite URL points at (relative paths resolve from the repository,
    which is where the application is started), or None for non-file databases."""
    if not url.startswith("sqlite") or "://" not in url:
        return None
    location = url.split("://", 1)[1]
    if location in ("", "/:memory:", ":memory:"):
        return None
    if url.startswith("sqlite:////"):
        return Path(location[1:] if location.startswith("//") else location)
    return REPO_ROOT / location.lstrip("/")


_database_url = os.environ.get("DATABASE_URL")
if _database_url is None:
    os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH_ROOT / 'tests.db'}"
else:
    _file = sqlite_file(_database_url)
    if _file is not None and _inside_repo(_file):
        _refuse(f"DATABASE_URL names a SQLite file inside the repository ({_database_url}).")

# The avatar folder is a module constant, not an environment setting.
import app.avatar_upload as _avatar_upload  # noqa: E402

_avatar_upload.AVATAR_DIR = SCRATCH_ROOT / "avatars"


def _fingerprint(path: Path) -> list[tuple]:
    if not path.exists():
        return [(str(path), "absent")]
    entries = [path] if path.is_file() else [path, *sorted(path.rglob("*"))]
    rows = []
    for entry in entries:
        info = entry.stat()
        rows.append((str(entry), info.st_size, info.st_mtime_ns,
                     getattr(info, "st_birthtime_ns", None) or info.st_ctime_ns))
    return rows


_BEFORE = {str(path): _fingerprint(path) for path in _GUARDED_PATHS}


def pytest_sessionfinish(session, exitstatus):
    changed = [
        path for path in _GUARDED_PATHS
        if _fingerprint(path) != _BEFORE[str(path)]
    ]
    if changed:
        print(f"\nREAL DATA TOUCHED BY THE TEST SESSION: {[str(p) for p in changed]}")
        session.exitstatus = 1
