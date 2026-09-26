import pytest

from app.services.tracking_quality import (
    HIGH,
    INSUFFICIENT_DATA,
    LOW,
    REVIEW_REQUIRED,
    VALID,
    VALID_WITH_LIMITED_CONFIDENCE,
    confidence_for_metric,
    evaluate_session_quality,
)


def make_samples(count, locked_ratio=1.0, ball_ratio=1.0, pose_ratio=0.0, sample_hz=10.0):
    samples = []
    for i in range(count):
        samples.append({
            # 10Hz by default — comfortably above EXPECTED_MIN_SAMPLE_RATE_HZ
            # (5Hz) so telemetry_completeness_ratio doesn't spuriously
            # degrade the outcome in tests that aren't specifically
            # testing that signal.
            "t_seconds": i / sample_hz,
            "tracking_status": "locked" if i < count * locked_ratio else "lost",
            # A small, smooth drift — never a jump large enough to trip
            # POSSIBLE_ID_SWITCH_DISTANCE — so id_switch_risk_count stays
            # 0 in tests that aren't specifically testing that signal.
            "player_center": [0.3 + i * 0.0005, 0.5],
            "ball_confidence": 0.8 if i < count * ball_ratio else 0.0,
            "ball_center": [0.6, 0.5] if i < count * ball_ratio else None,
            "pose_keypoints": [{"name": "leftHip"}] if i < count * pose_ratio else None,
        })
    return samples


def test_insufficient_data_below_minimum_sample_count():
    result = evaluate_session_quality(make_samples(5))
    assert result["outcome"] == INSUFFICIENT_DATA
    assert result["overall_confidence"] == INSUFFICIENT_DATA


def test_valid_outcome_with_strong_lock_and_ball_coverage():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.9, ball_ratio=0.8))
    assert result["outcome"] == VALID
    assert result["overall_confidence"] == HIGH


def test_review_required_when_player_lock_is_poor():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.2, ball_ratio=0.8))
    assert result["outcome"] == REVIEW_REQUIRED
    assert any("locked" in reason for reason in result["reasons"])


def test_limited_confidence_when_lock_ok_but_ball_coverage_poor():
    result = evaluate_session_quality(make_samples(50, locked_ratio=0.9, ball_ratio=0.1))
    assert result["outcome"] == VALID_WITH_LIMITED_CONFIDENCE


def test_confidence_for_metric_insufficient_below_min_samples():
    assert confidence_for_metric(data_coverage_ratio=0.9, sample_count=3) == INSUFFICIENT_DATA


def test_confidence_for_metric_scales_with_coverage():
    assert confidence_for_metric(data_coverage_ratio=0.9, sample_count=50) == HIGH
    assert confidence_for_metric(data_coverage_ratio=0.4, sample_count=50) == "MEDIUM"
    assert confidence_for_metric(data_coverage_ratio=0.05, sample_count=50) == LOW


# ---------------------------------------------------------------------------
# Phase 2: target_lost_duration, telemetry_completeness, id_switch_risk
# ---------------------------------------------------------------------------

def test_target_lost_duration_is_zero_when_always_locked():
    result = evaluate_session_quality(make_samples(50, locked_ratio=1.0))
    assert result["target_lost_duration_seconds"] == 0.0


def test_target_lost_duration_measures_the_longest_gap():
    samples = make_samples(50, locked_ratio=1.0)
    # Knock out a contiguous stretch in the middle: samples 20-29 (1.0s
    # at 10Hz) become non-locked.
    for i in range(20, 30):
        samples[i]["tracking_status"] = "temporarily_lost"
    result = evaluate_session_quality(samples)
    assert result["target_lost_duration_seconds"] >= 0.9


def test_telemetry_completeness_is_high_for_a_dense_sample_stream():
    result = evaluate_session_quality(make_samples(50, sample_hz=10.0))
    assert result["telemetry_completeness_ratio"] > 0.9


def test_telemetry_completeness_degrades_outcome_for_a_sparse_sample_stream():
    # 20 samples spread over 20 seconds is 1Hz — well below the 5Hz
    # expected minimum — while lock/ball ratios alone would be VALID.
    samples = make_samples(20, locked_ratio=1.0, ball_ratio=1.0, sample_hz=1.0)
    result = evaluate_session_quality(samples)
    assert result["telemetry_completeness_ratio"] < 0.5
    assert result["outcome"] == VALID_WITH_LIMITED_CONFIDENCE
    assert any("completeness" in reason for reason in result["reasons"])


def test_id_switch_risk_zero_for_smooth_continuous_tracking():
    result = evaluate_session_quality(make_samples(50, locked_ratio=1.0))
    assert result["id_switch_risk_count"] == 0


def test_id_switch_risk_detected_and_forces_review_required():
    samples = make_samples(50, locked_ratio=1.0, ball_ratio=0.9)
    # A single implausible teleport at a high sample rate — this is
    # exactly the "wrong player suddenly locked" signature the iOS
    # PlayerTracker itself is designed to reject, re-derived here
    # independently from the uploaded telemetry.
    samples[25]["player_center"] = [0.9, 0.9]
    result = evaluate_session_quality(samples)
    assert result["id_switch_risk_count"] >= 1
    assert result["outcome"] == REVIEW_REQUIRED
    assert result["overall_confidence"] != HIGH
    assert any("position jump" in reason for reason in result["reasons"])


