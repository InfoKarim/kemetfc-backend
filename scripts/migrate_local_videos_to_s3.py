#!/usr/bin/env python3
import argparse
import hashlib
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_video_storage_backend  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.db_models import VideoDB  # noqa: E402
from app.video_storage import S3VideoStorage  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy existing player videos to private S3 storage.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload files. Without this option, only show a dry run.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(
            os.getenv("PLAYER_VIDEO_UPLOAD_DIR", "uploads/videos")
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if get_video_storage_backend() != "s3":
        raise SystemExit("Set VIDEO_STORAGE_BACKEND=s3 before migration")

    storage = S3VideoStorage()
    database = SessionLocal()
    ready = []
    missing = []
    mismatched = []

    try:
        videos = database.query(VideoDB).order_by(VideoDB.video_id).all()

        for video in videos:
            if not video.file_path.startswith("/uploads/videos/"):
                continue

            filename = Path(video.file_path).name
            source_path = args.source_dir / filename

            if not source_path.is_file():
                missing.append(filename)
                continue

            actual_checksum = sha256_file(source_path)

            if video.checksum and actual_checksum != video.checksum:
                mismatched.append(filename)
                continue

            ready.append((video, filename, source_path))

        print(f"Ready: {len(ready)}")
        print(f"Missing local files: {len(missing)}")
        print(f"Checksum mismatches: {len(mismatched)}")

        for _, filename, _ in ready:
            print(f"  {filename}")

        if missing:
            print("Missing:")
            for filename in missing:
                print(f"  {filename}")

        if mismatched:
            print("Checksum mismatch:")
            for filename in mismatched:
                print(f"  {filename}")

        if missing or mismatched:
            print("Migration stopped; resolve all file issues first.")
            return 1

        if not args.apply:
            print("Dry run only. Re-run with --apply to upload.")
            return 0

        for video, filename, source_path in ready:
            content_type = {
                "mp4": "video/mp4",
                "mov": "video/quicktime",
                "avi": "video/x-msvideo",
                "mkv": "video/x-matroska",
                "webm": "video/webm",
                "m4v": "video/x-m4v",
            }.get(video.file_format.lower(), "application/octet-stream")

            with source_path.open("rb") as source:
                stored = storage.save(source, filename, content_type)

            if video.checksum and stored.checksum != video.checksum:
                storage.delete(filename)
                raise RuntimeError(
                    f"Uploaded checksum verification failed: {filename}"
                )

            print(f"Uploaded: {filename}")

        print(f"Migration complete: {len(ready)} video(s) uploaded")
        print("Local files were preserved.")
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
