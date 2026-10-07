"""A1 video-AI foundation: geometry, pose quality, identity continuity, proxies and the validator.

Synthetic landmarks and detections only; no video or model is needed.
"""
import copy
import math

import numpy as np

from app.video_ai import geometry, identity, proxies, validate
from app.video_ai.config import IDENTITY, POSE


def mark(x, y, visibility=0.9, presence=0.9, usable=True):
    return {"x_px": float(x), "y_px": float(y), "x_norm": x / 1000.0, "y_norm": y / 2000.0,
            "visibility": visibility, "presence": presence, "usable": usable, "unusable_reason": None}


def standing(overrides=None, hidden=()):
    """A straight, upright figure facing the camera (image 1000 x 2000)."""
    pts = {"left_shoulder": (550, 500), "right_shoulder": (450, 500), "left_elbow": (570, 650),
           "right_elbow": (430, 650), "left_wrist": (580, 800), "right_wrist": (420, 800),
           "left_hip": (530, 900), "right_hip": (470, 900), "left_knee": (530, 1250), "right_knee": (470, 1250),
           "left_ankle": (530, 1600), "right_ankle": (470, 1600)}
    pts.update(overrides or {})
    marks = {name: mark(*xy) for name, xy in pts.items()}
    for name in geometry.LANDMARK_NAMES:
        marks.setdefault(name, mark(500, 300, usable=False, visibility=0.1))
    for name in hidden:
        marks[name] = mark(*pts[name], visibility=0.1, usable=False)
    return marks


# ---- geometry -------------------------------------------------------------------------------------------

def test_a_straight_leg_is_180_degrees_and_a_right_angle_is_90():
    angles = geometry.compute_angles(standing(), True)
    assert angles["left_knee_angle_2d"]["value"] == 180.0
    bent = standing({"left_knee": (730, 1250), "left_ankle": (730, 1600)})
    expected = math.degrees(math.acos(-350 / math.hypot(200, 350)))                     # hip-knee-ankle, hand computed
    assert math.isclose(geometry.compute_angles(bent, True)["left_knee_angle_2d"]["value"], expected, abs_tol=0.01)
    right_angle = standing({"left_hip": (530, 1000), "left_knee": (530, 1250), "left_ankle": (780, 1250)})
    assert math.isclose(geometry.compute_angles(right_angle, True)["left_knee_angle_2d"]["value"], 90.0, abs_tol=0.01)


def test_every_measurement_carries_value_unit_confidence_state_definition_and_space():
    entry = geometry.compute_angles(standing(), True)["right_elbow_angle_2d"]
    assert entry["state"] == "MEASURED" and entry["unit"] == "degrees" and entry["space"] == "IMAGE_PLANE_2D"
    assert entry["definition_version"] == "angles_2d_v1" and 0 < entry["confidence"] <= 1


def test_trunk_lean_is_signed_from_image_vertical():
    upright = geometry.compute_angles(standing(), True)["trunk_lean_2d"]["value"]
    assert abs(upright) < 1e-6
    shifted = standing({"left_shoulder": (930, 500), "right_shoulder": (830, 500)})    # shoulder mid x=880, hip mid x=500
    value = geometry.compute_angles(shifted, True)["trunk_lean_2d"]["value"]
    assert math.isclose(value, math.degrees(math.atan2(380, 400)), abs_tol=0.01) and value > 0   # 380 right over 400 up
    mirrored = standing({"left_shoulder": (170, 500), "right_shoulder": (70, 500)})
    assert geometry.compute_angles(mirrored, True)["trunk_lean_2d"]["value"] < 0


def test_line_tilt_is_zero_for_level_shoulders_and_wraps_into_minus_90_to_90():
    angles = geometry.compute_angles(standing(), True)
    assert angles["shoulder_line_angle_2d"]["value"] == 0.0
    tilted = standing({"left_shoulder": (550, 560)})
    assert -90 < geometry.compute_angles(tilted, True)["shoulder_line_angle_2d"]["value"] <= 90


