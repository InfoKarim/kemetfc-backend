"""Movement time series from the tracked pose: VIDEO_MOTION_PROXY.

Everything is in IMAGE units (frame heights, degrees, hertz). Nothing here is metres, m/s, distance travelled or a
real sprint speed: without a calibrated scale and a stable (or compensated) camera an image displacement is not a
physical speed. Derivatives are computed only inside one identity segment and only over frames that are
measured, never across a gap, and never interpolated. Frames at the edge of a run, where a smoothing filter has
one-sided support, are NOT_MEASURABLE.
"""
import math

import numpy as np
from scipy.signal import lombscargle, savgol_filter

from app.video_ai.config import PROXY, PROXY_DEFINITION_VERSION

LABEL = "VIDEO_MOTION_PROXY"
DERIVED_ANGLES = ("left_knee_angle_2d", "right_knee_angle_2d", "left_hip_angle_2d", "right_hip_angle_2d",
                  "left_elbow_angle_2d", "right_elbow_angle_2d")


def _empty_proxy(reason: str) -> dict:
    return {"value": None, "state": "NOT_MEASURABLE", "reason": reason}


def _runs_of_true(mask: list[bool]) -> list[tuple[int, int]]:
    runs, start = [], None
    for index, flag in enumerate(mask):
        if flag and start is None:
            start = index
        if not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _uniform(times: np.ndarray) -> bool:
    steps = np.diff(times)
    if len(steps) == 0:
        return False
    median = float(np.median(steps))
    return median > 0 and float(np.max(np.abs(steps - median))) <= 0.25 * median


def savgol_run(times: np.ndarray, values: np.ndarray, deriv: int):
    """Derivative of a smoothed contiguous run, or None when the run is too short / non-uniform in time.
    Returns an array the length of the run with NaN at both edges (no one-sided estimates)."""
    window = PROXY["smoothing_window_frames"]
    if len(values) < max(window, PROXY["min_segment_frames"]) or not _uniform(times):
        return None
    dt = float(np.median(np.diff(times)))
    smooth = savgol_filter(values, window, 2, deriv=deriv, delta=dt, mode="interp")
    smooth = smooth.astype(float)
    edge = window // 2
    smooth[:edge] = np.nan
    smooth[-edge:] = np.nan
    return smooth


