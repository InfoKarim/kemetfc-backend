"""SYNTHETIC FIXTURES, for testing the pipeline only.

These are generated stick-figure sequences with made-up players, videos and drill types. They are not real people, not
real movement and not evidence of anything about football. A model trained on them can validate that the plumbing
works and can NEVER be approved (registry.approval_problems). Nothing here is, or imitates, a real child.
"""
import hashlib
import math

import numpy as np

from app.ml1.config import ANGLE_SCALE, ANGLES, KEYPOINTS, MOTION, SCHEMA_VERSION, SYNTHETIC_SOURCE
from app.ml1.consent import SYNTHETIC_CONSENT_KIND, anonymous_player_id

SYNTHETIC_SALT = b"synthetic-fixture-salt-0123456789"
DRILLS = {"SYNTH_DRILL_A": {"hz": 1.7, "swing": 0.55, "flex": 0.9, "arm": 0.5},
          "SYNTH_DRILL_B": {"hz": 0.8, "swing": 0.35, "flex": 1.4, "arm": 0.2},
          "SYNTH_DRILL_C": {"hz": 1.2, "swing": 0.2, "flex": 0.5, "arm": 0.8}}


def _pose(t: float, drill: dict, phase0: float, scale: float):
    """Joint positions in torso units: hip centre at the origin, torso length 1, y points down."""
    phi = 2 * math.pi * drill["hz"] * t + phase0
    pts = {"nose": (0.0, -1.45), "left_shoulder": (-0.22, -1.0), "right_shoulder": (0.22, -1.0),
           "left_hip": (-0.15, 0.0), "right_hip": (0.15, 0.0)}
    angles = {}
    for side, sign, off in (("left", -1, 0.0), ("right", 1, math.pi)):
        a = drill["swing"] * math.sin(phi + off)
        flex = drill["flex"] * (1 + math.sin(phi + off + math.pi / 2)) / 2
        hx, hy = pts[f"{side}_hip"]
        kx, ky = hx + 0.95 * math.sin(a), hy + 0.95 * math.cos(a)
        ax, ay = kx + 0.95 * math.sin(a - flex), ky + 0.95 * math.cos(a - flex)
        pts[f"{side}_knee"], pts[f"{side}_ankle"] = (kx, ky), (ax, ay)
        arm = drill["arm"] * math.sin(phi + off + math.pi)
        sx, sy = pts[f"{side}_shoulder"]
        ex, ey = sx + 0.6 * math.sin(arm), sy + 0.6 * math.cos(arm)
        pts[f"{side}_elbow"] = (ex, ey)
        pts[f"{side}_wrist"] = (ex + 0.55 * math.sin(arm + 0.5), ey + 0.55 * math.cos(arm + 0.5))
        angles[f"{side}_knee_angle_2d"] = 180.0 - math.degrees(flex)
        angles[f"{side}_hip_angle_2d"] = 180.0 - abs(math.degrees(a))
        angles[f"{side}_elbow_angle_2d"] = 160.0 - 40 * abs(math.sin(arm))
    angles["trunk_lean_2d"] = 5.0 * math.sin(phi / 2)
    angles["shoulder_line_angle_2d"] = 3.0 * math.sin(phi)
    angles["hip_line_angle_2d"] = 4.0 * math.sin(phi)
    angles["apparent_shoulder_width_ratio"] = 0.44
    return {k: (x * scale, y * scale) for k, (x, y) in pts.items()}, angles


def make_arrays(rng: np.random.RandomState, drill_name: str, *, T: int = 48, hz: float = 15.0, joint_dropout: float = 0.1,
                frame_dropout: float = 0.05, t0: float = 0.0) -> dict:
    drill = DRILLS[drill_name]
    phase0 = rng.uniform(0, 2 * math.pi)
    scale = rng.uniform(0.9, 1.1)
    kp = np.zeros((T, len(KEYPOINTS), 3), np.float32)
    kpm = np.zeros((T, len(KEYPOINTS)), bool)
    ang = np.zeros((T, len(ANGLES)), np.float32)
    angm = np.zeros((T, len(ANGLES)), bool)
    mot = np.zeros((T, len(MOTION)), np.float32)
    motm = np.zeros((T, len(MOTION)), bool)
    for i in range(T):
        pts, angles = _pose(i / hz, drill, phase0, scale)
        if rng.rand() < frame_dropout:
            pts_visible = {}
        else:
            pts_visible = {k: v for k, v in pts.items() if rng.rand() >= joint_dropout}
        for k, name in enumerate(KEYPOINTS):
            if name in pts_visible:
                x, y = pts_visible[name]
                kp[i, k] = (x + rng.normal(0, 0.02), y + rng.normal(0, 0.02), rng.uniform(0.6, 1.0))
                kpm[i, k] = True
        for a, name in enumerate(ANGLES):
            if pts_visible and rng.rand() > 0.15:
                is_ratio = name.endswith("ratio")
                unit = ANGLE_SCALE["ratio" if is_ratio else "degrees"]
                ang[i, a], angm[i, a] = (angles[name] + rng.normal(0, 0.02 if is_ratio else 1.0)) / unit, True
        mot[i, 2], motm[i, 2] = rng.uniform(0.7, 0.95), True
        mot[i, 3], motm[i, 3] = float(rng.rand() < 0.3), True
        if i > 0:
            mot[i, 0], mot[i, 1], motm[i, 0], motm[i, 1] = rng.normal(0, 0.05), rng.normal(0, 0.05), True, True
    return {"keypoints": kp, "keypoint_mask": kpm, "angles": ang, "angle_mask": angm, "motion": mot, "motion_mask": motm,
            "t": t0 + np.arange(T, dtype=np.float64) / hz}


