from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile


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


def save_avatar(user_id: str, file: UploadFile) -> str:
    if file.content_type not in _EXTENSION_BY_CONTENT_TYPE:
        raise AvatarUploadError("Only JPEG, PNG, or WebP images are allowed")

    extension = _validate_signature(file.file)

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}{extension}"
    target = AVATAR_DIR / filename

    size = 0
    with target.open("wb") as destination:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_AVATAR_SIZE_BYTES:
                destination.close()
                target.unlink(missing_ok=True)
                raise AvatarUploadError("Image exceeds the 5 MB limit")
            destination.write(chunk)

    for other_extension in _EXTENSION_BY_CONTENT_TYPE.values():
        if other_extension != extension:
            (AVATAR_DIR / f"{user_id}{other_extension}").unlink(missing_ok=True)

    return filename


def delete_avatar(filename: str) -> None:
    (AVATAR_DIR / filename).unlink(missing_ok=True)


def avatar_path(filename: str) -> Path:
    return AVATAR_DIR / filename
