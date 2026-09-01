import math
from statistics import fmean, pstdev


MIN_LANDMARK_CONFIDENCE = 0.5

LANDMARK_INDEX = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
    "left_foot_index": 31,
    "right_foot_index": 32,
}

JOINT_ANGLE_DEFINITIONS = {
    "left_knee_angle_degrees": ("left_hip", "left_knee", "left_ankle"),
    "right_knee_angle_degrees": ("right_hip", "right_knee", "right_ankle"),
    "left_hip_angle_degrees": ("left_shoulder", "left_hip", "left_knee"),
    "right_hip_angle_degrees": ("right_shoulder", "right_hip", "right_knee"),
    "left_elbow_angle_degrees": (
        "left_shoulder",
        "left_elbow",
        "left_wrist",
    ),
    "right_elbow_angle_degrees": (
        "right_shoulder",
        "right_elbow",
        "right_wrist",
    ),
    "left_ankle_angle_degrees": (
        "left_knee",
        "left_ankle",
        "left_foot_index",
    ),
    "right_ankle_angle_degrees": (
        "right_knee",
        "right_ankle",
        "right_foot_index",
    ),
}


def _is_reliable(landmark: dict) -> bool:
    return (
        landmark.get("visibility", 0.0) >= MIN_LANDMARK_CONFIDENCE
        and landmark.get("presence", 0.0) >= MIN_LANDMARK_CONFIDENCE
    )


def _point(
    landmarks: list[dict],
    name: str,
    image_width: int,
    image_height: int,
) -> tuple[float, float, float] | None:
    index = LANDMARK_INDEX[name]

    if index >= len(landmarks):
        return None

    landmark = landmarks[index]

    if not _is_reliable(landmark):
        return None

    return (
        landmark["x"] * image_width,
        landmark["y"] * image_height,
        landmark["z"] * image_width,
    )