def example_rng(seed: int, p: int, v: int, w: int) -> np.random.RandomState:
    """Each fixture example has its own deterministic generator, so a claim "this is synthetic" can be PROVEN by
    regenerating the example from (seed, p, v, w) and comparing every number (verify_synthetic)."""
    return np.random.RandomState(int(hashlib.sha256(f"{seed}:{p}:{v}:{w}".encode()).hexdigest()[:8], 16))


def verify_synthetic(meta: dict, arrays: dict) -> list[str]:
    """Problems with a claim that an example is a synthetic fixture. Real data cannot pass: it would have to equal the
    generator's output bit for bit."""
    prov = meta.get("provenance", {})
    try:
        seed, (p, v, w) = int(prov["seed"]), [int(x) for x in prov["index"]]
        drill = prov["drill"]
        expected = make_arrays(example_rng(seed, p, v, w), drill, t0=w * 3.2)
    except Exception:
        return ["SYNTHETIC_CLAIM_NOT_PROVEN:NO_GENERATOR_RECIPE"]
    if meta.get("example_id") != f"SYN-{p:03d}-{v}-{w:02d}" or prov.get("generator") != "app.ml1.synthetic":
        return ["SYNTHETIC_CLAIM_NOT_PROVEN:IDS_DO_NOT_MATCH_THE_RECIPE"]
    if meta.get("anonymous_player_id") != anonymous_player_id(f"SYNTHETIC-PLAYER-{p:03d}", SYNTHETIC_SALT):
        return ["SYNTHETIC_CLAIM_NOT_PROVEN:PLAYER_DOES_NOT_MATCH_THE_RECIPE"]
    for name, value in expected.items():
        if name not in arrays or arrays[name].shape != value.shape or not np.array_equal(arrays[name], value):
            return ["SYNTHETIC_CLAIM_NOT_PROVEN:ARRAYS_ARE_NOT_THE_GENERATORS_OUTPUT"]
    return []


def make_synthetic_examples(n_players: int = 8, videos_per_player: int = 2, windows_per_video: int = 6, seed: int = 7) -> list[tuple[dict, dict]]:
    drills = sorted(DRILLS)
    out = []
    for p in range(n_players):
        player = anonymous_player_id(f"SYNTHETIC-PLAYER-{p:03d}", SYNTHETIC_SALT)
        for v in range(videos_per_player):
            video_id = f"SYNTHVID-{p:03d}-{v}"
            drill = drills[(p + v) % len(drills)]
            for w in range(windows_per_video):
                meta = {
                    "schema_version": SCHEMA_VERSION, "example_id": f"SYN-{p:03d}-{v}-{w:02d}", "anonymous_player_id": player,
                    "assessment_id": f"SYNTHASSESS-{p:03d}-{v}", "video_id": video_id, "drill_type": drill,
                    "target": {"label": "SYNTHETIC_TARGET", "source": "SYNTHETIC_TEST_TAP", "identity_verified_by_coach": False, "epoch": 1},
                    "window": {"start_frame": w * 48, "end_frame": w * 48 + 47, "start_s": w * 3.2, "end_s": w * 3.2 + 3.13, "frames": 48, "epoch": 1, "segment": 1},
                    "ball_interaction": {"state": "NOT_AVAILABLE"},
                    "labels": [{"label_type": "drill_type", "value": drill, "status": "SYNTHETIC_GROUND_TRUTH", "source": "SYNTHETIC_GENERATOR"}],
                    "model_confidence": {"mean_tracking_confidence": 0.85, "mean_keypoint_confidence": 0.8, "observed_fraction": 0.9},
                    "consent": {"kind": SYNTHETIC_CONSENT_KIND},
                    "provenance": {"source": SYNTHETIC_SOURCE, "synthetic": True, "generator": "app.ml1.synthetic", "seed": seed,
                                   "index": [p, v, w], "drill": drill,
                                   "note": "generated stick figures; not a person, not football evidence"},
                }
                out.append((meta, make_arrays(example_rng(seed, p, v, w), drill, t0=w * 3.2)))
    return out
