"""Landmark usability, pose quality and image-plane (2D) joint geometry.

All angles are measured between points in the upright image plane (pixels). They are NOT 3D joint angles, are not
corrected for camera viewpoint or lens, and are not a medical assessment. A landmark that is not usable is never
filled in, so any angle that needs it is NOT_MEASURABLE rather than estimated.
"""
import math

from app.pose_features import LANDMARK_INDEX, _angle, _line_tilt
from app.video_ai.config import ANGLE_DEFINITION_VERSION, POSE

LANDMARK_NAMES = [
    "nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
    "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder", "right_shoulder", "left_elbow",
    "right_elbow", "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index", "right_index",
    "left_thumb", "right_thumb", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
    "left_heel", "right_heel", "left_foot_index", "right_foot_index",
]
LANDMARK_NAME_TO_INDEX = {name: i for i, name in enumerate(LANDMARK_NAMES)}
# The existing repository map must agree with the 33-point model order used here.
assert all(LANDMARK_NAME_TO_INDEX[name] == index for name, index in LANDMARK_INDEX.items())

CORE_JOINTS = [
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
]
SKELETON = [
    ("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"), ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"), ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ("left_ankle", "left_heel"), ("left_heel", "left_foot_index"), ("left_ankle", "left_foot_index"),
    ("right_ankle", "right_heel"), ("right_heel", "right_foot_index"), ("right_ankle", "right_foot_index"),
]

POSE_STATES = ("GOOD", "PARTIAL", "LOW_CONFIDENCE", "OCCLUDED", "NOT_DETECTED")

# name -> (kind, landmarks). "interior" = angle at the middle landmark between the other two.
ANGLE_DEFINITIONS = {
    "left_knee_angle_2d": ("interior", ("left_hip", "left_knee", "left_ankle")),
    "right_knee_angle_2d": ("interior", ("right_hip", "right_knee", "right_ankle")),
    "left_hip_angle_2d": ("interior", ("left_shoulder", "left_hip", "left_knee")),
    "right_hip_angle_2d": ("interior", ("right_shoulder", "right_hip", "right_knee")),
    "left_elbow_angle_2d": ("interior", ("left_shoulder", "left_elbow", "left_wrist")),
    "right_elbow_angle_2d": ("interior", ("right_shoulder", "right_elbow", "right_wrist")),
    "trunk_lean_2d": ("trunk", ("left_shoulder", "right_shoulder", "left_hip", "right_hip")),
    "shoulder_line_angle_2d": ("line", ("left_shoulder", "right_shoulder")),
    "hip_line_angle_2d": ("line", ("left_hip", "right_hip")),
    "apparent_shoulder_width_ratio": ("ratio", ("left_shoulder", "right_shoulder", "left_hip", "right_hip")),
}
ANGLE_DESCRIPTIONS = {
    "left_knee_angle_2d": "image-plane angle hip-knee-ankle, degrees (180 = straight in the image)",
    "right_knee_angle_2d": "image-plane angle hip-knee-ankle, degrees (180 = straight in the image)",
    "left_hip_angle_2d": "image-plane angle shoulder-hip-knee, degrees",
    "right_hip_angle_2d": "image-plane angle shoulder-hip-knee, degrees",
    "left_elbow_angle_2d": "image-plane angle shoulder-elbow-wrist, degrees",
    "right_elbow_angle_2d": "image-plane angle shoulder-elbow-wrist, degrees",
    "trunk_lean_2d": "signed angle of the hip-midpoint to shoulder-midpoint line from image vertical, degrees; "
                     "positive = leaning toward image right; image-plane only, not a spine measurement",
    "shoulder_line_angle_2d": "tilt of the left-to-right shoulder line from image horizontal, degrees, "
                              "wrapped to (-90, 90]; sign depends on which way the body faces",
    "hip_line_angle_2d": "tilt of the left-to-right hip line from image horizontal, degrees, "
                         "wrapped to (-90, 90]; sign depends on which way the body faces",
    "apparent_shoulder_width_ratio": "2D shoulder width divided by 2D shoulder-to-hip length; a rough proxy "
                                     "for how frontal the torso looks to the camera, not a rotation angle",
}
ANGLE_UNITS = {name: ("ratio" if name.endswith("ratio") else "degrees") for name in ANGLE_DEFINITIONS}