def test_a_missing_joint_never_produces_an_angle():
    marks = standing(hidden=("left_ankle",))
    angles = geometry.compute_angles(marks, True)
    assert angles["left_knee_angle_2d"]["value"] is None
    assert angles["left_knee_angle_2d"]["state"] == "NOT_MEASURABLE"
    assert angles["left_knee_angle_2d"]["reason"] == "LANDMARK_NOT_USABLE:left_ankle"
    assert angles["right_knee_angle_2d"]["state"] == "MEASURED"       # untouched side still measured


def test_no_pose_or_unconfirmed_identity_gives_not_measurable_everywhere():
    for angles in (geometry.compute_angles(None, True), geometry.compute_angles(standing(), False, "IDENTITY_LOST")):
        assert all(a["state"] == "NOT_MEASURABLE" and a["value"] is None and a["reason"] for a in angles.values())


def test_collinear_degenerate_geometry_is_not_a_number():
    marks = standing({"left_shoulder": (530, 900), "right_shoulder": (470, 900)})   # shoulders on the hips
    entry = geometry.compute_angles(marks, True)["apparent_shoulder_width_ratio"]
    assert entry["value"] is None and entry["reason"] == "DEGENERATE_GEOMETRY"


# ---- landmark usability and pose quality ----------------------------------------------------------------------

def test_landmark_usability_rules():
    box = (0.2, 0.2, 0.8, 0.8)
    assert geometry.landmark_usable(0.9, 0.9, 0.5, 0.5, box) == (True, None)
    assert geometry.landmark_usable(0.4, 0.9, 0.5, 0.5, box) == (False, "LOW_CONFIDENCE")
    assert geometry.landmark_usable(0.9, 0.4, 0.5, 0.5, box) == (False, "LOW_CONFIDENCE")
    assert geometry.landmark_usable(0.9, 0.9, 1.2, 0.5, box) == (False, "OUTSIDE_FRAME")
    assert geometry.landmark_usable(0.9, 0.9, 0.95, 0.5, box) == (False, "OUTSIDE_TARGET_BOX")
    assert geometry.landmark_usable(0.9, 0.9, 0.5, 0.5, None) == (True, None)
    assert geometry.landmark_usable(None, None, 0.5, 0.5, box) == (False, "NO_CONFIDENCE")


def test_pose_quality_states():
    good = geometry.pose_quality(standing(), overlap=False, touches_edge=False)
    assert good["status"] == "GOOD" and good["usable_core"] == 12
    lower_hidden = standing(hidden=("left_knee", "right_knee", "left_ankle", "right_ankle", "left_hip", "right_hip"))
    assert geometry.pose_quality(lower_hidden, overlap=False, touches_edge=False)["status"] == "PARTIAL"
    assert geometry.pose_quality(lower_hidden, overlap=True, touches_edge=False)["status"] == "OCCLUDED"
    assert geometry.pose_quality(lower_hidden, overlap=False, touches_edge=True)["status"] == "OCCLUDED"
    mostly_hidden = standing(hidden=tuple(geometry.CORE_JOINTS[:8]))
    assert geometry.pose_quality(mostly_hidden, overlap=False, touches_edge=False)["status"] == "LOW_CONFIDENCE"
    none = geometry.pose_quality(None, overlap=False, touches_edge=False)
    assert none["status"] == "NOT_DETECTED"
    assert all(s in geometry.POSE_STATES for s in (good["status"], none["status"]))


def test_good_requires_enough_mean_visibility_not_just_usable_flags():
    marks = standing()
    for name in geometry.CORE_JOINTS:
        marks[name]["visibility"] = 0.55          # usable, but only just
    assert geometry.pose_quality(marks, overlap=False, touches_edge=False)["status"] == "PARTIAL"


# ---- identity -------------------------------------------------------------------------------------------------

def det(track, x, conf=0.9, w=0.2, y=0.4, h=0.3):
    return {"track_id": track, "confidence": conf, "bbox_norm": [x, y, x + w, y + h]}


