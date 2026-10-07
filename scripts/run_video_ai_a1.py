"""Run the A1 pose + body-movement analysis on one local video. Reads the video only; writes to --out."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.video_ai.run import run_a1  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--player-id", required=True)
    args = parser.parse_args()
    header = run_a1(args.video, args.out, args.recording_id, args.player_id)
    print("validation all passed:", header["validation"]["all_passed"])
    return 0 if header["validation"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
