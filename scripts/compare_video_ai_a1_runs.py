"""Compare two A1 runs of the same video: are the per-frame results identical? Prints the first differences."""
import hashlib
import json
import sys
from pathlib import Path

a, b = (Path(p) for p in sys.argv[1:3])
fa, fb = (open(p / "a1_frames.jsonl").read().splitlines() for p in (a, b))
print("frames:", len(fa), len(fb))
print("per-frame file sha256 equal:", hashlib.sha256("\n".join(fa).encode()).hexdigest()
      == hashlib.sha256("\n".join(fb).encode()).hexdigest())
differences = [i for i, (x, y) in enumerate(zip(fa, fb)) if x != y]
print("differing frames:", len(differences), differences[:10])
ha, hb = (json.load(open(p / "a1_analysis.json")) for p in (a, b))
for key in ("summary", "windows", "not_measurable_intervals", "tracks", "target_selection"):
    print(key, "equal:", ha[key] == hb[key])
print("per_frame_file sha256:", ha["per_frame_file"]["sha256"], hb["per_frame_file"]["sha256"])
sys.exit(0 if not differences and len(fa) == len(fb) else 1)