def test_the_dominant_track_is_chosen_and_short_tracks_are_not_eligible():
    frames = [[det(1, 0.1), det(2, 0.5, w=0.05, h=0.1)] for _ in range(120)] + [[det(3, 0.3, w=0.5, h=0.6)]] * 10
    target, tracks, method = identity.select_target(frames, 60.0)
    assert target == 1 and method == "dominant_track_offline"
    assert tracks[3]["seconds_present"] < IDENTITY["min_target_presence_seconds"]
    assert identity.select_target([[det(None, 0.1)]] * 5, 60.0)[0] is None


def test_the_target_id_is_never_switched_when_it_is_lost_and_a_new_id_appears_nearby():
    frames = [[det(1, 0.30)]] * 20 + [[]] * 5 + [[det(9, 0.30)]] * 20 + [[det(1, 0.30)]] * 40
    result = identity.assess_identity(frames, 1, [False] * len(frames), 0.5625)
    assert [r["state"] for r in result[20:25]] == ["LOST"] * 5
    assert all(r["state"] == "LOST" and r["target"] is None for r in result[25:45])        # id 9 is NOT adopted
    assert all(r["target"]["track_id"] == 1 for r in result if r["target"])


def test_first_sight_and_reacquisition_are_unverified_for_a_probation_period():
    frames = [[]] * 3 + [[det(1, 0.3)]] * 40 + [[]] * 5 + [[det(1, 0.3)]] * 40
    result = identity.assess_identity(frames, 1, [False] * len(frames), 0.5625)
    n = IDENTITY["reacquire_probation_frames"]
    assert [r["state"] for r in result[3:3 + n]] == ["REACQUIRED_UNVERIFIED"] * n
    assert result[3 + n]["state"] == "CONFIRMED"
    assert result[3]["reason"] == "TARGET_FIRST_SEEN" and result[48]["reason"] == "REACQUIRED_AFTER_GAP"
    assert result[48 + n]["state"] == "CONFIRMED"
    assert result[3 + n]["segment"] != result[48 + n]["segment"]


def test_an_overlapping_person_makes_the_frame_ambiguous_and_breaks_the_segment():
    frames = [[det(1, 0.3)]] * 30 + [[det(1, 0.3), det(2, 0.32)]] * 5 + [[det(1, 0.3)]] * 10
    result = identity.assess_identity(frames, 1, [False] * len(frames), 0.5625)
    assert all(r["state"] == "OVERLAP_AMBIGUOUS" for r in result[30:35])
    assert result[29]["segment"] != result[35]["segment"] or result[35]["segment"] is None


def test_a_scene_cut_and_an_implausible_jump_start_a_probation_even_if_the_id_persists():
    frames = [[det(1, 0.3)]] * 60
    cuts = [False] * 60
    cuts[30] = True
    result = identity.assess_identity(frames, 1, cuts, 0.5625)
    assert result[30]["state"] == "REACQUIRED_UNVERIFIED" and result[30]["reason"] == "AFTER_SCENE_CUT"
    jumpy = [[det(1, 0.1)]] * 30 + [[det(1, 0.7)]] * 30
    result = identity.assess_identity(jumpy, 1, [False] * 60, 0.5625)
    assert result[30]["reason"] == "IMPLAUSIBLE_JUMP" and result[30]["state"] == "REACQUIRED_UNVERIFIED"


def test_tracking_confidence_is_scaled_by_identity_trust():
    assert identity.tracking_confidence(0.8, "CONFIRMED") == 0.8
    assert identity.tracking_confidence(0.8, "REACQUIRED_UNVERIFIED") == 0.4
    assert identity.tracking_confidence(0.8, "LOST") == 0.0
    assert identity.tracking_confidence(None, "CONFIRMED") == 0.0


# ---- proxies --------------------------------------------------------------------------------------------------

def series(n, fps=60.0, state="CONFIRMED", segment=1, center=lambda t: (0.5, 0.5), angle=None):
    out = []
    for i in range(n):
        t = i / fps
        out.append({"t": t, "state": state, "segment": segment, "center": center(t), "camera_moving": False,
                    "angles": {"left_knee_angle_2d": None if angle is None else angle(t),
                               "right_knee_angle_2d": None if angle is None else angle(t + 0.3)}})
    for item in out:
        for name in proxies.DERIVED_ANGLES:
            item["angles"].setdefault(name, None)
    return out


