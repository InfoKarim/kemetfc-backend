import io
from pathlib import Path

from app.avatar_upload import (
    LocalAvatarStorage,
    S3AvatarStorage,
    get_avatar_storage,
)


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.upload_args = None
        self.deleted = None

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = Path(path).read_bytes()
        self.upload_args = ExtraArgs

    def delete_object(self, Bucket, Key):
        self.deleted = (Bucket, Key)
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return (
            f"https://storage.example/{Params['Bucket']}/"
            f"{Params['Key']}?expires={ExpiresIn}"
        )


def test_local_avatar_storage_saves_serves_and_deletes(tmp_path):
    storage = LocalAvatarStorage(tmp_path)
    storage.save(io.BytesIO(b"avatar-bytes"), "P100.png", "image/png")

    assert storage.create_download_url("P100.png") is None
    path = storage.local_path("P100.png")
    assert path.read_bytes() == b"avatar-bytes"

    storage.delete("P100.png")
    assert not (tmp_path / "P100.png").exists()


def test_s3_avatar_storage_uploads_and_generates_download_url(monkeypatch):
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-media")
    monkeypatch.setenv("S3_AVATAR_PREFIX", "avatars")
    monkeypatch.setenv("S3_PRESIGNED_URL_EXPIRY_SECONDS", "120")
    client = FakeS3Client()
    storage = S3AvatarStorage(client=client)

    storage.save(io.BytesIO(b"avatar-bytes"), "P100.png", "image/png")

    object_key = ("private-media", "avatars/P100.png")
    assert client.objects[object_key] == b"avatar-bytes"
    assert client.upload_args["ContentType"] == "image/png"
    assert storage.local_path("P100.png") is None
    assert storage.create_download_url("P100.png").endswith("?expires=120")

    storage.delete("P100.png")
    assert client.deleted == object_key


def test_get_avatar_storage_switches_on_backend(monkeypatch):
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")
    assert isinstance(get_avatar_storage(), LocalAvatarStorage)

    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-media")
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: FakeS3Client())
    assert isinstance(get_avatar_storage(), S3AvatarStorage)
