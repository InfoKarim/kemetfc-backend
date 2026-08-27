import io
from pathlib import Path

import pytest

from app.video_storage import (
    LocalVideoStorage,
    S3VideoStorage,
    VideoStorageError,
)


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.upload_args = None
        self.deleted = None

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = Path(path).read_bytes()
        self.upload_args = ExtraArgs

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key):
        self.deleted = (Bucket, Key)
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return (
            f"https://storage.example/{Params['Bucket']}/"
            f"{Params['Key']}?expires={ExpiresIn}"
        )


def test_local_storage_saves_materializes_and_deletes(tmp_path):
    storage = LocalVideoStorage(tmp_path)
    stored = storage.save(
        io.BytesIO(b"private-video"),
        "VID_LOCAL.mp4",
        "video/mp4",
    )

    assert stored.filename == "VID_LOCAL.mp4"
    assert stored.size_bytes == len(b"private-video")
    assert len(stored.checksum) == 64
    assert storage.create_download_url("VID_LOCAL.mp4") is None

    with storage.materialize("VID_LOCAL.mp4") as path:
        assert path.read_bytes() == b"private-video"

    storage.delete("VID_LOCAL.mp4")
    assert not (tmp_path / "VID_LOCAL.mp4").exists()


def test_storage_rejects_invalid_or_empty_video(tmp_path):
    storage = LocalVideoStorage(tmp_path)

    with pytest.raises(VideoStorageError, match="Invalid"):
        storage.save(io.BytesIO(b"data"), "../secret.mp4", "video/mp4")

    with pytest.raises(VideoStorageError, match="empty"):
        storage.save(io.BytesIO(b""), "EMPTY.mp4", "video/mp4")


def test_s3_storage_uses_private_object_and_temporary_download(
    monkeypatch,
    tmp_path,
):
    del tmp_path
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-videos")
    monkeypatch.setenv("S3_VIDEO_PREFIX", "academy/videos")
    monkeypatch.setenv("S3_PRESIGNED_URL_EXPIRY_SECONDS", "120")
    client = FakeS3Client()
    storage = S3VideoStorage(client=client)

    stored = storage.save(
        io.BytesIO(b"cloud-video"),
        "VID_CLOUD.mov",
        "video/quicktime",
    )

    object_key = ("private-videos", "academy/videos/VID_CLOUD.mov")
    assert client.objects[object_key] == b"cloud-video"
    assert client.upload_args["ContentType"] == "video/quicktime"
    assert client.upload_args["Metadata"]["sha256"] == stored.checksum
    assert storage.local_path("VID_CLOUD.mov") is None
    assert storage.create_download_url("VID_CLOUD.mov").endswith(
        "?expires=120"
    )

    with storage.materialize("VID_CLOUD.mov") as path:
        temporary = path
        assert path.read_bytes() == b"cloud-video"

    assert not temporary.exists()
    storage.delete("VID_CLOUD.mov")
    assert client.deleted == object_key
