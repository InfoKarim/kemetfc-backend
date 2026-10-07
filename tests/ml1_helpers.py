"""Shared fixtures for the ML-1 tests. Everything here is synthetic or obviously fake; no real person, video or consent."""
import copy
import math
from datetime import datetime, timedelta

import numpy as np

from app.ml1 import consent as c
from app.ml1.config import ANGLES, KEYPOINTS, SCHEMA_VERSION
from app.ml1.synthetic import DRILLS, SYNTHETIC_SALT, make_arrays

SALT = b"test-only-salt-0123456789abcdef"
NOW = datetime(2026, 10, 8, 12, 0, 0)
HUMAN = c.coach_ref("coach-007", SALT)          # AC-...
HUMAN2 = c.coach_ref("coach-008", SALT)


def video_sha(video: str) -> str:
    """The fake registered video's checksum (what the platform would hold for the file)."""
    import hashlib
    return hashlib.sha256(f"fake-video-file:{video}".encode()).hexdigest()


def real_meta(player="player-1", video="vid-1", assessment=None, drill="SYNTH_DRILL_A", n=0, **overrides):
    """A REAL-style example record with valid consent. The ids are fake; used to test the gates, never to train for real."""
    meta = {
        "schema_version": SCHEMA_VERSION, "example_id": f"EX-{player}-{video}-{n:03d}",
        "anonymous_player_id": c.anonymous_player_id(player, SALT), "assessment_id": assessment or f"ASSESS-{video}", "video_id": c.video_ref(video, SALT),
        "drill_type": drill,
        "target": {"label": "TARGET-1", "source": "COACH_TAP", "identity_verified_by_coach": True, "epoch": 1},
        "window": {"start_frame": 0, "end_frame": 47, "start_s": 0.0, "end_s": 3.13, "frames": 48, "epoch": 1, "segment": 1},
        "ball_interaction": {"state": "NOT_AVAILABLE"},
        "labels": [{"label_type": "drill_type", "value": drill, "status": "COACH_REVIEWED", "source": "COACH", "reviewed_by_ref": HUMAN}],
        "model_confidence": {"mean_tracking_confidence": 0.9, "mean_keypoint_confidence": 0.85, "observed_fraction": 0.9},
        "consent": {"kind": c.GUARDIAN_CONSENT_KIND, "purpose": "ml_training", "consent_ref": c.consent_ref(f"consent-{player}", SALT),
                    "granted_at": (NOW - timedelta(days=30)).isoformat(), "expires_at": (NOW + timedelta(days=300)).isoformat(),
                    "withdrawn_at": None, "dataset_entry_status": "approved", "covers_all_visible_children": True,
                    "reviewed_by_ref": HUMAN},
        "provenance": {"source": "CAMERA_RECORDING", "recording_id": f"REC-{video}", "source_video_sha256": video_sha(video),
                       "pipeline_version": "a1.0.0", "third_party_content": False},
    }
    for key, value in overrides.items():
        meta[key] = value
    return meta


def real_pairs(n_players=10, videos=2):
    return [(f"player-{p}", f"vid-{p}-{v}") for p in range(n_players) for v in range(videos)]


def real_examples(n_players=10, videos=2, windows=6, seed=3):
    """The synthetic stick-figure arrays dressed with a real-style consented record (fake ids), for gate tests."""
    rng = np.random.RandomState(seed)
    out = []
    drills = sorted(DRILLS)
    for p in range(n_players):
        for v in range(videos):
            drill = drills[(p + v) % 3]
            for w in range(windows):
                meta = real_meta(player=f"player-{p}", video=f"vid-{p}-{v}", drill=drill, n=w)       # ids are fake; see ml1_registry_fixture
                meta["example_id"] = f"EX-{p:03d}-{v}-{w:02d}"
                out.append((meta, make_arrays(rng, drill, t0=w * 3.2)))
    return out


# ---- A1-shaped frames -------------------------------------------------------------------------------------------------------
def _landmarks():
    pts = {"nose": (540, 300), "left_shoulder": (600, 420), "right_shoulder": (480, 420), "left_elbow": (620, 560), "right_elbow": (460, 560),
           "left_wrist": (630, 690), "right_wrist": (450, 690), "left_hip": (590, 700), "right_hip": (490, 700), "left_knee": (595, 900),
           "right_knee": (485, 900), "left_ankle": (600, 1100), "right_ankle": (480, 1100)}
    return {name: {"x_px": float(x), "y_px": float(y), "x_norm": x / 1080, "y_norm": y / 1920, "visibility": 0.9, "presence": 0.9,
                   "usable": True, "unusable_reason": None} for name, (x, y) in pts.items()}


def a1_frame(i, *, state="LOCKED", segment=1, epoch=1, cut=False, fps=60.0, pose=True, drop=()):
    marks = _landmarks() if pose else None
    if marks:
        for name in drop:
            marks[name]["usable"] = False
        for k, m in marks.items():
            m["x_px"] += 3 * math.sin(i / 20.0)
    angles = {name: {"value": 150.0 if "ratio" not in name else 0.5, "unit": "ratio" if "ratio" in name else "degrees", "state": "MEASURED" if pose else "NOT_MEASURABLE"}
              for name in ANGLES}
    if not pose:
        for a in angles.values():
            a["value"] = None
    return {"frame_index": i, "timestamp_s": round(i / fps, 6),
            "identity": {"state": state, "segment": segment if state == "LOCKED" else None, "epoch": epoch, "picture_cut": cut},
            "target": {"tracking_confidence": 0.9} if state == "LOCKED" else None,
            "pose": {"landmarks": marks, "quality": {"status": "GOOD"}}, "angles": angles, "camera": {"camera_moving": False},
            "motion_proxy": {"body_center": {"x_frame_heights": 0.3 + 0.0005 * i, "y_frame_heights": 0.5, "source": "TARGET_BBOX_CENTER"}}}


def a1_run(n=1200, **kwargs):
    return [a1_frame(i, **kwargs) for i in range(n)]
