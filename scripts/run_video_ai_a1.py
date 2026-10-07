"""Run the A1 identity + pose + body-movement analysis on one local video. Reads the video only; writes to --out.

The player is whoever the coach tapped (--selection-json, written by the app), never the biggest person.
  --selection-json FILE        [{"label","frame_index","tap_norm":[x,y],...}] coach selections (COACH_TAP)
  --test-tap FRAME X Y         a SYNTHETIC tap for offline tests only; labelled SYNTHETIC_TEST_TAP, never a coach choice
  --diagnostic-most-prominent  DIAGNOSTIC fallback, never a verified identity
  --identity-only              identity evidence only: no pose, no angles, no motion proxies
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.video_ai import target_selection as ts  # noqa: E402
from app.video_ai.run import run_a1  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--player-id", required=True, help="the roster player the assessment belongs to")
    parser.add_argument("--selection-json", type=Path)
    parser.add_argument("--test-tap", nargs=3, action="append", metavar=("FRAME", "X", "Y"))
    parser.add_argument("--test-label", default="TEST_TARGET")
    parser.add_argument("--diagnostic-most-prominent", action="store_true")
    parser.add_argument("--identity-only", action="store_true")
    parser.add_argument("--detections-cache", type=Path)
    parser.add_argument("--fps-for-time", type=float, default=None, help="only for converting a --test-tap frame to seconds in the label")
    args = parser.parse_args()
    selections = []
    if args.selection_json:
        for item in json.loads(args.selection_json.read_text()):
            selections.append(ts.TargetSelection(label=item["label"], source=ts.SOURCE_COACH_TAP, frame_index=item["frame_index"],
                                                 timestamp_s=item["timestamp_s"], tap_norm=tuple(item["tap_norm"]),
                                                 bbox_norm=tuple(item["bbox_norm"]) if item.get("bbox_norm") else None))
    for frame, x, y in args.test_tap or []:
        selections.append(ts.TargetSelection(label=args.test_label, source=ts.SOURCE_SYNTHETIC_TEST_TAP, frame_index=int(frame),
                                             timestamp_s=int(frame) / (args.fps_for_time or 60.0), tap_norm=(float(x), float(y)),
                                             note="synthetic tap chosen by the test, not by a coach"))
    header = run_a1(args.video, args.out, args.recording_id, args.player_id, selections or None,
                    diagnostic_most_prominent=args.diagnostic_most_prominent, identity_only=args.identity_only,
                    detections_cache=args.detections_cache)
    print("validation all passed:", header["validation"]["all_passed"])
    return 0 if header["validation"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