def _midpoint(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple((a + b) / 2.0 for a, b in zip(first, second))


def _distance(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def _angle(
    first: tuple[float, float, float],
    vertex: tuple[float, float, float],
    third: tuple[float, float, float],
) -> float | None:
    first_vector = tuple(a - b for a, b in zip(first, vertex))
    second_vector = tuple(a - b for a, b in zip(third, vertex))
    first_length = math.sqrt(sum(value**2 for value in first_vector))
    second_length = math.sqrt(sum(value**2 for value in second_vector))

    if first_length == 0.0 or second_length == 0.0:
        return None

    cosine = sum(
        a * b for a, b in zip(first_vector, second_vector)
    ) / (first_length * second_length)
    cosine = min(max(cosine, -1.0), 1.0)
    return math.degrees(math.acos(cosine))


def _line_tilt(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    angle = math.degrees(
        math.atan2(right[1] - left[1], right[0] - left[0])
    )
    return ((angle + 90.0) % 180.0) - 90.0


def _trunk_lean(
    shoulder_midpoint: tuple[float, float, float],
    hip_midpoint: tuple[float, float, float],
) -> float | None:
    torso = tuple(
        shoulder - hip
        for shoulder, hip in zip(shoulder_midpoint, hip_midpoint)
    )
    torso_length = math.sqrt(sum(value**2 for value in torso))

    if torso_length == 0.0:
        return None

    vertical = (0.0, -1.0, 0.0)
    cosine = sum(a * b for a, b in zip(torso, vertical)) / torso_length
    cosine = min(max(cosine, -1.0), 1.0)
    return math.degrees(math.acos(cosine))


def calculate_frame_features(
    landmarks: list[dict],
    image_width: int,
    image_height: int,
) -> dict[str, float]:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be greater than 0")

    points = {
        name: _point(landmarks, name, image_width, image_height)
        for name in LANDMARK_INDEX
    }
    features: dict[str, float] = {}

    for feature_name, point_names in JOINT_ANGLE_DEFINITIONS.items():
        angle_points = [points[name] for name in point_names]

        if all(point is not None for point in angle_points):
            value = _angle(*angle_points)

            if value is not None:
                features[feature_name] = value

    left_shoulder = points["left_shoulder"]
    right_shoulder = points["right_shoulder"]
    left_hip = points["left_hip"]
    right_hip = points["right_hip"]
    left_ankle = points["left_ankle"]
    right_ankle = points["right_ankle"]

    if left_shoulder is not None and right_shoulder is not None:
        features["shoulder_tilt_degrees"] = _line_tilt(
            left_shoulder,
            right_shoulder,
        )

    if left_hip is not None and right_hip is not None:
        features["hip_tilt_degrees"] = _line_tilt(left_hip, right_hip)
        features["hip_center_x_normalized"] = (
            (left_hip[0] + right_hip[0]) / 2.0 / image_width
        )
        features["hip_center_y_normalized"] = (
            (left_hip[1] + right_hip[1]) / 2.0 / image_height
        )

    if left_ankle is not None and right_ankle is not None:
        features["ankle_center_y_normalized"] = (
            (left_ankle[1] + right_ankle[1]) / 2.0 / image_height
        )

    nose = points["nose"]
    ankle_points = [
        point for point in (left_ankle, right_ankle) if point is not None
    ]

    if nose is not None and ankle_points:
        ankle_center_x = sum(point[0] for point in ankle_points) / len(
            ankle_points
        )
        ankle_center_y = sum(point[1] for point in ankle_points) / len(
            ankle_points
        )
        # 2D-only (ignores MediaPipe's z, a rough relative depth, not a
        # pixel-comparable unit) distance from nose to ankle(s) — a
        # per-frame proxy for the player's standing height in pixels,
        # used elsewhere to convert pixel movement into real-world speed.
        features["body_height_pixels"] = (
            (nose[0] - ankle_center_x) ** 2
            + (nose[1] - ankle_center_y) ** 2
        ) ** 0.5

    if all(
        point is not None
        for point in (
            left_shoulder,
            right_shoulder,
            left_hip,
            right_hip,
        )
    ):
        shoulder_midpoint = _midpoint(left_shoulder, right_shoulder)
        hip_midpoint = _midpoint(left_hip, right_hip)
        torso_length = _distance(shoulder_midpoint, hip_midpoint)
        value = _trunk_lean(shoulder_midpoint, hip_midpoint)

        if value is not None:
            features["trunk_lean_degrees"] = value

        if torso_length > 0.0:
            features["torso_length_pixels"] = torso_length

            for side in ("left", "right"):
                hip = points[f"{side}_hip"]
                knee = points[f"{side}_knee"]
                ankle = points[f"{side}_ankle"]

                if knee is not None:
                    features[
                        f"{side}_knee_hip_vertical_ratio"
                    ] = (knee[1] - hip[1]) / torso_length

                if ankle is not None:
                    features[
                        f"{side}_ankle_hip_vertical_ratio"
                    ] = (ankle[1] - hip[1]) / torso_length

    if all(
        point is not None
        for point in (
            left_shoulder,
            right_shoulder,
            left_ankle,
            right_ankle,
        )
    ):
        shoulder_width = _distance(left_shoulder, right_shoulder)

        if shoulder_width > 0.0:
            features["stance_width_shoulder_ratio"] = (
                _distance(left_ankle, right_ankle) / shoulder_width
            )

    if (
        "left_knee_angle_degrees" in features
        and "right_knee_angle_degrees" in features
    ):
        features["knee_angle_asymmetry_degrees"] = abs(
            features["left_knee_angle_degrees"]
            - features["right_knee_angle_degrees"]
        )

    if (
        "left_hip_angle_degrees" in features
        and "right_hip_angle_degrees" in features
    ):
        features["hip_angle_asymmetry_degrees"] = abs(
            features["left_hip_angle_degrees"]
            - features["right_hip_angle_degrees"]
        )

    return features


def _point_world(
    landmarks: list[dict],
    name: str,
) -> tuple[float, float, float] | None:
    index = LANDMARK_INDEX[name]

    if index >= len(landmarks):
        return None

    landmark = landmarks[index]

    if not _is_reliable(landmark):
        return None

    return (landmark["x"], landmark["y"], landmark["z"])


def calculate_frame_joint_angles_3d(
    world_landmarks: list[dict],
) -> dict[str, float]:
    """Joint angles computed from MediaPipe's real-world (metric,
    hip-centered) landmarks, rather than the 2D image-normalized ones.
    Unlike calculate_frame_features, no image_width/height scaling is
    needed since world landmarks are already in meters."""
    points = {
        name: _point_world(world_landmarks, name)
        for name in LANDMARK_INDEX
    }
    angles: dict[str, float] = {}

    for feature_name, point_names in JOINT_ANGLE_DEFINITIONS.items():
        angle_points = [points[name] for name in point_names]

        if all(point is not None for point in angle_points):
            value = _angle(*angle_points)

            if value is not None:
                angles[feature_name] = value

    return angles


def _summarize(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    return {
        "count": len(values),
        "mean": fmean(values),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "median": median,
        "standard_deviation": pstdev(values),
    }


def extract_pose_features(
    landmark_frames: list[dict],
    image_width: int,
    image_height: int,
) -> dict:
    frame_features = []
    values_by_feature: dict[str, list[float]] = {}

    for frame in landmark_frames:
        features = calculate_frame_features(
            landmarks=frame.get("landmarks", []),
            image_width=image_width,
            image_height=image_height,
        )

        if not features:
            continue

        frame_entry = {
            "frame_index": frame["frame_index"],
            "timestamp_ms": frame["timestamp_ms"],
            "measurements": features,
        }

        world_landmarks = frame.get("world_landmarks")

        if world_landmarks:
            frame_entry["joint_angles_3d"] = calculate_frame_joint_angles_3d(
                world_landmarks
            )

        frame_features.append(frame_entry)

        for name, value in features.items():
            values_by_feature.setdefault(name, []).append(value)

    return {
        "schema_version": "1.0",
        "coordinate_space": "pixel-scaled normalized landmarks",
        "minimum_landmark_confidence": MIN_LANDMARK_CONFIDENCE,
        "frames_with_features": len(frame_features),
        "feature_coverage": (
            len(frame_features) / len(landmark_frames)
            if landmark_frames
            else 0.0
        ),
        "summary": {
            name: _summarize(values)
            for name, values in sorted(values_by_feature.items())
        },
        "frames": frame_features,
    }
