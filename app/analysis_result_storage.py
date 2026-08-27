import json
import os
import re
from pathlib import Path
from typing import Protocol

from app.config import (
    get_s3_bucket,
    get_s3_endpoint_url,
    get_s3_region,
    get_video_storage_backend,
)


JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class AnalysisResultStorage(Protocol):
    def save(self, job_id: str, result: dict) -> str:
        ...

    def read(self, job_id: str, reference: str | None) -> bytes:
        ...

    def delete(self, job_id: str, reference: str | None) -> None:
        ...


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("Invalid analysis job ID")
    return job_id


class LocalAnalysisResultStorage:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(
            os.getenv("VIDEO_ANALYSIS_OUTPUT_DIR", "analysis/results")
        )

    def _path(self, job_id: str) -> Path:
        return self.directory / f"{validate_job_id(job_id)}.json"

    def save(self, job_id: str, result: dict) -> str:
        target = self._path(job_id)
        temporary = target.with_name(f"{target.name}.part")
        self.directory.mkdir(parents=True, exist_ok=True)

        try:
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return str(target)

    def read(self, job_id: str, reference: str | None) -> bytes:
        expected = self._path(job_id).resolve()

        if not reference or Path(reference).resolve() != expected:
            raise FileNotFoundError("Analysis result is unavailable")
        if not expected.is_file():
            raise FileNotFoundError("Analysis result is unavailable")

        return expected.read_bytes()

    def delete(self, job_id: str, reference: str | None) -> None:
        expected = self._path(job_id).resolve()

        if reference and Path(reference).resolve() != expected:
            raise ValueError("Invalid analysis result reference")

        expected.unlink(missing_ok=True)


class S3AnalysisResultStorage:
    def __init__(self, client=None):
        if client is None:
            try:
                import boto3
            except ImportError as error:
                raise RuntimeError(
                    "S3 analysis storage requires the boto3 package"
                ) from error

            client = boto3.client(
                "s3",
                region_name=get_s3_region(),
                endpoint_url=get_s3_endpoint_url(),
            )

        self.client = client
        self.bucket = get_s3_bucket()
        self.prefix = os.getenv(
            "S3_ANALYSIS_PREFIX", "analysis-results"
        ).strip().strip("/")

    def _key(self, job_id: str) -> str:
        filename = f"{validate_job_id(job_id)}.json"
        return f"{self.prefix}/{filename}" if self.prefix else filename

    def _reference(self, job_id: str) -> str:
        return f"s3://{self.bucket}/{self._key(job_id)}"

    def save(self, job_id: str, result: dict) -> str:
        payload = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(job_id),
            Body=payload,
            ContentType="application/json",
        )
        return self._reference(job_id)

    def read(self, job_id: str, reference: str | None) -> bytes:
        if reference != self._reference(job_id):
            raise FileNotFoundError("Analysis result is unavailable")

        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=self._key(job_id),
            )
        except Exception as error:
            raise FileNotFoundError(
                "Analysis result is unavailable"
            ) from error

        return response["Body"].read()

    def delete(self, job_id: str, reference: str | None) -> None:
        if reference and reference != self._reference(job_id):
            raise ValueError("Invalid analysis result reference")

        self.client.delete_object(
            Bucket=self.bucket,
            Key=self._key(job_id),
        )


def get_analysis_result_storage(
    local_directory: Path | None = None,
) -> AnalysisResultStorage:
    if local_directory is None and get_video_storage_backend() == "s3":
        return S3AnalysisResultStorage()
    return LocalAnalysisResultStorage(directory=local_directory)