def per_frame_proxies(frames: list[dict], aspect_ratio: float) -> list[dict]:
    """frames[i]: {t, state, segment, center (x_norm, y_norm)|None, angles: {name: value|None}, camera_moving}.
    Returns one dict per frame."""
    count = len(frames)
    out = [{"label": LABEL, "definition_version": PROXY_DEFINITION_VERSION, "space": "IMAGE_UNITS_NOT_PHYSICAL",
            "camera_motion_present": bool(f.get("camera_moving")),
            "camera_compensated": False,
            "body_center": None,
            "speed_proxy_frame_heights_per_s": _empty_proxy("NOT_COMPUTED"),
            "acceleration_proxy_frame_heights_per_s2": _empty_proxy("NOT_COMPUTED"),
            "angular_velocity_deg_per_s": {name: _empty_proxy("NOT_COMPUTED") for name in DERIVED_ANGLES}}
           for f in frames]

    reason_for_frame = [None] * count
    for i, f in enumerate(frames):
        if f["state"] != "CONFIRMED":
            reason_for_frame[i] = "IDENTITY_NOT_CONFIRMED"
        elif f.get("center") is None:
            reason_for_frame[i] = "NO_TARGET_BOX"
        else:
            out[i]["body_center"] = {"x_frame_heights": round(f["center"][0] * aspect_ratio, 5),
                                     "y_frame_heights": round(f["center"][1], 5),
                                     "source": "TARGET_BBOX_CENTER"}
    for i in range(count):
        if reason_for_frame[i]:
            out[i]["speed_proxy_frame_heights_per_s"] = _empty_proxy(reason_for_frame[i])
            out[i]["acceleration_proxy_frame_heights_per_s2"] = _empty_proxy(reason_for_frame[i])

    # position derivatives: one run per identity segment, contiguous frame indices only
    ok = [reason_for_frame[i] is None for i in range(count)]
    for start, end in _runs_of_true(ok):
        idx = list(range(start, end + 1))
        # a run may span two segments only if the segment id changed without a non-confirmed frame; split anyway
        pieces, piece = [], [idx[0]]
        for a, b in zip(idx, idx[1:]):
            if frames[a]["segment"] == frames[b]["segment"]:
                piece.append(b)
            else:
                pieces.append(piece)
                piece = [b]
        pieces.append(piece)
        for piece in pieces:
            times = np.array([frames[i]["t"] for i in piece])
            xs = np.array([out[i]["body_center"]["x_frame_heights"] for i in piece])
            ys = np.array([out[i]["body_center"]["y_frame_heights"] for i in piece])
            vx, vy = savgol_run(times, xs, 1), savgol_run(times, ys, 1)
            ax, ay = savgol_run(times, xs, 2), savgol_run(times, ys, 2)
            if vx is None:
                for i in piece:
                    why = "RUN_TOO_SHORT_OR_NONUNIFORM_TIME"
                    out[i]["speed_proxy_frame_heights_per_s"] = _empty_proxy(why)
                    out[i]["acceleration_proxy_frame_heights_per_s2"] = _empty_proxy(why)
                continue
            for k, i in enumerate(piece):
                if math.isnan(vx[k]):
                    why = "RUN_EDGE"
                    out[i]["speed_proxy_frame_heights_per_s"] = _empty_proxy(why)
                    out[i]["acceleration_proxy_frame_heights_per_s2"] = _empty_proxy(why)
                    continue
                speed = math.hypot(vx[k], vy[k])
                tangential = ((ax[k] * vx[k] + ay[k] * vy[k]) / speed) if speed > 1e-9 else None
                out[i]["speed_proxy_frame_heights_per_s"] = {"value": round(speed, 5), "state": "MEASURED",
                                                             "reason": None}
                out[i]["acceleration_proxy_frame_heights_per_s2"] = (
                    {"value": round(tangential, 5), "state": "MEASURED", "reason": None} if tangential is not None
                    else _empty_proxy("NO_MOTION_DIRECTION"))

    # angular velocity per joint angle, only over runs of consecutive frames in which that angle was measured
    for name in DERIVED_ANGLES:
        measured = [(frames[i]["state"] == "CONFIRMED" and frames[i]["angles"].get(name) is not None)
                    for i in range(count)]
        for i in range(count):
            why = "IDENTITY_NOT_CONFIRMED" if frames[i]["state"] != "CONFIRMED" else "ANGLE_NOT_MEASURABLE"
            if not measured[i]:
                out[i]["angular_velocity_deg_per_s"][name] = _empty_proxy(why)
        for start, end in _runs_of_true(measured):
            run = list(range(start, end + 1))
            if len({frames[i]["segment"] for i in run}) != 1:
                for i in run:
                    out[i]["angular_velocity_deg_per_s"][name] = _empty_proxy("SPANS_IDENTITY_SEGMENTS")
                continue
            times = np.array([frames[i]["t"] for i in run])
            values = np.array([frames[i]["angles"][name] for i in run])
            derived = savgol_run(times, values, 1)
            for k, i in enumerate(run):
                if derived is None:
                    out[i]["angular_velocity_deg_per_s"][name] = _empty_proxy("RUN_TOO_SHORT_OR_NONUNIFORM_TIME")
                elif math.isnan(derived[k]):
                    out[i]["angular_velocity_deg_per_s"][name] = _empty_proxy("RUN_EDGE")
                else:
                    out[i]["angular_velocity_deg_per_s"][name] = {"value": round(float(derived[k]), 3),
                                                                  "state": "MEASURED", "reason": None}
    return out


def _band_peak(times: np.ndarray, values: np.ndarray):
    low, high = PROXY["cadence_band_hz"]
    freqs = np.arange(low, high + 1e-9, 0.01)
    centred = values - values.mean()
    if float(np.std(centred)) < 1e-6:
        return None
    power = lombscargle(times - times[0], centred, 2 * math.pi * freqs)
    total = float(power.sum())
    if total <= 0:
        return None
    peak = int(np.argmax(power))
    # a 4 s window resolves ~0.25 Hz, so a real oscillation spreads over its neighbouring bins: count them
    near = np.abs(freqs - freqs[peak]) <= 1.0 / PROXY["cadence_window_seconds"]
    return float(freqs[peak]), float(power[near].sum() / total)