def test_speed_and_acceleration_proxy_follow_a_known_image_motion():
    frames = series(120, center=lambda t: (0.2 + 0.1 * t + 0.5 * 0.4 * t * t, 0.5))     # v = 0.1 + 0.4 t, a = 0.4
    out = proxies.per_frame_proxies(frames, aspect_ratio=0.5625)
    mid = out[60]
    t = frames[60]["t"]
    assert mid["speed_proxy_frame_heights_per_s"]["state"] == "MEASURED"
    assert math.isclose(mid["speed_proxy_frame_heights_per_s"]["value"], (0.1 + 0.4 * t) * 0.5625, rel_tol=1e-2)
    assert math.isclose(mid["acceleration_proxy_frame_heights_per_s2"]["value"], 0.4 * 0.5625, rel_tol=1e-2)
    assert mid["label"] == "VIDEO_MOTION_PROXY" and mid["camera_compensated"] is False
    assert out[0]["speed_proxy_frame_heights_per_s"]["reason"] == "RUN_EDGE"


def test_derivatives_never_span_an_identity_gap_or_a_segment_change():
    frames = series(120)
    for i in range(50, 60):
        frames[i]["state"], frames[i]["segment"], frames[i]["center"] = "LOST", None, None
    for i in range(60, 120):
        frames[i]["segment"] = 2
    out = proxies.per_frame_proxies(frames, 0.5625)
    assert all(out[i]["speed_proxy_frame_heights_per_s"]["state"] == "NOT_MEASURABLE" for i in range(50, 60))
    assert out[50]["speed_proxy_frame_heights_per_s"]["reason"] == "IDENTITY_NOT_CONFIRMED"
    # nothing near the gap borrows frames from the other side: edges of each run are not measured
    assert out[49]["speed_proxy_frame_heights_per_s"]["reason"] == "RUN_EDGE"
    assert out[60]["speed_proxy_frame_heights_per_s"]["reason"] == "RUN_EDGE"
    assert out[75]["speed_proxy_frame_heights_per_s"]["state"] == "MEASURED"


def test_short_runs_are_not_smoothed_into_a_value():
    out = proxies.per_frame_proxies(series(6), 0.5625)
    assert all(p["speed_proxy_frame_heights_per_s"]["state"] == "NOT_MEASURABLE" for p in out)


def test_angular_velocity_is_computed_only_over_consecutive_measured_frames():
    frames = series(120, angle=lambda t: 120 + 30 * t)
    frames[60]["angles"]["left_knee_angle_2d"] = None
    out = proxies.per_frame_proxies(frames, 0.5625)
    assert math.isclose(out[30]["angular_velocity_deg_per_s"]["left_knee_angle_2d"]["value"], 30.0, rel_tol=1e-3)
    assert out[60]["angular_velocity_deg_per_s"]["left_knee_angle_2d"]["state"] == "NOT_MEASURABLE"
    assert out[61]["angular_velocity_deg_per_s"]["left_knee_angle_2d"]["reason"] == "RUN_EDGE"


def test_cadence_proxy_finds_a_known_oscillation_and_refuses_noise_or_gaps():
    frames = series(600, angle=lambda t: 140 + 25 * math.sin(2 * math.pi * 1.5 * t))
    windows = proxies.window_proxies(frames, 60.0)
    first = windows[0]
    assert first["left_knee_dominant_frequency_hz"]["state"] == "MEASURED"
    assert math.isclose(first["left_knee_dominant_frequency_hz"]["value"], 1.5, abs_tol=0.05)
    assert first["label"] == "VIDEO_MOTION_PROXY"
    noise = np.random.default_rng(1).normal(0, 1, 600)
    flat = series(600, angle=lambda t: 140 + 0.0 * t)
    for i, item in enumerate(flat):
        item["angles"]["left_knee_angle_2d"] = 140 + float(noise[i]) * 8
    assert proxies.window_proxies(flat, 60.0)[0]["left_knee_dominant_frequency_hz"]["state"] == "NOT_MEASURABLE"
    gappy = series(600, angle=lambda t: 140 + 25 * math.sin(2 * math.pi * 1.5 * t))
    gappy[100]["state"], gappy[100]["segment"] = "LOST", None
    assert proxies.window_proxies(gappy, 60.0)[0]["left_knee_dominant_frequency_hz"]["reason"] \
        == "IDENTITY_NOT_CONFIRMED_THROUGHOUT_WINDOW"