def test_id_switch_risk_ignored_across_a_long_gap():
    # A big position difference is expected (not suspicious) if it
    # happens after a long time gap — e.g. after TEMPORARILY_LOST/
    # SEARCHING, the player could legitimately be anywhere.
    samples = make_samples(20, locked_ratio=1.0, sample_hz=10.0)
    samples[10]["t_seconds"] = samples[9]["t_seconds"] + 5.0  # 5s gap
    samples[10]["player_center"] = [0.95, 0.95]
    result = evaluate_session_quality(samples)
    assert result["id_switch_risk_count"] == 0


# --- identity / honesty review round ------------------------------------------------------------


def tracked_samples(count=60, statuses=None, **extra):
    samples = make_samples(count)
    for index, sample in enumerate(samples):
        if statuses is not None:
            sample["tracking_status"] = statuses(index)
        sample.update(extra)
    return samples


def test_reacquired_counts_as_tracked_not_as_lost():
    # After the first reacquisition the iOS tracker reports "reacquired" for the rest of the session.
    samples = tracked_samples(60, statuses=lambda i: "locked" if i < 20 else ("temporarily_lost" if i < 24 else "reacquired"))

    result = evaluate_session_quality(samples)

    assert result["player_lock_ratio"] == pytest.approx(56 / 60)
    assert result["target_lost_duration_seconds"] < 1.0, "not the whole tail of the session"
    assert result["reacquisition_count"] == 1


def test_a_reacquisition_caps_the_outcome_at_limited_confidence():
    samples = tracked_samples(60, statuses=lambda i: "locked" if i < 30 else ("searching" if i < 33 else "reacquired"))

    result = evaluate_session_quality(samples)

    assert result["outcome"] == VALID_WITH_LIMITED_CONFIDENCE
    assert result["overall_confidence"] != HIGH
    assert any("reacquired" in reason for reason in result["reasons"])


def test_identity_ambiguous_frames_above_the_ratio_force_review():
    samples = tracked_samples(100)
    for index in range(5):                    # 5% of the session
        samples[40 + index]["identity_ambiguous"] = True

    result = evaluate_session_quality(samples)

    assert result["outcome"] == REVIEW_REQUIRED
    assert result["ambiguous_frame_count"] == 5
    assert result["overall_confidence"] == LOW
    assert any("identity-ambiguous" in reason for reason in result["reasons"])


def test_a_single_ambiguous_frame_limits_confidence_without_forcing_review():
    samples = tracked_samples(100)
    samples[50]["identity_ambiguous"] = True

    result = evaluate_session_quality(samples)

    assert result["outcome"] == VALID_WITH_LIMITED_CONFIDENCE
    assert result["ambiguous_frame_count"] == 1


def test_unknown_ambiguity_is_not_counted_as_ambiguous():
    samples = tracked_samples(60, identity_ambiguous=None)
    assert evaluate_session_quality(samples)["ambiguous_frame_count"] == 0
    assert evaluate_session_quality(tracked_samples(60, identity_ambiguous=False))["outcome"] == VALID


def test_missing_sequence_numbers_are_reported_and_a_large_gap_forces_review():
    samples = tracked_samples(60)
    for index, sample in enumerate(samples):
        sample["sequence_number"] = index + 1
    small_gap = [s for i, s in enumerate(samples) if i != 30]           # 1 of 60 missing
    assert evaluate_session_quality(small_gap)["sequence_gap_count"] == 1
    assert evaluate_session_quality(small_gap)["outcome"] == VALID_WITH_LIMITED_CONFIDENCE

    big_gap = [s for i, s in enumerate(samples) if not 20 <= i < 30]    # 10 of 60 missing
    result = evaluate_session_quality(big_gap)
    assert result["sequence_gap_count"] == 10
    assert result["outcome"] == REVIEW_REQUIRED


def test_a_recording_that_is_mostly_holes_needs_review():
    samples = make_samples(50, sample_hz=0.9)     # under 1 Hz against an expected 5 Hz minimum: <20% complete
    result = evaluate_session_quality(samples)
    assert result["telemetry_completeness_ratio"] < 0.2
    assert result["outcome"] == REVIEW_REQUIRED


def test_ball_confidence_without_a_ball_measurement_is_not_ball_coverage():
    samples = tracked_samples(60)
    for sample in samples:
        sample["ball_center"] = None          # confidence alone is not a detection
    assert evaluate_session_quality(samples)["ball_coverage_ratio"] == 0.0


def test_only_verified_poses_count_as_pose_coverage():
    keypoints = [{"name": n, "x": 0.45 + 0.02 * i, "y": 0.3 + 0.05 * i} for i, n in enumerate(["a", "b", "c", "d", "e"])]
    verified = tracked_samples(60, pose_keypoints=keypoints, player_bbox=[0.40, 0.25, 0.20, 0.35])
    unverified = tracked_samples(60, pose_keypoints=keypoints)            # no player box to tie it to
    assert evaluate_session_quality(verified)["pose_coverage_ratio"] == 1.0
    assert evaluate_session_quality(unverified)["pose_coverage_ratio"] == 0.0
