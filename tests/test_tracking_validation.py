"""Real-video validation metrics (app/services/tracking_validation.py).

These tests use SYNTHETIC telemetry and ground truth to prove the arithmetic and
the honesty rules of the harness. They say nothing about real-footage accuracy,
and the harness itself refuses to label such data as real video.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.tracking_validation import (
    GroundTruthError,
    telemetry_continuity,
    validate_ground_truth,
    validate_session,
)

ROOT = Path(__file__).resolve().parents[1]
DT = 0.1
BOX = [0.40, 0.30, 0.12, 0.40]          # ground-truth target box; center (0.46, 0.50)


def gt_frame(i, visible=True, ball=None, bbox=BOX):
    frame = {"t_seconds": round(i * DT, 6), "target_visible": visible, "ball_visible": ball is not None}
    if visible:
        frame["target_bbox"] = bbox
    if ball is not None:
        frame["ball_center"] = ball
    return frame


def sample(i, status="locked", center=(0.46, 0.50), ball=None, ball_id=None, pose=None, sequence=None, ambiguous=None):
    return {
        "t_seconds": round(i * DT, 6),
        "sequence_number": sequence if sequence is not None else i + 1,
        "tracking_status": status,
        "player_center": list(center) if center else None,
        "player_confidence": 0.9 if center else None,
        "ball_center": list(ball) if ball else None,
        "ball_confidence": 0.35 if ball else None,
        "ball_track_id": ball_id,
        "pose_keypoints": pose,
        "identity_ambiguous": ambiguous,
    }


def ground_truth(frames, source="synthetic"):
    return {"metadata": {"source": source, "clip": "unit-test"}, "frames": frames}


def test_perfect_tracking_retains_the_target_with_no_false_switches():
    frames = [gt_frame(i) for i in range(20)]
    report = validate_session([sample(i) for i in range(20)], ground_truth(frames))

    player = report["player"]
    assert player["target_visible_frames"] == 20
    assert player["selected_player_retention"] == 1.0
    assert player["false_id_switch_events"] == 0
    assert player["tracked_wrong_person_frames"] == 0
    assert report["coverage"]["matched_frames"] == 20


def test_tracking_the_wrong_child_is_counted_as_a_false_id_switch_not_as_retention():
    frames = [gt_frame(i) for i in range(20)]
    samples = [sample(i, center=(0.90, 0.50) if 8 <= i < 14 else (0.46, 0.50)) for i in range(20)]

    player = validate_session(samples, ground_truth(frames))["player"]

    assert player["tracked_on_target_frames"] == 14
    assert player["tracked_wrong_person_frames"] == 6
    assert player["false_id_switch_events"] == 1, "one contiguous run of wrong-person frames is one event"
    assert player["selected_player_retention"] == pytest.approx(14 / 20)
    assert player["false_id_switch_frame_ratio"] == pytest.approx(6 / 20)


def test_a_safe_loss_is_not_a_false_switch_and_reacquisition_latency_is_measured():
    frames = [gt_frame(i) for i in range(30)]
    samples = [
        sample(i, status="temporarily_lost", center=None) if 10 <= i < 15
        else sample(i, status="reacquired" if i >= 15 else "locked")
        for i in range(30)
    ]

    player = validate_session(samples, ground_truth(frames))["player"]

    assert player["not_tracked_frames"] == 5
    assert player["tracked_wrong_person_frames"] == 0
    assert len(player["reacquisition_events"]) == 1
    event = player["reacquisition_events"][0]
    assert event["latency_seconds"] == pytest.approx(0.5)
    assert event["correct"] is True
    assert player["correct_reacquisitions"] == 1 and player["false_reacquisitions"] == 0


def test_reacquiring_onto_the_wrong_person_is_a_false_reacquisition():
    frames = [gt_frame(i) for i in range(20)]
    samples = [
        sample(i, status="temporarily_lost", center=None) if 5 <= i < 9
        else sample(i, status="reacquired", center=(0.85, 0.5)) if i >= 9
        else sample(i)
        for i in range(20)
    ]
    player = validate_session(samples, ground_truth(frames))["player"]
    assert player["false_reacquisitions"] == 1
    assert player["correct_reacquisitions"] == 0


def test_a_target_that_never_comes_back_is_reported_as_still_lost():
    frames = [gt_frame(i) for i in range(20)]
    samples = [sample(i) if i < 10 else sample(i, status="searching", center=None) for i in range(20)]
    assert validate_session(samples, ground_truth(frames))["player"]["still_lost_at_end"] is True


def test_frames_where_the_target_is_not_visible_are_not_held_against_the_tracker():
    frames = [gt_frame(i, visible=(i < 10)) for i in range(20)]
    samples = [sample(i) if i < 10 else sample(i, status="temporarily_lost", center=None) for i in range(20)]
    player = validate_session(samples, ground_truth(frames))["player"]
    assert player["target_visible_frames"] == 10
    assert player["selected_player_retention"] == 1.0


def test_ball_precision_and_recall_count_wrong_places_and_phantoms():
    ball = [0.6, 0.6]
    frames = [gt_frame(i, ball=ball if i < 6 else None) for i in range(10)]
    samples = [
        sample(0, ball=ball), sample(1, ball=ball),             # 2 TP
        sample(2, ball=[0.9, 0.1]),                             # wrong place: FP and FN
        sample(3),                                              # miss: FN
        sample(4, ball=ball), sample(5, ball=ball),             # 2 TP
        sample(6, ball=[0.2, 0.2]),                             # phantom: FP
        sample(7), sample(8), sample(9),                        # 3 TN
    ]
    result = validate_session(samples, ground_truth(frames))["ball"]
    assert (result["true_positives"], result["false_positives"], result["false_negatives"], result["true_negatives"]) == (4, 2, 2, 3)
    assert result["precision"] == pytest.approx(4 / 6)
    assert result["recall"] == pytest.approx(4 / 6)
    assert result["detections_at_wrong_location"] == 1


def test_ball_track_id_changes_while_the_ball_is_visible_are_counted():
    ball = [0.6, 0.6]
    frames = [gt_frame(i, ball=ball) for i in range(6)]
    samples = [sample(i, ball=ball, ball_id=1 if i < 3 else 2) for i in range(6)]
    assert validate_session(samples, ground_truth(frames))["ball"]["ball_track_id_changes_while_visible"] == 1


def test_a_ratio_is_none_when_there_is_nothing_to_divide_by():
    frames = [gt_frame(i, visible=False) for i in range(5)]
    report = validate_session([sample(i, status="lost", center=None) for i in range(5)], ground_truth(frames))
    assert report["player"]["selected_player_retention"] is None, "not 0% and not 100%: undefined"
    assert report["ball"]["precision"] is None and report["ball"]["recall"] is None
    assert report["pose"]["pose_association_precision"] is None


def test_pose_association_separates_the_targets_pose_from_another_childs():
    keypoints = lambda cx: [  # noqa: E731
        {"name": name, "x": cx + dx, "y": 0.35 + 0.05 * i, "confidence": 0.9}
        for i, (name, dx) in enumerate([("a", -0.02), ("b", 0.02), ("c", -0.02), ("d", 0.02), ("e", 0.0)])
    ]
    frames = [gt_frame(i) for i in range(4)]
    samples = [
        sample(0, pose=keypoints(0.46)),         # on the target
        sample(1, pose=keypoints(0.46)),         # on the target
        sample(2, pose=keypoints(0.85)),         # another child's body
        sample(3),                               # target visible, no pose reported
    ]
    pose = validate_session(samples, ground_truth(frames))["pose"]
    assert pose["pose_on_target_frames"] == 2
    assert pose["pose_on_wrong_person_frames"] == 1
    assert pose["pose_absent_while_target_visible_frames"] == 1
    assert pose["pose_association_precision"] == pytest.approx(2 / 3)


def test_telemetry_continuity_reports_gaps_duplicates_and_ordering():
    samples = [sample(i, sequence=n) for i, n in enumerate([1, 2, 3, 3, 6, 7])]      # 4 and 5 missing, 3 duplicated
    samples.append({**sample(9, sequence=8), "t_seconds": 0.05})                      # sequence 8 arrives with an earlier time
    result = telemetry_continuity(samples)
    assert result["missing_sequence_numbers"] == 2
    assert result["duplicate_sequence_numbers"] == 1
    assert result["sequence_vs_time_order_violations"] >= 1
    assert result["max_time_gap_seconds"] is not None


def test_synthetic_ground_truth_is_never_labelled_as_real_video():
    frames = [gt_frame(i) for i in range(5)]
    assert validate_session([sample(i) for i in range(5)], ground_truth(frames))["evidence"] == "not_real_video"
    assert validate_session([sample(i) for i in range(5)], ground_truth(frames, source="real_video"))["evidence"] == "real_video"
    assert validate_session([sample(i) for i in range(5)], {"frames": frames})["evidence"] == "not_real_video"


def test_only_samples_within_the_match_window_are_matched():
    frames = [gt_frame(i) for i in range(10)]
    samples = [sample(i) for i in range(5)]                 # telemetry stops halfway
    coverage = validate_session(samples, ground_truth(frames), match_window_seconds=0.05)["coverage"]
    assert coverage["matched_frames"] == 5 and coverage["unmatched_frames"] == 5


@pytest.mark.parametrize("bad", [{}, {"frames": []}, {"frames": [{"t_seconds": "x"}]},
                                 {"frames": [{"t_seconds": 0, "target_visible": True}]},
                                 {"frames": [{"t_seconds": 0, "ball_visible": True}]}])
def test_malformed_ground_truth_is_rejected(bad):
    with pytest.raises(GroundTruthError):
        validate_ground_truth(bad)


def test_command_line_entry_point_writes_a_report(tmp_path):
    frames = [gt_frame(i) for i in range(10)]
    (tmp_path / "gt.json").write_text(json.dumps(ground_truth(frames)))
    (tmp_path / "telemetry.json").write_text(json.dumps({"samples": [sample(i) for i in range(10)]}))
    out = tmp_path / "report.json"

    completed = subprocess.run(
        [sys.executable, "scripts/validate_tracking_session.py", "--telemetry", str(tmp_path / "telemetry.json"),
         "--ground-truth", str(tmp_path / "gt.json"), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(out.read_text())
    assert report["player"]["selected_player_retention"] == 1.0
    assert report["evidence"] == "not_real_video"


def test_command_line_rejects_invalid_ground_truth(tmp_path):
    (tmp_path / "gt.json").write_text(json.dumps({"frames": []}))
    (tmp_path / "telemetry.json").write_text("[]")
    completed = subprocess.run(
        [sys.executable, "scripts/validate_tracking_session.py", "--telemetry", str(tmp_path / "telemetry.json"),
         "--ground-truth", str(tmp_path / "gt.json")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode == 2
    assert "Invalid ground truth" in completed.stderr
