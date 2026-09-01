import hashlib
from pathlib import Path
from urllib.request import urlopen


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_lite/float16/latest/"
    "pose_landmarker_lite.task"
)
MODEL_SHA256 = (
    "59929e1d1ee95287735ddd833b19cf4ac"
    "46d29bc7afddbbf6753c459690d574a"
)
MODEL_PATH = Path("models/pose_landmarker_lite.task")


def main() -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_PATH.with_suffix(".task.part")
    digest = hashlib.sha256()

    try:
        # MODEL_URL is a hardcoded https:// constant, not attacker-influenced
        # input, and the download is checksum-verified below before use.
        with urlopen(MODEL_URL) as response, temporary.open("wb") as output:  # nosec B310
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)

        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError("Pose model checksum verification failed")

        temporary.replace(MODEL_PATH)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    print(f"Downloaded verified model to {MODEL_PATH}")


if __name__ == "__main__":
    main()