def test_asymmetry_proxy_is_the_left_right_difference():
    frames = series(300, angle=lambda t: 140 + 20 * math.sin(2 * math.pi * 1.0 * t))
    for item in frames:
        item["angles"]["right_knee_angle_2d"] = item["angles"]["left_knee_angle_2d"] - 10.0
    window = proxies.window_proxies(frames, 60.0)[0]
    assert math.isclose(window["knee_angle_asymmetry_mean_abs_deg"]["value"], 10.0, abs_tol=1e-6)
    assert math.isclose(window["knee_range_asymmetry_ratio"]["value"], 0.0, abs_tol=1e-6)


# ---- validator ------------------------------------------------------------------------------------------------

def good_frame(index, t, marks=None):
    marks = marks if marks is not None else standing()
    angles = geometry.compute_angles(marks, True)
    return {"frame_index": index, "timestamp_s": t, "persons": [{"is_target": True, "track_id": 1}],
            "target": {"track_id": 1}, "identity": {"state": "CONFIRMED", "reason": None, "segment": 1},
            "pose": {"quality": geometry.pose_quality(marks, overlap=False, touches_edge=False), "landmarks": marks},
            "angles": angles,
            "motion_proxy": {"speed_proxy_frame_heights_per_s": {"state": "NOT_MEASURABLE"},
                             "acceleration_proxy_frame_heights_per_s2": {"state": "NOT_MEASURABLE"},
                             "angular_velocity_deg_per_s": {}}}


def failing(frames, target=1):
    return [c["check"] for c in validate.validate(frames, None, target) if not c["passed"]]


def test_the_validator_accepts_a_clean_analysis():
    frames = [good_frame(i, i / 60) for i in range(5)]
    assert failing(frames) == []


