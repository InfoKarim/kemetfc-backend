"""Draw the annotated preview video and sample frames from an A1 artifact. Reads the video only."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.video_ai.render_debug import render  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--video", required=True, type=Path)
parser.add_argument("--artifact", required=True, type=Path, help="folder holding a1_frames.jsonl")
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
print(render(args.video, args.artifact / "a1_frames.jsonl", args.out))