def landmark_usable(visibility, presence, x_norm, y_norm, bbox_norm=None) -> tuple[bool, str | None]:
    """A landmark may be used only when the model is confident, it lies inside the image, and it lies on the
    tracked person's box (so a joint hallucinated below a half-visible body, or taken from a neighbour, is out)."""
    if visibility is None or presence is None:
        return False, "NO_CONFIDENCE"
    if visibility < POSE["min_visibility"] or presence < POSE["min_presence"]:
        return False, "LOW_CONFIDENCE"
    if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
        return False, "OUTSIDE_FRAME"
    if bbox_norm is not None:
        x1, y1, x2, y2 = bbox_norm
        slack_x = (x2 - x1) * POSE["bbox_slack"]
        slack_y = (y2 - y1) * POSE["bbox_slack"]
        if not (x1 - slack_x <= x_norm <= x2 + slack_x and y1 - slack_y <= y_norm <= y2 + slack_y):
            return False, "OUTSIDE_TARGET_BOX"
    return True, None


def pose_quality(landmarks: dict | None, *, overlap: bool, touches_edge: bool,
                 rejected_reason: str | None = None) -> dict:
    """Frame pose quality from the 12 core joints. `landmarks` maps name -> dict with visibility and usable;
    None means the model produced no pose for the target."""
    if landmarks is None:
        return {"status": "NOT_DETECTED", "reason": "NO_POSE_FOR_TARGET", "usable_core": 0,
                "hidden_core": 0, "mean_core_visibility": None}
    if rejected_reason:
        return {"status": "LOW_CONFIDENCE", "reason": rejected_reason, "usable_core": 0, "hidden_core": 0,
                "mean_core_visibility": round(sum(landmarks[n]["visibility"] for n in CORE_JOINTS) / len(CORE_JOINTS), 4)}
    core = [landmarks[name] for name in CORE_JOINTS]
    usable = sum(1 for item in core if item["usable"])
    hidden = sum(1 for item in core if item["visibility"] < POSE["hidden_visibility"])
    mean_visibility = sum(item["visibility"] for item in core) / len(core)
    base = {"usable_core": usable, "hidden_core": hidden, "mean_core_visibility": round(mean_visibility, 4)}
    if usable >= POSE["good_min_usable_core"] and mean_visibility >= POSE["good_min_mean_visibility"]:
        return {"status": "GOOD", "reason": None, **base}
    if hidden >= POSE["occluded_min_hidden_core"] and (overlap or touches_edge):
        return {"status": "OCCLUDED", "reason": "OVERLAPPED_BY_PERSON" if overlap else "CUT_BY_FRAME_EDGE", **base}
    if usable >= POSE["partial_min_usable_core"]:
        return {"status": "PARTIAL", "reason": "SOME_CORE_JOINTS_NOT_USABLE", **base}
    return {"status": "LOW_CONFIDENCE", "reason": "FEW_USABLE_CORE_JOINTS", **base}


def _mid(landmarks: dict, left: str, right: str):
    pts = [(landmarks[n]["x_norm"], landmarks[n]["y_norm"]) for n in (left, right) if landmarks[n]["usable"]]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def apply_plausibility(landmarks: dict, bbox_norm, frame_aspect: float | None = None) -> str | None:
    """Reject anatomically impossible poses (a model can fit a whole body to a face close-up, or hallucinate legs
    on a half-visible person, and still report high visibility). Works on the already-usable joints:
    returns a rejection reason and marks every joint unusable, or marks only the individual joints that sit in
    the wrong part of the person's box, or returns None. Assumes an upright camera, so a body lying or inverted
    in the image becomes NOT_MEASURABLE rather than a wrong number."""
    x1, y1, x2, y2 = bbox_norm
    height = max(y2 - y1, 1e-9)

    def reject(reason):
        for item in landmarks.values():
            if item["usable"]:
                item["usable"], item["unusable_reason"] = False, reason
        return reason

    if frame_aspect is not None and (y2 - y1) / max((x2 - x1) * frame_aspect, 1e-9) < POSE["min_box_height_over_width"]:
        return reject("IMPLAUSIBLE_BOX_ASPECT")      # e.g. a face close-up or a screen, not an upright body

    chain = [m for m in (_mid(landmarks, "left_shoulder", "right_shoulder"), _mid(landmarks, "left_hip", "right_hip"),
                         _mid(landmarks, "left_knee", "right_knee"), _mid(landmarks, "left_ankle", "right_ankle"))
             if m is not None]
    tolerance = POSE["orientation_tolerance_box_fraction"] * height
    reversed_links = [b[1] < a[1] - tolerance for a, b in zip(chain, chain[1:])]
    torso_inverted = (_mid(landmarks, "left_shoulder", "right_shoulder") is not None
                      and _mid(landmarks, "left_hip", "right_hip") is not None and reversed_links[0])
    # one reversed link can be a real movement (a raised knee, a heel flicked up in a kick); an inverted torso, or
    # two or more reversed links, is a skeleton fitted the wrong way up
    if torso_inverted or sum(reversed_links) >= 2:
        return reject("IMPLAUSIBLE_BODY_ORIENTATION")

    def length(a, b):
        if landmarks[a]["usable"] and landmarks[b]["usable"]:
            return math.hypot(landmarks[a]["x_px"] - landmarks[b]["x_px"], landmarks[a]["y_px"] - landmarks[b]["y_px"])
        return None

    ls, rs, lh, rh = (landmarks[n] for n in ("left_shoulder", "right_shoulder", "left_hip", "right_hip"))
    if all(item["usable"] for item in (ls, rs, lh, rh)):
        torso = math.hypot((ls["x_px"] + rs["x_px"] - lh["x_px"] - rh["x_px"]) / 2,
                           (ls["y_px"] + rs["y_px"] - lh["y_px"] - rh["y_px"]) / 2)
        for side in ("left", "right"):
            for a, b in ((f"{side}_hip", f"{side}_knee"), (f"{side}_knee", f"{side}_ankle")):
                seg = length(a, b)
                if seg is not None and torso > 0 and not (POSE["min_segment_ratio"] <= seg / torso
                                                          <= POSE["max_segment_ratio"]):
                    return reject("IMPLAUSIBLE_BODY_PROPORTIONS")

    for name in ("left_ankle", "right_ankle"):
        item = landmarks[name]
        if item["usable"] and (item["y_norm"] - y1) / height < POSE["ankle_min_box_fraction"]:
            item["usable"], item["unusable_reason"] = False, "OUTSIDE_EXPECTED_BODY_REGION"
    for name in ("left_shoulder", "right_shoulder"):
        item = landmarks[name]
        if item["usable"] and (item["y_norm"] - y1) / height > POSE["shoulder_max_box_fraction"]:
            item["usable"], item["unusable_reason"] = False, "OUTSIDE_EXPECTED_BODY_REGION"
    return None