def test_the_validator_catches_each_promise_being_broken():
    frames = [good_frame(i, i / 60) for i in range(5)]
    bad = copy.deepcopy(frames)
    bad[3]["timestamp_s"] = bad[1]["timestamp_s"]
    assert any("increasing" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[2]["frame_index"] = 1
    assert any("unique" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[2]["target"]["track_id"] = 7
    assert any("target id" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[1]["pose"]["landmarks"]["left_knee"]["x_norm"] = 1.4                       # usable but off-frame
    assert any("landmark coordinates" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[1]["angles"]["left_knee_angle_2d"]["value"] = 91.0                         # not what the landmarks give
    assert any("angles" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[1]["pose"]["landmarks"]["left_ankle"]["usable"] = False                    # angle without a usable joint
    assert any("angles" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[1]["identity"]["state"] = "LOST"
    assert any("angles" in c for c in failing(bad))
    bad = copy.deepcopy(frames)
    bad[1]["motion_proxy"]["speed_mps"] = {"state": "MEASURED"}
    assert any("physical-unit" in c for c in failing(bad))


def test_not_measurable_with_a_value_is_rejected():
    frames = [good_frame(i, i / 60) for i in range(3)]
    frames[1]["angles"]["left_knee_angle_2d"]["state"] = "NOT_MEASURABLE"
    assert any("angles" in c for c in failing(frames))


def test_no_physical_speed_module_or_field_exists_in_the_a1_package():
    import pathlib
    source = "\n".join(p.read_text() for p in pathlib.Path("app/video_ai").glob("*.py")
                       if p.name not in ("validate.py",))
    for word in ("meters_per_second", "m_per_s", "kmh", "km/h", "overall_rating", "kemet_rating", "sprint_time"):
        assert word not in source


# ---- anatomical plausibility ----------------------------------------------------------------------------------

BOX = (0.38, 0.20, 0.62, 0.85)


def test_a_normal_upright_pose_passes_the_plausibility_gate_untouched():
    marks = standing()
    assert geometry.apply_plausibility(marks, BOX) is None
    assert all(marks[n]["usable"] for n in geometry.CORE_JOINTS)


def test_an_upside_down_skeleton_fitted_to_a_close_up_is_rejected_and_never_measured():
    # the real failure seen on a face close-up: hips, knees and ankles ABOVE the shoulders, all "visible"
    marks = standing({"left_hip": (390, 590), "right_hip": (375, 590), "left_knee": (377, 506),
                      "right_knee": (360, 506), "left_ankle": (337, 432), "right_ankle": (320, 432)})
    assert geometry.apply_plausibility(marks, BOX) == "IMPLAUSIBLE_BODY_ORIENTATION"
    assert not any(m["usable"] for m in marks.values())
    angles = geometry.compute_angles(marks, True)
    assert all(a["state"] == "NOT_MEASURABLE" for a in angles.values())
    quality = geometry.pose_quality(marks, overlap=False, touches_edge=False,
                                    rejected_reason="IMPLAUSIBLE_BODY_ORIENTATION")
    assert quality["status"] == "LOW_CONFIDENCE" and quality["reason"] == "IMPLAUSIBLE_BODY_ORIENTATION"


def test_impossible_limb_proportions_are_rejected():
    marks = standing({"left_knee": (530, 1000), "left_ankle": (530, 1010)})        # thigh 100, shin 10 vs torso 400
    marks["left_knee"]["y_norm"], marks["left_ankle"]["y_norm"] = 0.5, 0.505
    assert geometry.apply_plausibility(marks, BOX) == "IMPLAUSIBLE_BODY_PROPORTIONS"


def test_an_ankle_in_the_top_of_the_box_is_dropped_but_the_rest_of_the_pose_stays():
    marks = standing({"left_ankle": (530, 640)})
    marks["left_ankle"]["y_norm"] = 640 / 2000.0
    assert geometry.apply_plausibility(marks, (0.38, 0.20, 0.62, 0.85)) is None
    assert marks["left_ankle"]["usable"] is False and marks["left_ankle"]["unusable_reason"] == "OUTSIDE_EXPECTED_BODY_REGION"
    assert marks["right_ankle"]["usable"] and marks["left_shoulder"]["usable"]


def test_a_lower_body_that_is_simply_not_visible_is_not_a_plausibility_failure():
    marks = standing(hidden=("left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"))
    assert geometry.apply_plausibility(marks, BOX) is None
    assert geometry.pose_quality(marks, overlap=False, touches_edge=False)["status"] == "PARTIAL"


def test_a_single_raised_knee_or_kicked_heel_is_not_mistaken_for_an_upside_down_body():
    high_knee = standing({"left_knee": (560, 700)})                  # knee well above the other one
    assert geometry.apply_plausibility(high_knee, BOX) is None
    assert all(high_knee[n]["usable"] for n in geometry.CORE_JOINTS)
    heel_up = standing({"left_ankle": (530, 1000)})                  # ankle raised toward the hip
    assert geometry.apply_plausibility(heel_up, BOX) is None
    assert heel_up["left_ankle"]["usable"] is False                   # but outside the lower part of the box
    assert heel_up["right_ankle"]["usable"] and heel_up["left_knee"]["usable"]


def test_a_box_wider_than_tall_is_not_an_upright_body_and_its_pose_is_rejected():
    marks = standing()
    wide = (0.20, 0.30, 0.90, 0.60)                       # in a 1000 x 2000 frame: 700 px wide, 600 px tall
    assert geometry.apply_plausibility(marks, wide, frame_aspect=0.5) == "IMPLAUSIBLE_BOX_ASPECT"
    assert not any(m["usable"] for m in marks.values())
    marks = standing()
    assert geometry.apply_plausibility(marks, BOX, frame_aspect=0.5) is None          # 480 px wide, 1300 px tall
