import io

import pytest

from app.analysis_result_storage import (
    LocalAnalysisResultStorage,
    S3AnalysisResultStorage,
)


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, **kwargs):
        payload = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": io.BytesIO(payload)}


def test_local_analysis_result_storage_rejects_untrusted_reference(tmp_path):
    storage = LocalAnalysisResultStorage(tmp_path)
    reference = storage.save("JOB_LOCAL", {"score": 88})

    assert b'"score": 88' in storage.read("JOB_LOCAL", reference)

    with pytest.raises(FileNotFoundError):
        storage.read("JOB_LOCAL", str(tmp_path / "other.json"))


def test_s3_analysis_result_storage_round_trip(monkeypatch):
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-trainingbuddy")
    monkeypatch.setenv("S3_ANALYSIS_PREFIX", "analysis/results")
    storage = S3AnalysisResultStorage(client=FakeS3Client())

    reference = storage.save("JOB_S3", {"score": 91})

    assert reference == (
        "s3://private-trainingbuddy/analysis/results/JOB_S3.json"
    )
    assert storage.read("JOB_S3", reference) == b'{"score":91}'

    with pytest.raises(FileNotFoundError):
        storage.read("JOB_S3", "s3://private-trainingbuddy/other.json")
