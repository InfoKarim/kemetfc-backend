import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, ContextManager, Iterator, Protocol

from app.config import (
    get_s3_bucket,
    get_s3_endpoint_url,
    get_s3_prefix,
    get_s3_presigned_url_expiry_seconds,
    get_s3_region,
    get_video_storage_backend,
)


MAX_PLAYER_VIDEO_SIZE_BYTES = 500 * 1024 * 1024
VIDEO_FILENAME_PATTERN = re.compile(
    r"[A-Za-z0-9_-]+[.](mp4|mov|avi|mkv|webm|m4v)",
    flags=re.IGNORECASE,
)


class VideoStorageError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class StoredVideoObject:
    filename: str
    checksum: str
    size_bytes: int


class VideoStorage(Protocol):
    def save(
        self,
        source: BinaryIO,
        filename: str,
        content_type: str,
    ) -> StoredVideoObject:
        ...

    def delete(self, filename: str) -> None:
        ...

    def local_path(self, filename: str) -> Path | None:
        ...

    def create_download_url(self, filename: str) -> str | None:
        ...

    def materialize(self, filename: str) -> ContextManager[Path]:
        ...


def validate_video_filename(filename: str) -> str:
    if not VIDEO_FILENAME_PATTERN.fullmatch(filename):
        raise VideoStorageError("Invalid video filename")

    return filename


def _copy_and_hash(
    source: BinaryIO,
    target: BinaryIO,
    max_size_bytes: int = MAX_PLAYER_VIDEO_SIZE_BYTES,
    size_error: str = "Video exceeds the 500 MB limit",
) -> tuple[int, str]:
    total_size = 0
    digest = hashlib.sha256()

    while chunk := source.read(1024 * 1024):
        total_size += len(chunk)

        if total_size > max_size_bytes:
            raise VideoStorageError(
                size_error,
                status_code=413,
            )

        digest.update(chunk)
        target.write(chunk)

    if total_size == 0:
        raise VideoStorageError("Video file cannot be empty")

    return total_size, digest.hexdigest()


class LocalVideoStorage:
    def __init__(
        self,
        directory: Path | None = None,
        max_size_bytes: int = MAX_PLAYER_VIDEO_SIZE_BYTES,
        size_error: str = "Video exceeds the 500 MB limit",
    ):
        self.directory = directory or Path(
            os.getenv("PLAYER_VIDEO_UPLOAD_DIR", "uploads/videos")
        )
        self.max_size_bytes = max_size_bytes
        self.size_error = size_error

    def _path(self, filename: str) -> Path:
        return self.directory / validate_video_filename(filename)

    def save(
        self,
        source: BinaryIO,
        filename: str,
        content_type: str,
    ) -> StoredVideoObject:
        del content_type
        target = self._path(filename)
        temporary = target.with_name(f"{target.name}.part")
        self.directory.mkdir(parents=True, exist_ok=True)

        try:
            with temporary.open("wb") as output:
                size_bytes, checksum = _copy_and_hash(
                    source,
                    output,
                    self.max_size_bytes,
                    self.size_error,
                )
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return StoredVideoObject(filename, checksum, size_bytes)

    def delete(self, filename: str) -> None:
        self._path(filename).unlink(missing_ok=True)

    def local_path(self, filename: str) -> Path | None:
        target = self._path(filename)

        if not target.is_file():
            raise VideoStorageError("Video not found", status_code=404)

        return target

    def create_download_url(self, filename: str) -> str | None:
        self.local_path(filename)
        return None

    @contextmanager
    def materialize(self, filename: str) -> Iterator[Path]:
        yield self.local_path(filename)


class S3VideoStorage:
    def __init__(
        self,
        client=None,
        prefix: str | None = None,
        max_size_bytes: int = MAX_PLAYER_VIDEO_SIZE_BYTES,
        size_error: str = "Video exceeds the 500 MB limit",
    ):
        if client is None:
            try:
                import boto3
            except ImportError as error:
                raise RuntimeError(
                    "S3 video storage requires the boto3 package"
                ) from error

            client = boto3.client(
                "s3",
                region_name=get_s3_region(),
                endpoint_url=get_s3_endpoint_url(),
            )

        self.client = client
        self.bucket = get_s3_bucket()
        self.prefix = get_s3_prefix() if prefix is None else prefix
        self.max_size_bytes = max_size_bytes
        self.size_error = size_error

    def _key(self, filename: str) -> str:
        name = validate_video_filename(filename)
        return f"{self.prefix}/{name}" if self.prefix else name

    def save(
        self,
        source: BinaryIO,
        filename: str,
        content_type: str,
    ) -> StoredVideoObject:
        key = self._key(filename)
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(delete=False) as temporary:
                temporary_path = Path(temporary.name)
                size_bytes, checksum = _copy_and_hash(
                    source,
                    temporary,
                    self.max_size_bytes,
                    self.size_error,
                )

            self.client.upload_file(
                str(temporary_path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": checksum},
                },
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return StoredVideoObject(filename, checksum, size_bytes)

    def delete(self, filename: str) -> None:
        self.client.delete_object(
            Bucket=self.bucket,
            Key=self._key(filename),
        )

    def local_path(self, filename: str) -> Path | None:
        validate_video_filename(filename)
        return None

    def create_download_url(self, filename: str) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self._key(filename),
            },
            ExpiresIn=get_s3_presigned_url_expiry_seconds(),
        )

    @contextmanager
    def materialize(self, filename: str) -> Iterator[Path]:
        suffix = Path(validate_video_filename(filename)).suffix
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)

            self.client.download_file(
                self.bucket,
                self._key(filename),
                str(temporary_path),
            )
            yield temporary_path
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def get_video_storage() -> VideoStorage:
    if get_video_storage_backend() == "s3":
        return S3VideoStorage()

    return LocalVideoStorage()


def get_drill_video_storage(
    max_size_bytes: int = 500 * 1024 * 1024,
) -> VideoStorage:
    size_error = "Video exceeds the 500 MB limit"

    if get_video_storage_backend() == "s3":
        prefix = os.getenv("S3_DRILL_PREFIX", "drills").strip().strip("/")
        return S3VideoStorage(
            prefix=prefix,
            max_size_bytes=max_size_bytes,
            size_error=size_error,
        )

    return LocalVideoStorage(
        directory=Path(os.getenv("DRILL_UPLOAD_DIR", "uploads/drills")),
        max_size_bytes=max_size_bytes,
        size_error=size_error,
    )
