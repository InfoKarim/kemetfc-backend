import pytest

from app import drill_upload
from app.video_storage import VideoStorageError


def test_get_drill_video_path_rejects_invalid_filename():
    with pytest.raises(drill_upload.DrillUploadError, match="Invalid video filename"):
        drill_upload.get_drill_video_path("../etc/passwd")


def test_get_drill_video_path_rejects_unsupported_extension():
    with pytest.raises(drill_upload.DrillUploadError, match="Invalid video filename"):
        drill_upload.get_drill_video_path("drill123.txt")


def test_get_drill_video_path_returns_local_path(monkeypatch):
    class FakeStorage:
        def local_path(self, filename):
            return f"/tmp/drills/{filename}"

    monkeypatch.setattr(
        drill_upload, "get_drill_video_storage", lambda *args, **kwargs: FakeStorage()
    )

    result = drill_upload.get_drill_video_path("drill123.mp4")

    assert str(result) == "/tmp/drills/drill123.mp4"


def test_get_drill_video_path_raises_409_when_stored_remotely(monkeypatch):
    class FakeStorage:
        def local_path(self, filename):
            return None

    monkeypatch.setattr(
        drill_upload, "get_drill_video_storage", lambda *args, **kwargs: FakeStorage()
    )

    with pytest.raises(drill_upload.DrillUploadError, match="stored remotely") as excinfo:
        drill_upload.get_drill_video_path("drill123.mp4")

    assert excinfo.value.status_code == 409


def test_get_drill_video_path_wraps_video_storage_error(monkeypatch):
    class FakeStorage:
        def local_path(self, filename):
            raise VideoStorageError("storage misconfigured", status_code=500)

    monkeypatch.setattr(
        drill_upload, "get_drill_video_storage", lambda *args, **kwargs: FakeStorage()
    )

    with pytest.raises(drill_upload.DrillUploadError, match="storage misconfigured") as excinfo:
        drill_upload.get_drill_video_path("drill123.mp4")

    assert excinfo.value.status_code == 500


def test_save_drill_video_rejects_invalid_drill_id():
    class FakeUpload:
        filename = "clip.mp4"
        content_type = "video/mp4"
        file = None

    with pytest.raises(drill_upload.DrillUploadError, match="Invalid drill_id"):
        drill_upload.save_drill_video(FakeUpload(), "not a valid id!")


def test_save_drill_video_rejects_unsupported_format():
    class FakeUpload:
        filename = "clip.exe"
        content_type = "application/octet-stream"
        file = None

    with pytest.raises(drill_upload.DrillUploadError, match="Unsupported video format"):
        drill_upload.save_drill_video(FakeUpload(), "DRILL1")


def test_save_drill_video_rejects_mismatched_content_type():
    class FakeUpload:
        filename = "clip.mp4"
        content_type = "image/png"
        file = None

    with pytest.raises(drill_upload.DrillUploadError, match="does not match"):
        drill_upload.save_drill_video(FakeUpload(), "DRILL1")
