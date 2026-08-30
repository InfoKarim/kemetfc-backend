import os
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

from fastapi import UploadFile

from app.config import (
    get_s3_bucket,
    get_s3_endpoint_url,
    get_s3_presigned_url_expiry_seconds,
    get_s3_region,
    get_video_storage_backend,
)


MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024
AVATAR_DIR = Path(__file__).parent.parent / "uploads" / "avatars"

_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class AvatarUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _detect_image_extension(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp"
    return None


def _validate_signature(source: BinaryIO) -> str:
    position = source.tell()
    try:
        header = source.read(16)
    finally:
        source.seek(position)

    extension = _detect_image_extension(header)
    if extension is None:
        raise AvatarUploadError(
            "File content is not a recognized image (JPEG, PNG, or WebP)"
        )
    return extension


class AvatarStorage(Protocol):
    def save(self, source: BinaryIO, filename: str, content_type: str) -> None:
        ...

    def delete(self, filename: str) -> None:
        ...

    def local_path(self, filename: str) -> Path | None:
        ...

    def create_download_url(self, filename: str) -> str | None:
        ...


def _write_with_size_limit(source: BinaryIO, destination: BinaryIO) -> None:
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_AVATAR_SIZE_BYTES:
            raise AvatarUploadError("Image exceeds the 5 MB limit")
        destination.write(chunk)


class LocalAvatarStorage:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or AVATAR_DIR

    def save(self, source: BinaryIO, filename: str, content_type: str) -> None:
        del content_type
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / filename

        try:
            with target.open("wb") as destination:
                _write_with_size_limit(source, destination)
        except AvatarUploadError:
            target.unlink(missing_ok=True)
            raise

    def delete(self, filename: str) -> None:
        (self.directory / filename).unlink(missing_ok=True)

    def local_path(self, filename: str) -> Path | None:
        return self.directory / filename

    def create_download_url(self, filename: str) -> str | None:
        return None


class S3AvatarStorage:
    def __init__(self, client=None, prefix: str | None = None):
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                region_name=get_s3_region(),
                endpoint_url=get_s3_endpoint_url(),
            )

        self.client = client
        self.bucket = get_s3_bucket()
        self.prefix = (
            os.getenv("S3_AVATAR_PREFIX", "avatars").strip().strip("/")
            if prefix is None
            else prefix
        )

    def _key(self, filename: str) -> str:
        return f"{self.prefix}/{filename}" if self.prefix else filename

    def save(self, source: BinaryIO, filename: str, content_type: str) -> None:
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(delete=False) as temporary:
                temporary_path = Path(temporary.name)
                _write_with_size_limit(source, temporary)

            self.client.upload_file(
                str(temporary_path),
                self.bucket,
                self._key(filename),
                ExtraArgs={"ContentType": content_type},
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self, filename: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(filename))

    def local_path(self, filename: str) -> Path | None:
        return None

    def create_download_url(self, filename: str) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._key(filename)},
            ExpiresIn=get_s3_presigned_url_expiry_seconds(),
        )


def get_avatar_storage() -> AvatarStorage:
    if get_video_storage_backend() == "s3":
        return S3AvatarStorage()

    return LocalAvatarStorage()


def save_avatar(user_id: str, file: UploadFile) -> str:
    if file.content_type not in _EXTENSION_BY_CONTENT_TYPE:
        raise AvatarUploadError("Only JPEG, PNG, or WebP images are allowed")

    extension = _validate_signature(file.file)
    filename = f"{user_id}{extension}"

    storage = get_avatar_storage()
    storage.save(file.file, filename, file.content_type)

    for other_extension in _EXTENSION_BY_CONTENT_TYPE.values():
        if other_extension != extension:
            storage.delete(f"{user_id}{other_extension}")

    return filename


def delete_avatar(filename: str) -> None:
    get_avatar_storage().delete(filename)


def avatar_local_path(filename: str) -> Path | None:
    return get_avatar_storage().local_path(filename)


def avatar_download_url(filename: str) -> str | None:
    return get_avatar_storage().create_download_url(filename)
