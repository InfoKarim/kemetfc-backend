import re
from pathlib import Path

from fastapi import UploadFile

from app.video_storage import (
    VideoStorageError,
    get_drill_video_storage,
)
from app.video_file_validation import (
    InvalidVideoContent,
    validate_video_signature,
)


MAX_VIDEO_SIZE_BYTES = 500 * 1024 * 1024

SUPPORTED_DRILL_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
}


class DrillUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def save_drill_video(
    video: UploadFile,
    drill_id: str,
) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", drill_id):
        raise DrillUploadError("Invalid drill_id for upload")

    filename = video.filename or ""
    extension = Path(filename).suffix.lower()
    expected_content_type = SUPPORTED_DRILL_VIDEO_TYPES.get(extension)

    if expected_content_type is None:
        raise DrillUploadError("Unsupported video format")

    if video.content_type and video.content_type not in {
        expected_content_type,
        "application/octet-stream",
    }:
        raise DrillUploadError("File content type does not match its video format")

    try:
        validate_video_signature(video.file, filename)
    except InvalidVideoContent as error:
        raise DrillUploadError(str(error)) from error

    try:
        stored = get_drill_video_storage(MAX_VIDEO_SIZE_BYTES).save(
            video.file,
            f"{drill_id}{extension}",
            expected_content_type,
        )
    except VideoStorageError as error:
        raise DrillUploadError(
            str(error),
            status_code=error.status_code,
        ) from error

    return f"/uploads/drills/{stored.filename}"


def get_drill_video_path(filename: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+[.](mp4|mov|avi|mkv|webm|m4v)", filename):
        raise DrillUploadError("Invalid video filename")

    try:
        target = get_drill_video_storage().local_path(filename)
    except VideoStorageError as error:
        raise DrillUploadError(
            str(error),
            status_code=error.status_code,
        ) from error

    if target is None:
        raise DrillUploadError("Video is stored remotely", status_code=409)
    return target