def _px(landmarks: dict, name: str):
    item = landmarks[name]
    return (item["x_px"], item["y_px"], 0.0)


def _missing(landmarks: dict, names) -> list[str]:
    return [name for name in names if not landmarks[name]["usable"]]


def _signed_trunk_lean(shoulder_mid, hip_mid) -> float | None:
    dx = shoulder_mid[0] - hip_mid[0]
    dy = shoulder_mid[1] - hip_mid[1]
    if math.hypot(dx, dy) == 0.0:
        return None
    return math.degrees(math.atan2(dx, -dy))


def compute_angles(landmarks: dict | None, identity_ok: bool, identity_reason: str | None = None) -> dict:
    """Every defined measurement, each with value/confidence/state/definition. Never an estimate: if a needed
    landmark is not usable (or the identity is not confirmed) the value is null and the state is NOT_MEASURABLE."""
    out = {}
    for name, (kind, needed) in ANGLE_DEFINITIONS.items():
        entry = {"value": None, "unit": ANGLE_UNITS[name], "confidence": None, "state": "NOT_MEASURABLE",
                 "reason": None, "definition_version": ANGLE_DEFINITION_VERSION, "space": "IMAGE_PLANE_2D"}
        out[name] = entry
        if not identity_ok:
            entry["reason"] = identity_reason or "IDENTITY_NOT_CONFIRMED"
            continue
        if landmarks is None:
            entry["reason"] = "NO_POSE"
            continue
        absent = _missing(landmarks, needed)
        if absent:
            entry["reason"] = "LANDMARK_NOT_USABLE:" + ",".join(absent)
            continue
        value = None
        if kind == "interior":
            value = _angle(*(_px(landmarks, n) for n in needed))
        elif kind == "line":
            a, b = (_px(landmarks, n) for n in needed)
            value = _line_tilt(a, b)
        elif kind == "trunk":
            ls, rs, lh, rh = (_px(landmarks, n) for n in needed)
            value = _signed_trunk_lean(tuple((a + b) / 2 for a, b in zip(ls, rs)),
                                       tuple((a + b) / 2 for a, b in zip(lh, rh)))
        elif kind == "ratio":
            ls, rs, lh, rh = (_px(landmarks, n) for n in needed)
            shoulder_mid = tuple((a + b) / 2 for a, b in zip(ls, rs))
            hip_mid = tuple((a + b) / 2 for a, b in zip(lh, rh))
            torso = math.dist(shoulder_mid, hip_mid)
            value = (math.dist(ls, rs) / torso) if torso > 0 else None
        if value is None or not math.isfinite(value):
            entry["reason"] = "DEGENERATE_GEOMETRY"
            continue
        entry["value"] = round(float(value), 3)
        entry["confidence"] = round(min(landmarks[n]["visibility"] for n in needed), 4)
        entry["state"] = "MEASURED"
    return out
