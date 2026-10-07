"""List frames worth showing the coach for SELECT PLAYER IN VIDEO (people visible, sharp, clear of picture cuts)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.video_ai.run import suggest_target_frames  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--video", required=True, type=Path)
parser.add_argument("--cache", type=Path)
parser.add_argument("--count", type=int, default=5)
args = parser.parse_args()
print(json.dumps(suggest_target_frames(args.video, args.cache, args.count), indent=1))
