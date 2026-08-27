import io

import pytest

from app.video_file_validation import (
    InvalidVideoContent,
    validate_video_signature,
)


@pytest.mark.parametrize(
    ("filename", "header"),
    [
        ("training.mp4", b"\x00\x00\x00\x18ftypmp42"),
        ("training.mov", b"\x00\x00\x00\x18ftypqt  "),
        ("training.avi", b"RIFF\x10\x00\x00\x00AVI "),
        ("training.webm", b"\x1aE\xdf\xa3webm"),
    ],
)
def test_accepts_recognized_video_container(filename, header):
    source = io.BytesIO(header + b"payload")

    validate_video_signature(source, filename)

    assert source.tell() == 0


def test_rejects_disguised_non_video_file():
    source = io.BytesIO(b"this is not a video")

    with pytest.raises(InvalidVideoContent, match="recognized video"):
        validate_video_signature(source, "training.mp4")

    assert source.tell() == 0