def window_proxies(frames: list[dict], fps: float) -> list[dict]:
    """Cadence proxy and left/right asymmetry over fixed windows. A window is measurable only if every frame in it
    is CONFIRMED in one identity segment and at least cadence_min_usable_fraction of its frames have the angle."""
    if not frames:
        return []
    span, hop = PROXY["cadence_window_seconds"], PROXY["cadence_hop_seconds"]
    times = np.array([f["t"] for f in frames])
    results = []
    start_time = float(times[0])
    end_time = float(times[-1])
    t = start_time
    while t + span <= end_time + 1e-9:
        lo = int(np.searchsorted(times, t, side="left"))
        hi = int(np.searchsorted(times, t + span, side="right"))
        idx = list(range(lo, hi))
        entry = {"start_s": round(t, 3), "end_s": round(t + span, 3), "start_frame": lo, "end_frame": hi - 1,
                 "label": LABEL, "definition_version": PROXY_DEFINITION_VERSION}
        results.append(entry)
        t += hop
        whole_window_ok = (len(idx) > 0 and all(frames[i]["state"] == "CONFIRMED" for i in idx)
                           and len({frames[i]["segment"] for i in idx}) == 1)
        for key in ("left_knee_dominant_frequency_hz", "right_knee_dominant_frequency_hz"):
            entry[key] = _empty_proxy("IDENTITY_NOT_CONFIRMED_THROUGHOUT_WINDOW")
        entry["knee_angle_asymmetry_mean_abs_deg"] = _empty_proxy("IDENTITY_NOT_CONFIRMED_THROUGHOUT_WINDOW")
        entry["knee_range_asymmetry_ratio"] = _empty_proxy("IDENTITY_NOT_CONFIRMED_THROUGHOUT_WINDOW")
        if not whole_window_ok:
            continue
        for side in ("left", "right"):
            name = f"{side}_knee_angle_2d"
            key = f"{side}_knee_dominant_frequency_hz"
            have = [i for i in idx if frames[i]["angles"].get(name) is not None]
            if len(have) < PROXY["cadence_min_usable_fraction"] * len(idx):
                entry[key] = _empty_proxy("KNEE_ANGLE_NOT_MEASURABLE_ENOUGH_OF_WINDOW")
                continue
            peak = _band_peak(times[have], np.array([frames[i]["angles"][name] for i in have]))
            if peak is None or peak[1] < PROXY["cadence_min_peak_fraction"]:
                entry[key] = _empty_proxy("NO_PERIODICITY")
                continue
            entry[key] = {"value": round(peak[0], 3), "state": "MEASURED", "reason": None,
                          "peak_power_fraction": round(peak[1], 3),
                          "note": "dominant oscillation of one knee's image-plane angle; not a validated cadence"}
        both = [i for i in idx if frames[i]["angles"].get("left_knee_angle_2d") is not None
                and frames[i]["angles"].get("right_knee_angle_2d") is not None]
        if len(both) >= PROXY["cadence_min_usable_fraction"] * len(idx):
            left = np.array([frames[i]["angles"]["left_knee_angle_2d"] for i in both])
            right = np.array([frames[i]["angles"]["right_knee_angle_2d"] for i in both])
            entry["knee_angle_asymmetry_mean_abs_deg"] = {"value": round(float(np.mean(np.abs(left - right))), 3),
                                                          "state": "MEASURED", "reason": None}
            rom_l, rom_r = float(left.max() - left.min()), float(right.max() - right.min())
            top = max(rom_l, rom_r)
            entry["knee_range_asymmetry_ratio"] = (
                {"value": round((rom_l - rom_r) / top, 3), "state": "MEASURED", "reason": None} if top > 0
                else _empty_proxy("NO_KNEE_MOTION"))
        else:
            for key in ("knee_angle_asymmetry_mean_abs_deg", "knee_range_asymmetry_ratio"):
                entry[key] = _empty_proxy("KNEE_ANGLES_NOT_MEASURABLE_ENOUGH_OF_WINDOW")
    return results
