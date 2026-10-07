"""Versions and every threshold of the A1 analysis, in one place so each is written into the artifact."""

SCHEMA_VERSION = "kemet.video_ai.a1/1"
PIPELINE_VERSION = "a1.0.0"
ANGLE_DEFINITION_VERSION = "angles_2d_v1"
PROXY_DEFINITION_VERSION = "motion_proxy_v1"

# Person detection / tracking (Ultralytics YOLO + ByteTrack, the same stack full_match_analyzer uses).
DETECTION = {
    "confidence_threshold": 0.25,
    "iou_threshold": 0.7,
    "image_size": 960,
    "tracker": "bytetrack.yaml",
    "device": "cpu",
}

# Target-player identity (A1.5). The coach taps the player; this is how that selection is followed. There is no
# face recognition and no biometric identity: only where a person is, how they move and what they look like in
# colour. Anything that cannot be proven stays UNCERTAIN, LOST or RESELECTION_REQUIRED; it is never guessed.
IDENTITY = {
    "diagnostic_min_presence_seconds": 1.0,  # the DIAGNOSTIC most-prominent fallback only considers tracks at least this long
    "overlap_iou": 0.25,                    # another person this much on the target -> UNCERTAIN
    "gate_base_frame_heights": 0.10,        # how far (in frame heights) the target may move between two frames ...
    "gate_per_missing_frame": 0.02,         # ... plus this much for every frame it was not seen
    "gate_max_frame_heights": 0.60,
    "size_ratio_range": (0.5, 2.0),         # candidate box height / last target box height
    "appearance_accept_min": 0.55,          # colour correlation needed to keep following the target
    "appearance_recover_min": 0.85,         # ... and to take it back after UNCERTAIN / LOST
    "score_margin": 0.12,                   # best candidate must beat the runner-up by this much, else ambiguous
    "template_update_min": 0.70,            # the colour reference is refreshed only from confident, unambiguous frames
    "template_update_rate": 0.05,
    "brief_miss_seconds": 0.5,              # not seen for less than this is UNCERTAIN (and may be taken back); longer is LOST, which never recovers by itself
    "uncertain_max_seconds": 1.0,           # UNCERTAIN longer than this -> RESELECTION_REQUIRED
    "recover_max_seconds": 1.5,             # a target gone longer than this is never taken back automatically
    "recover_gate_max_frame_heights": 0.25, # ... and only if they reappear this close to where they were last seen
    "edge_margin": 0.02,                    # a target last seen touching the frame edge left the frame: no auto re-entry
    "scene_cut_hist_correlation": 0.75,     # consecutive-frame colour correlation below this = a picture cut (calibrated on one reel: cuts 0.41-0.71, normal frames >= 0.85)
    "appearance_break_hist_correlation": 0.50,
    "continuity_factor": {"LOCKED": 1.0, "UNCERTAIN": 0.5, "LOST": 0.0, "RESELECTION_REQUIRED": 0.0},
    # frame suggestion for SELECT PLAYER IN VIDEO
    "suggest_min_people": 1,
    "suggest_min_confidence": 0.5,
    "suggest_min_box_height": 0.12,         # of frame height: a person this small is not a useful target
    "suggest_min_sharpness_ratio": 0.5,     # of the clip's median sharpness
    "suggest_min_brightness": 25.0,
    "suggest_clear_of_cut_seconds": 0.4,
}

# Pose.
POSE = {
    "crop_margin": 0.15,                 # target box grown by this fraction on each side before pose
    "min_visibility": 0.5,               # landmark usable only at/above this visibility AND presence
    "min_presence": 0.5,
    "bbox_slack": 0.10,                  # usable landmarks must lie inside the target box grown by this much
    "hidden_visibility": 0.30,           # below this the model itself says the joint is hidden
    "good_min_usable_core": 11,          # of the 12 core joints
    "good_min_mean_visibility": 0.60,
    "partial_min_usable_core": 6,
    "occluded_min_hidden_core": 3,
    # Anatomical plausibility (image plane, upright camera). A pose that breaks these is rejected, not measured.
    "orientation_tolerance_box_fraction": 0.02,  # a lower body part may sit this much (of box height) above the one before it
    "min_box_height_over_width": 1.0,    # an upright person's box is taller than wide; wider means not an upright body
    "min_segment_ratio": 0.25,           # thigh / shin length over torso length must stay inside this ...
    "max_segment_ratio": 2.5,            # ... and this range (2D foreshortening allowed for)
    "ankle_min_box_fraction": 0.55,      # an ankle must lie in the lower part of the target box (0 = top, 1 = bottom)
    "shoulder_max_box_fraction": 0.65,   # a shoulder must lie in the upper part of the target box
    "min_pose_detection_confidence": 0.5,
    "min_pose_presence_confidence": 0.5,
}

# Proxies.
PROXY = {
    "smoothing_window_frames": 9,        # Savitzky-Golay, order 2, inside one identity segment only
    "min_segment_frames": 9,
    "cadence_window_seconds": 4.0,
    "cadence_hop_seconds": 2.0,
    "cadence_min_usable_fraction": 0.9,  # of the window's frames must have a usable knee angle
    "cadence_band_hz": (0.5, 4.0),
    "cadence_min_peak_fraction": 0.60,   # share of band power within +-1/window Hz of the strongest peak, else NO_PERIODICITY
    "camera_motion_flag_norm": 0.003,   # global image shift per frame (fraction of frame size) above which the camera is moving
}
