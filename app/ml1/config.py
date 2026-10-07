"""ML-1 settings in one place; every value that shapes a dataset or a model is written into its manifest."""

SCHEMA_VERSION = "kemet.ml1.pose_sequence/1"
DATASET_FORMAT = "kemet.ml1.dataset/1"
MODEL_ARCHITECTURE = "kemet.ml1.pose_tcn/1"

# The joints the network sees: the nose and the 12 core joints (A1 landmark names).
KEYPOINTS = ["nose", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
             "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"]
# The A1 2D body measurements, in a fixed order (definitions: app/video_ai/geometry.py).
ANGLES = ["left_knee_angle_2d", "right_knee_angle_2d", "left_hip_angle_2d", "right_hip_angle_2d",
          "left_elbow_angle_2d", "right_elbow_angle_2d", "trunk_lean_2d", "shoulder_line_angle_2d",
          "hip_line_angle_2d", "apparent_shoulder_width_ratio"]
ANGLE_SCALE = {"degrees": 180.0, "ratio": 1.0}
# Per-frame movement features (all image-plane; VIDEO_MOTION_PROXY units).
MOTION = ["center_vx_frame_heights_per_s", "center_vy_frame_heights_per_s", "tracking_confidence", "camera_moving"]

WINDOW = {
    "target_hz": 15.0,            # frames are taken at this rate by stride; keypoints are never interpolated
    "length_frames": 48,          # 3.2 s
    "hop_frames": 24,
    "min_observed_fraction": 0.5, # a window needs at least this share of frames with a usable pose
    "max_timestamp_gap_s": 0.25,  # between consecutive sampled frames of a window
}

CONSENT_PURPOSE = "ml_training"
SYNTHETIC_SOURCE = "SYNTHETIC_FIXTURE"
REAL_SOURCES = ("CAMERA_RECORDING", "IMPORTED_VIDEO")
SOURCES = REAL_SOURCES + (SYNTHETIC_SOURCE,)

SPLITS = {"train": 0.6, "val": 0.2, "test": 0.2}
MIN_GROUPS_PER_SPLIT = 1          # independent groups (player+video components) each split must receive
MIN_GROUPS_TOTAL = 5

# Supervised heads need real reviewed labels. Below this there is no classification, only embeddings.
LABELS = {"min_examples_per_class": 30, "min_players_per_class": 3, "min_classes": 2}

TRAINING = {"seed": 1337, "epochs": 25, "batch_size": 16, "learning_rate": 2e-3, "weight_decay": 1e-4,
            "embedding_dim": 32, "channels": 48, "hide_fraction": 0.25}

# A candidate can be approved only if it clears these on the held-out TEST split, using data that is not synthetic.
APPROVAL_GATES = {"min_test_examples": 50, "min_reconstruction_improvement_over_baseline": 0.10,
                  "min_macro_f1_if_classifier": 0.60}
