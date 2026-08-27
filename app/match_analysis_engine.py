import math


PLAYER_CLASSES = {"player", "goalkeeper"}


def _distance(first: list[float], second: list[float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _round(value: float) -> float:
    return round(float(value), 4)


class MatchTrackAccumulator:
    """Constant-memory aggregation for a streamed football match."""

    def __init__(
        self,
        possession_radius=0.09,
        high_speed_threshold=0.12,
        minimum_player_detection_rate=0.5,
        minimum_ball_detection_rate=0.1,
        minimum_mean_confidence=0.5,
        minimum_target_tracking_rate=0.25,
    ):
        if possession_radius <= 0 or high_speed_threshold <= 0:
            raise ValueError("Match thresholds must be greater than zero")
        quality_thresholds = {
            "minimum_player_detection_rate": minimum_player_detection_rate,
            "minimum_ball_detection_rate": minimum_ball_detection_rate,
            "minimum_mean_confidence": minimum_mean_confidence,
            "minimum_target_tracking_rate": minimum_target_tracking_rate,
        }
        if any(not 0 <= value <= 1 for value in quality_thresholds.values()):
            raise ValueError("Match quality thresholds must be between zero and one")
        self.possession_radius = possession_radius
        self.high_speed_threshold = high_speed_threshold
        self.quality_thresholds = quality_thresholds
        self.sampled_frames = 0
        self.frames_with_players = 0
        self.frames_with_ball = 0
        self.confidence_total = 0.0
        self.confidence_count = 0
        self.tracks = {}
        self.events = []
        self.previous_owner = None

    def add_frame(self, frame: dict) -> None:
        self.sampled_frames += 1
        detections = frame.get("detections") or []
        players = [
            item for item in detections
            if item.get("class_name") in PLAYER_CLASSES
            and isinstance(item.get("track_id"), int)
        ]
        balls = [item for item in detections if item.get("class_name") == "ball"]
        self.frames_with_players += bool(players)
        self.frames_with_ball += bool(balls)
        timestamp = float(frame["timestamp_seconds"])

        for player in players:
            track_id = player["track_id"]
            center = [float(value) for value in player["center"]]
            confidence = float(player["confidence"])
            state = self.tracks.setdefault(track_id, {
                "frames": 0, "confidence": 0.0, "distance": 0.0,
                "high_speed": 0, "controls": 0, "passes": 0,
                "received": 0, "last_time": None, "last_center": None,
            })
            if state["last_center"] is not None:
                displacement = _distance(state["last_center"], center)
                elapsed = timestamp - state["last_time"]
                state["distance"] += displacement
                if elapsed > 0 and displacement / elapsed >= self.high_speed_threshold:
                    state["high_speed"] += 1
            state["frames"] += 1
            state["confidence"] += confidence
            state["last_time"] = timestamp
            state["last_center"] = center
            self.confidence_total += confidence
            self.confidence_count += 1

        if not players or not balls:
            return
        ball = max(balls, key=lambda item: item["confidence"])
        self.confidence_total += float(ball["confidence"])
        self.confidence_count += 1
        owner = min(players, key=lambda item: _distance(item["center"], ball["center"]))
        if _distance(owner["center"], ball["center"]) > self.possession_radius:
            return

        sample = {
            "track_id": owner["track_id"],
            "timestamp_seconds": timestamp,
            "confidence": _round(min(owner["confidence"], ball["confidence"])),
        }
        self.tracks[sample["track_id"]]["controls"] += 1
        if self.previous_owner and sample["track_id"] != self.previous_owner["track_id"]:
            event = {
                "event_type": "pass_candidate",
                "timestamp_seconds": timestamp,
                "from_track_id": self.previous_owner["track_id"],
                "to_track_id": sample["track_id"],
                "confidence": _round(min(self.previous_owner["confidence"], sample["confidence"])),
                "review_required": True,
            }
            self.events.append(event)
            self.tracks[event["from_track_id"]]["passes"] += 1
            self.tracks[event["to_track_id"]]["received"] += 1
        self.previous_owner = sample

    def finalize(self, target_track_id: int | None = None) -> dict:
        if self.sampled_frames <= 0:
            raise ValueError("total_sampled_frames must be greater than zero")
        tracks = {}
        for track_id, state in self.tracks.items():
            tracks[track_id] = {
                "track_id": track_id,
                "frames_tracked": state["frames"],
                "tracking_rate": _round(state["frames"] / self.sampled_frames),
                "mean_detection_confidence": _round(state["confidence"] / state["frames"]),
                "normalized_distance": _round(state["distance"]),
                "high_speed_run_count": state["high_speed"],
                "ball_control_samples": state["controls"],
                "ball_involvement_rate": _round(state["controls"] / state["frames"]),
                "pass_candidates": state["passes"],
                "received_pass_candidates": state["received"],
                "pass_completion_rate": None,
                "units": {"distance": "normalized_image_diagonal"},
            }
        if target_track_id is not None and target_track_id not in tracks:
            raise ValueError(f"Target player track {target_track_id} was not found")
        player_rate = self.frames_with_players / self.sampled_frames
        ball_rate = self.frames_with_ball / self.sampled_frames
        mean_confidence = self.confidence_total / self.confidence_count if self.confidence_count else 0.0
        confidence = player_rate * 0.4 + ball_rate * 0.35 + mean_confidence * 0.25
        abstention_reasons = []
        if player_rate < self.quality_thresholds["minimum_player_detection_rate"]:
            abstention_reasons.append("player_detection_rate_below_threshold")
        if ball_rate < self.quality_thresholds["minimum_ball_detection_rate"]:
            abstention_reasons.append("ball_detection_rate_below_threshold")
        if mean_confidence < self.quality_thresholds["minimum_mean_confidence"]:
            abstention_reasons.append("mean_detection_confidence_below_threshold")
        target = tracks.get(target_track_id)
        if target_track_id is not None and target[
            "tracking_rate"
        ] < self.quality_thresholds["minimum_target_tracking_rate"]:
            abstention_reasons.append("target_tracking_rate_below_threshold")
        return {
            "summary": {
                "sampled_frames": self.sampled_frames,
                "players_tracked": len(tracks),
                "player_detection_rate": _round(player_rate),
                "ball_detection_rate": _round(ball_rate),
                "mean_detection_confidence": _round(mean_confidence),
                "analysis_confidence": _round(confidence),
                "confidence_semantics": "heuristic_unvalidated",
                "candidate_event_count": len(self.events),
            },
            "quality_control": {
                "abstained": bool(abstention_reasons),
                "score_generation_allowed": not abstention_reasons,
                "reasons": abstention_reasons,
                "thresholds": self.quality_thresholds,
                "message": (
                    "Automated scoring withheld; review the video and model outputs."
                    if abstention_reasons
                    else "Automated scoring may proceed, subject to human review."
                ),
            },
            "events": self.events,
            "player_tracks": list(tracks.values()),
            "target_player": target,
            "limitations": [
                "Events are candidates and require human review.",
                "Image-plane distance is not physical distance without calibration.",
                "Team identity and tactical position require validated team labels.",
            ],
        }


def analyze_match_tracks(frames, total_sampled_frames, target_track_id=None,
                         possession_radius=0.09, high_speed_threshold=0.12,
                         **quality_thresholds):
    if total_sampled_frames <= 0:
        raise ValueError("total_sampled_frames must be greater than zero")
    if len(frames) != total_sampled_frames:
        raise ValueError("total_sampled_frames must match the supplied frames")
    accumulator = MatchTrackAccumulator(
        possession_radius,
        high_speed_threshold,
        **quality_thresholds,
    )
    for frame in frames:
        accumulator.add_frame(frame)
    return accumulator.finalize(target_track_id)
