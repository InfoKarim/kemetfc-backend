"""Independent checks of an A1 analysis. These recompute from the stored landmarks rather than trusting the
stored angles, and check the rules the analysis promises (no estimates, no silent identity switch, no physical
units)."""
import math

from app.video_ai.geometry import ANGLE_DEFINITIONS, LANDMARK_NAMES, POSE_STATES

PHYSICAL_UNIT_WORDS = ("m/s", "meter", "metre", "km/h", "mph", "_mps", "kmh")


def _interior(a, b, c):
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _recompute(name, marks):
    kind, needed = ANGLE_DEFINITIONS[name]
    pts = [(marks[n]["x_px"], marks[n]["y_px"]) for n in needed]
    if kind == "interior":
        return _interior(*pts)
    if kind == "line":
        angle = math.degrees(math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
        return ((angle + 90.0) % 180.0) - 90.0
    ls, rs, lh, rh = pts
    sm = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hm = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    if kind == "trunk":
        return math.degrees(math.atan2(sm[0] - hm[0], -(sm[1] - hm[1])))
    torso = math.dist(sm, hm)
    return math.dist(ls, rs) / torso if torso > 0 else None


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def validate(frames: list[dict], expected_timestamps: list[float] | None, target_id: int | None,
             windows: list[dict] | None = None) -> list[dict]:
    checks = []

    def record(name, problems):
        checks.append({"check": name, "passed": not problems, "problems": problems[:10],
                       "problem_count": len(problems)})

    record("frame ids are unique and contiguous from 0",
           [i for i, f in enumerate(frames) if f["frame_index"] != i])
    times = [f["timestamp_s"] for f in frames]
    record("timestamps are strictly increasing", [i for i in range(1, len(times)) if not times[i] > times[i - 1]])
    if expected_timestamps is not None:
        record("timestamps equal the container's presentation timestamps",
               [i for i, (a, b) in enumerate(zip(times, expected_timestamps)) if abs(a - b) > 1e-5]
               + ([-1] if len(times) != len(expected_timestamps) else []))
    problems = []
    for f in frames:
        flagged = [p for p in f["persons"] if p["is_target"]]
        if len(flagged) > 1 or (f["target"] is None) != (len(flagged) == 0):
            problems.append(f["frame_index"])
        if f["target"] is not None and f["target"]["track_id"] != target_id:
            problems.append(f["frame_index"])
    record("one fixed target id; never two targets in a frame; never another id", problems)

    problems = []
    for f in frames:
        marks = f["pose"]["landmarks"]
        if marks is None:
            continue
        if set(marks) != set(LANDMARK_NAMES):
            problems.append((f["frame_index"], "landmark set"))
            continue
        for name, mark in marks.items():
            values = [mark[k] for k in ("x_px", "y_px", "x_norm", "y_norm", "visibility", "presence")]
            if not all(math.isfinite(v) for v in values):
                problems.append((f["frame_index"], name, "not finite"))
            elif mark["usable"] and not (0 <= mark["x_norm"] <= 1 and 0 <= mark["y_norm"] <= 1):
                problems.append((f["frame_index"], name, "usable but outside the frame"))
            elif not (0 <= mark["visibility"] <= 1 and 0 <= mark["presence"] <= 1):
                problems.append((f["frame_index"], name, "confidence out of range"))
    record("landmark coordinates are finite, usable ones inside the frame, confidences in [0,1]", problems)

    problems = []
    for f in frames:
        marks = f["pose"]["landmarks"]
        for name, entry in f["angles"].items():
            kind, needed = ANGLE_DEFINITIONS[name]
            if entry["state"] == "MEASURED":
                if entry["value"] is None or f["identity"]["state"] != "CONFIRMED" or marks is None \
                        or not all(marks[n]["usable"] for n in needed):
                    problems.append((f["frame_index"], name, "measured without usable landmarks or identity"))
                    continue
                again = _recompute(name, marks)
                if again is None or abs(again - entry["value"]) > 0.01:
                    problems.append((f["frame_index"], name, "does not match recomputation", again, entry["value"]))
                v = entry["value"]
                if kind == "interior" and not 0 <= v <= 180:
                    problems.append((f["frame_index"], name, "interior angle outside 0..180"))
                if kind == "ratio" and v < 0:
                    problems.append((f["frame_index"], name, "negative ratio"))
                if kind == "line" and not -90 <= v <= 90:
                    problems.append((f["frame_index"], name, "line tilt outside -90..90"))
                if kind == "trunk" and not -180 <= v <= 180:
                    problems.append((f["frame_index"], name, "lean outside -180..180"))
            elif entry["value"] is not None or entry["reason"] is None:
                problems.append((f["frame_index"], name, "NOT_MEASURABLE must have a null value and a reason"))
    record("angles: only from usable landmarks and confirmed identity, mathematically valid, reproducible by an "
           "independent recomputation, NOT_MEASURABLE has no value", problems)

    problems = []
    for f in frames:
        if f["pose"]["quality"]["status"] not in POSE_STATES:
            problems.append((f["frame_index"], "unknown pose status"))
        if f["identity"]["state"] != "CONFIRMED":
            mp = f["motion_proxy"]
            if mp["speed_proxy_frame_heights_per_s"]["state"] == "MEASURED" or \
                    mp["acceleration_proxy_frame_heights_per_s2"]["state"] == "MEASURED" or \
                    any(v["state"] == "MEASURED" for v in mp["angular_velocity_deg_per_s"].values()):
                problems.append((f["frame_index"], "proxy measured while identity not confirmed"))
    record("pose states are defined; no movement proxy where identity is not confirmed", problems)

    problems = []
    for f in frames:
        seg = f["identity"]["segment"]
        if (f["identity"]["state"] == "CONFIRMED") != (seg is not None):
            problems.append((f["frame_index"], "segment id mismatch"))
    segment_frames = {}
    for f in frames:
        if f["identity"]["segment"] is not None:
            segment_frames.setdefault(f["identity"]["segment"], []).append(f["frame_index"])
    for seg, idx in segment_frames.items():
        if idx != list(range(idx[0], idx[-1] + 1)):
            problems.append((seg, "identity segment is not contiguous"))
    record("identity segments are contiguous runs of confirmed frames", problems)

    keys = []
    for f in frames[:50]:
        for path, _ in _walk(f["motion_proxy"]):
            keys.append(path)
    banned = [k for k in set(keys) if any(w in k.lower() for w in PHYSICAL_UNIT_WORDS)]
    record("no physical-unit speed or distance fields exist", banned)
    return checks
