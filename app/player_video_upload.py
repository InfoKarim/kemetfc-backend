import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.video_storage import (
    VideoStorageError,
    get_video_storage,
    validate_video_filename,
)
from app.video_file_validation import (
    InvalidVideoContent,
    validate_video_signature,
)


SUPPORTED_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
}


class PlayerVideoUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SavedPlayerVideo:
    public_path: str
    filename: str
    checksum: str
    file_size_mb: float
    file_format: str
    content_type: str


def save_player_video(
    video: UploadFile,
    video_id: str,
) -> SavedPlayerVideo:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", video_id):
        raise PlayerVideoUploadError(
            "Invalid video_id for upload"
        )

    filename = video.filename or ""

    extension = Path(filename).suffix.lower()
    expected_content_type = SUPPORTED_VIDEO_TYPES.get(extension)

    if expected_content_type is None:
        raise PlayerVideoUploadError(
            "Unsupported video format"
        )

    if video.content_type and video.content_type not in {
        expected_content_type,
        "application/octet-stream",
    }:
        raise PlayerVideoUploadError(
            "File content type does not match its video format"
        )

    try:
        validate_video_signature(video.file, filename)
    except InvalidVideoContent as error:
        raise PlayerVideoUploadError(str(error)) from error

    try:
        filename = f"{video_id}{extension}"
        stored = get_video_storage().save(
            source=video.file,
            filename=filename,
            content_type=expected_content_type,
        )
    except VideoStorageError as error:
        raise PlayerVideoUploadError(
            str(error),
            status_code=error.status_code,
        ) from error

    return SavedPlayerVideo(
        public_path=f"/uploads/videos/{stored.filename}",
        filename=stored.filename,
        checksum=stored.checksum,
        file_size_mb=stored.size_bytes / (1024 * 1024),
        file_format=extension.removeprefix("."),
        content_type=expected_content_type,
    )


def get_player_video_path(filename: str) -> Path:
    try:
        validate_video_filename(filename)
        path = get_video_storage().local_path(filename)
    except VideoStorageError as error:
        raise PlayerVideoUploadError(
            str(error), status_code=error.status_code
        ) from error

    if path is None:
        raise PlayerVideoUploadError(
            "Video is stored remotely",
            status_code=409,
        )

    return path


def delete_player_video(filename: str) -> None:
    get_video_storage().delete(filename)
