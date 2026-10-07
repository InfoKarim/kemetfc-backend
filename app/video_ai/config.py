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

# Target-player identity. No re-identification model exists: identity is a tracker id plus these guards.
IDENTITY = {
    "min_target_presence_seconds": 1.0,      # a track shorter than this can never be chosen as the target
    "overlap_iou": 0.30,                     # another person this much on the target -> OVERLAP_AMBIGUOUS
    "max_center_jump_frame_heights": 0.15,   # target centre moving further than this in one frame is implausible
    "reacquire_gap_frames": 3,               # a gap this long (or longer) means the target was lost
    "reacquire_probation_frames": 15,        # frames after a gap/cut that are REACQUIRED_UNVERIFIED
    "scene_cut_hist_correlation": 0.60,      # consecutive-frame colour correlation below this = a cut
    "appearance_break_hist_correlation": 0.50,  # target-box colour correlation between frames below this = a different picture
    "continuity_factor": {"CONFIRMED": 1.0, "REACQUIRED_UNVERIFIED": 0.5, "OVERLAP_AMBIGUOUS": 0.5, "LOST": 0.0},
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
