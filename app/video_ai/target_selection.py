"""Choosing WHO in the video is the player: the coach's tap, resolved against the detected people.

Roster selection says whose assessment this is. It does not say which visible person is that player. The coach
says that, by tapping the person on a frame of the video. Nothing here recognises a face or identifies a child:
a tap is a point, a detection is a box, and the rule below turns one into the other or refuses.

Coordinates are normalized (0..1) in the UPRIGHT video frame, origin top-left, exactly as the iPhone screen
mapping produces them.
"""
from dataclasses import dataclass, field

from app.video_ai.config import IDENTITY

SOURCE_COACH_TAP = "COACH_TAP"
SOURCE_SYNTHETIC_TEST_TAP = "SYNTHETIC_TEST_TAP"
SOURCE_DIAGNOSTIC_MOST_PROMINENT = "DIAGNOSTIC_MOST_PROMINENT"
IDENTITY_VERIFIED_SOURCES = (SOURCE_COACH_TAP,)

NO_PLAYER_AT_TAP_MESSAGE = "No player detected at that point. Tap directly on the player."


@dataclass(frozen=True)
class TargetSelection:
    """One explicit choice of the person to follow. `label` is a temporary tracking identity for this video; it
    is never a roster player id."""
    label: str
    source: str
    frame_index: int
    timestamp_s: float
    tap_norm: tuple[float, float] | None = None
    bbox_norm: tuple[float, float, float, float] | None = None       # the person box the tap resolved to
    detection_confidence: float | None = None
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def identity_verified_by_coach(self) -> bool:
        return self.source in IDENTITY_VERIFIED_SOURCES

    def as_json(self) -> dict:
        return {"label": self.label, "source": self.source, "frame_index": self.frame_index,
                "timestamp_s": self.timestamp_s, "tap_norm": self.tap_norm, "bbox_norm": self.bbox_norm,
                "detection_confidence": self.detection_confidence, "note": self.note,
                "identity_verified_by_coach": self.identity_verified_by_coach}


def contains(box, point) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def resolve_tap(detections: list[dict], tap_norm: tuple[float, float]) -> tuple[int | None, str | None]:
    """Index of the detection the tap is on, or (None, message). Never the nearest person: a tap that is not on
    anyone selects no one.

    When several boxes contain the tap (people overlapping), the rule, in order:
      1. the box whose centre is closest to the tap, measured in that box's own size (so a tap on a small child
         in front of a tall adult picks the child, not the adult whose box is merely larger);
      2. then the smaller box;
      3. then the higher detection confidence;
      4. then the lower detection index (the detector's own order).
    """
    if not (0.0 <= tap_norm[0] <= 1.0 and 0.0 <= tap_norm[1] <= 1.0):
        return None, NO_PLAYER_AT_TAP_MESSAGE
    hits = [(i, d) for i, d in enumerate(detections) if contains(d["bbox_norm"], tap_norm)]
    if not hits:
        return None, NO_PLAYER_AT_TAP_MESSAGE

    def key(item):
        i, d = item
        x1, y1, x2, y2 = d["bbox_norm"]
        w, h = max(x2 - x1, 1e-9), max(y2 - y1, 1e-9)
        offset = (((tap_norm[0] - (x1 + x2) / 2) / w) ** 2 + ((tap_norm[1] - (y1 + y2) / 2) / h) ** 2) ** 0.5
        return (round(offset, 9), round(w * h, 9), -d.get("confidence", 0.0), i)

    return min(hits, key=key)[0], None


def make_selection(detections: list[dict], tap_norm: tuple[float, float], *, frame_index: int, timestamp_s: float,
                   label: str, source: str, note: str = "") -> tuple[TargetSelection | None, int | None, str | None]:
    index, message = resolve_tap(detections, tap_norm)
    if index is None:
        return None, None, message
    box = tuple(detections[index]["bbox_norm"])
    return TargetSelection(label=label, source=source, frame_index=frame_index, timestamp_s=timestamp_s,
                           tap_norm=tuple(tap_norm), bbox_norm=box,
                           detection_confidence=detections[index].get("confidence"), note=note), index, None


def suggest_frames(frames: list[dict], fps: float, count: int = 5) -> list[dict]:
    """Frames worth showing the coach, best first, spread out in time. Each frame dict carries:
    detections [{bbox_norm, confidence}], sharpness, brightness, near_cut (bool). A frame qualifies only if it
    has people, every shown detection is plausible, it is sharp enough, not dark or a transition, and not within
    a moment of a picture cut. Frame 0 gets no special treatment."""
    guard = IDENTITY
    sharp = sorted(f["sharpness"] for f in frames if f.get("sharpness") is not None)
    median_sharp = sharp[len(sharp) // 2] if sharp else 0.0
    scored = []
    for index, f in enumerate(frames):
        people = [d for d in f["detections"]
                  if d["confidence"] >= guard["suggest_min_confidence"]
                  and (d["bbox_norm"][3] - d["bbox_norm"][1]) >= guard["suggest_min_box_height"]]
        if len(people) < guard["suggest_min_people"] or f.get("near_cut"):
            continue
        if f.get("brightness") is not None and f["brightness"] < guard["suggest_min_brightness"]:
            continue
        if f.get("sharpness") is not None and median_sharp > 0 and f["sharpness"] < guard["suggest_min_sharpness_ratio"] * median_sharp:
            continue
        mean_conf = sum(d["confidence"] for d in people) / len(people)
        sharp_part = (f["sharpness"] / median_sharp) if (f.get("sharpness") is not None and median_sharp > 0) else 1.0
        scored.append((mean_conf * min(sharp_part, 2.0) * min(len(people), 3), -index, index))
    scored.sort(reverse=True)
    chosen: list[int] = []
    for _, _, index in scored:
        if all(abs(index - c) >= max(int(fps * 2.0), 1) for c in chosen):
            chosen.append(index)
        if len(chosen) >= count:
            break
    return [{"frame_index": i, "people": len([d for d in frames[i]["detections"] if d["confidence"] >= guard["suggest_min_confidence"]])}
            for i in sorted(chosen)]
