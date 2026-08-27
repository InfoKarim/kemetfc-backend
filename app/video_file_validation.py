from pathlib import Path
from typing import BinaryIO


class InvalidVideoContent(ValueError):
    pass


def validate_video_signature(source: BinaryIO, filename: str) -> None:
    position = source.tell()

    try:
        header = source.read(16)
    finally:
        source.seek(position)

    extension = Path(filename).suffix.lower()
    is_iso_media = len(header) >= 8 and header[4:8] == b"ftyp"
    signatures = {
        ".mp4": is_iso_media,
        ".mov": is_iso_media,
        ".m4v": is_iso_media,
        ".avi": header.startswith(b"RIFF") and header[8:12] == b"AVI ",
        ".mkv": header.startswith(b"\x1aE\xdf\xa3"),
        ".webm": header.startswith(b"\x1aE\xdf\xa3"),
    }

    if not signatures.get(extension, False):
        raise InvalidVideoContent(
            "File content is not a recognized video container"
        )
