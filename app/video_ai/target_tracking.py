"""Following the coach-selected person, and refusing to guess.

Input: for every frame, the detected people (box, confidence, a colour descriptor), the picture cuts, and the
coach's selection(s). Output: for every frame, exactly one identity state and the evidence for it.

States
  LOCKED                the selected person is followed with enough evidence; player-specific analysis allowed
  UNCERTAIN             a short doubt (detector miss, someone overlapping, two equally likely candidates);
                        analysis is blocked, nothing is adopted
  LOST                  the person is not visible; analysis is blocked
  RESELECTION_REQUIRED  continuity cannot be proven (picture cut, long doubt, a lookalike nearby, a long
                        absence); the coach must tap the player again. Nothing recovers from this by itself.

Rules that do not bend
  * The target is whoever the coach tapped. Never the biggest, nearest or most confident person.
  * After a loss, a new person is never adopted just because they are near, large or look similar. Taking a
    person back needs strong colour agreement, a clear margin over every other candidate, no overlap, and a
    short absence; otherwise the state is RESELECTION_REQUIRED.
  * A picture cut ends continuity at the cut. Two different children on either side of a cut are never joined.
  * People already seen next to the target are followed too, so when the target vanishes they are not
    mistaken for it.
  * No face recognition, no biometric identity: position, size and clothing colour only. Colour cannot tell two
    children in the same kit apart, so a same-kit neighbour makes the state ambiguous (UNCERTAIN, then
    RESELECTION_REQUIRED), never a guess.

Measurements may use only runs of consecutive LOCKED frames inside one epoch (one explicit selection).
"""
import math

from app.video_ai.config import IDENTITY

LOCKED = "LOCKED"
UNCERTAIN = "UNCERTAIN"
LOST = "LOST"
RESELECTION_REQUIRED = "RESELECTION_REQUIRED"
NOT_SELECTED = "NOT_SELECTED"
STATES = (LOCKED, UNCERTAIN, LOST, RESELECTION_REQUIRED)
ANALYSIS_ALLOWED_STATES = (LOCKED,)

OTHER_GATE = 0.12          # how far (frame heights) a known neighbour may move between two frames
OTHER_MAX_AGE_SECONDS = 1.0
LOOKALIKE_RADIUS = 0.35   # frame heights: how close a lookalike must be to make a recovery unprovable


def similarity(a, b) -> float:
    """Pearson correlation of two colour descriptors, -1..1; 0 when either is missing or flat."""
    if a is None or b is None or len(a) != len(b) or len(a) == 0:
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    da, db = [x - ma for x in a], [x - mb for x in b]
    na, nb = math.sqrt(sum(x * x for x in da)), math.sqrt(sum(x * x for x in db))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return sum(x * y for x, y in zip(da, db)) / (na * nb)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _distance(a, b, aspect):
    (ax, ay), (bx, by) = _center(a), _center(b)
    return math.hypot((ax - bx) * aspect, ay - by)


def _height(box):
    return box[3] - box[1]


def _record(state, reason, target=None, similarity_value=None, candidates=0, last_known=None):
    return {"state": state, "reason": reason, "target": target, "appearance_similarity": similarity_value,
            "candidates": candidates, "last_known_box_norm": last_known}


def follow(frames: list[list[dict]], cuts: list[bool], order: list[int], start_detection: int, fps: float,
           aspect: float) -> dict[int, dict]:
    """Follow one selection through `order` (frame indices; order[0] is the frame where the coach tapped and
    `start_detection` is the index of the tapped person in that frame). Returns frame index -> record.
    frames[i] is a list of {bbox_norm, confidence, appearance}. cuts[i] is True when frame i starts a new shot."""
    guard = IDENTITY
    first = order[0]
    target0 = frames[first][start_detection]
    template = list(target0["appearance"]) if target0.get("appearance") is not None else None
    last_box = tuple(target0["bbox_norm"])
    status = LOCKED
    last_seen = 0
    uncertain_since = None
    others: list[dict] = [{"box": tuple(d["bbox_norm"]), "seen": 0} for k, d in enumerate(frames[first]) if k != start_detection]
    recent_lookalike = False          # in the last LOCKED frame, someone near the target looked like the target
    episode_lookalike = False         # ... at any time while the target was in doubt
    in_doubt = False
    out = {first: _record(LOCKED, "COACH_SELECTED", target0, 1.0, 1, last_box)}
    brief = guard["brief_miss_seconds"] * fps
    uncertain_max = guard["uncertain_max_seconds"] * fps
    recover_max = guard["recover_max_seconds"] * fps
    other_age = OTHER_MAX_AGE_SECONDS * fps

    for pos in range(1, len(order)):
        idx, previous = order[pos], order[pos - 1]
        detections = frames[idx]
        if status == RESELECTION_REQUIRED:
            out[idx] = _record(RESELECTION_REQUIRED, "COACH_MUST_SELECT_AGAIN", None, None, 0, last_box)
            continue
        if cuts[max(idx, previous)]:
            status = RESELECTION_REQUIRED
            out[idx] = _record(RESELECTION_REQUIRED, "SCENE_CUT", None, None, 0, last_box)
            continue

        missing = pos - last_seen
        gate = min(guard["gate_max_frame_heights"], guard["gate_base_frame_heights"] + guard["gate_per_missing_frame"] * (missing - 1))

        # follow the people who were next to the target, so they can be told apart from it
        explained: set[int] = set()
        taken: set[int] = set()
        for other in sorted(others, key=lambda o: o["seen"], reverse=True):
            best, best_d = None, None
            for k, d in enumerate(detections):
                if k in taken:
                    continue
                dist = _distance(other["box"], d["bbox_norm"], aspect)
                if dist <= OTHER_GATE and (best_d is None or dist < best_d):
                    best, best_d = k, dist
            if best is not None:
                # a person who is clearly nearer to this neighbour than to where the target was is the neighbour
                if best_d < _distance(last_box, detections[best]["bbox_norm"], aspect) or best_d <= 0.02:
                    explained.add(best)
                    taken.add(best)
                    other["box"], other["seen"] = tuple(detections[best]["bbox_norm"]), pos
        others = [o for o in others if pos - o["seen"] <= other_age]

        needs_strong = status != LOCKED
        if needs_strong:
            gate = min(gate, guard["recover_gate_max_frame_heights"])
        floor = guard["appearance_recover_min"] if needs_strong else guard["appearance_accept_min"]
        candidates = []
        for k, d in enumerate(detections):
            if k in explained:
                continue
            dist = _distance(last_box, d["bbox_norm"], aspect)
            ratio = _height(d["bbox_norm"]) / max(_height(last_box), 1e-9)
            low, high = guard["size_ratio_range"]
            if dist > gate or not (low <= ratio <= high):
                continue
            sim = similarity(d.get("appearance"), template)
            if sim < floor:
                continue
            score = 0.7 * sim + 0.3 * max(0.0, 1.0 - dist / max(gate, 1e-9))
            candidates.append((score, k, sim, dist))
        candidates.sort(reverse=True)

        # someone near the target, other than the person being followed, who looks like the target (same clothing
        # colours): colour cannot tell them apart, so while the target is in doubt taking it back is unprovable
        chosen_index = candidates[0][1] if candidates else None
        lookalike_here = template is not None and any(
            k != chosen_index and _distance(last_box, d["bbox_norm"], aspect) <= LOOKALIKE_RADIUS
            and similarity(d.get("appearance"), template) >= guard["appearance_accept_min"]
            for k, d in enumerate(detections))
        if status != LOCKED:
            episode_lookalike = episode_lookalike or (not in_doubt and recent_lookalike) or lookalike_here
            in_doubt = True

        if not candidates:
            gone = missing / fps
            if status == LOST or missing > brief:
                status, reason = LOST, "TARGET_NOT_VISIBLE"
            else:
                status, reason = UNCERTAIN, "TARGET_NOT_DETECTED_BRIEFLY"
                uncertain_since = pos if uncertain_since is None else uncertain_since
            out[idx] = _record(status, reason, None, None, 0, last_box)
            continue

        best = candidates[0]
        chosen = detections[best[1]]
        ambiguous = len(candidates) > 1 and best[0] - candidates[1][0] < guard["score_margin"]
        overlapped = any(k != best[1] and iou(d["bbox_norm"], chosen["bbox_norm"]) >= guard["overlap_iou"]
                         for k, d in enumerate(detections))

        if status in (UNCERTAIN, LOST):
            margin = guard["edge_margin"]
            left_the_frame = (last_box[0] <= margin or last_box[1] <= margin or last_box[2] >= 1 - margin or last_box[3] >= 1 - margin)
            if status == LOST or missing > recover_max or left_the_frame:
                # someone is here after the target was gone too long, or after it walked out of the picture:
                # whether this is the same person cannot be proven, so the coach is asked, never the code
                status = RESELECTION_REQUIRED
                why = ("TARGET_LEFT_THE_FRAME_RETURN_NOT_PROVEN" if left_the_frame else
                       "CONTINUITY_NOT_PROVEN_AFTER_LONG_ABSENCE" if missing > recover_max else "TARGET_RETURN_NOT_PROVEN_AFTER_LOSS")
                out[idx] = _record(RESELECTION_REQUIRED, why, None, best[2], len(candidates), last_box)
                continue
            if ambiguous or overlapped:
                status = UNCERTAIN
                uncertain_since = pos if uncertain_since is None else uncertain_since
                if pos - uncertain_since > uncertain_max:
                    status = RESELECTION_REQUIRED
                    out[idx] = _record(RESELECTION_REQUIRED, "AMBIGUOUS_FOR_TOO_LONG", None, best[2], len(candidates), last_box)
                else:
                    out[idx] = _record(UNCERTAIN, "AMBIGUOUS_CANDIDATES" if ambiguous else "ANOTHER_PERSON_OVERLAPS", None, best[2], len(candidates), last_box)
                continue
            if episode_lookalike:
                status = RESELECTION_REQUIRED
                out[idx] = _record(RESELECTION_REQUIRED, "LOOKALIKE_NEARBY_IDENTITY_NOT_PROVEN", None, best[2], len(candidates), last_box)
                continue
            status, uncertain_since = LOCKED, None
            in_doubt, episode_lookalike = False, False
            reason = "RECOVERED"
        else:
            if ambiguous or overlapped:
                status = UNCERTAIN
                uncertain_since = pos if uncertain_since is None else uncertain_since
                out[idx] = _record(UNCERTAIN, "AMBIGUOUS_CANDIDATES" if ambiguous else "ANOTHER_PERSON_OVERLAPS", None, best[2], len(candidates), last_box)
                continue
            reason = None
            uncertain_since = None

        recent_lookalike = lookalike_here
        last_box, last_seen = tuple(chosen["bbox_norm"]), pos
        if template is not None and chosen.get("appearance") is not None and best[2] >= guard["template_update_min"]:
            rate = guard["template_update_rate"]
            template = [(1 - rate) * t + rate * a for t, a in zip(template, chosen["appearance"])]
        # neighbours are whoever else is on screen right now
        others = [o for o in others if o["seen"] == pos] + [
            {"box": tuple(d["bbox_norm"]), "seen": pos} for k, d in enumerate(detections)
            if k != best[1] and k not in explained]
        out[idx] = _record(LOCKED, reason, chosen, best[2], len(candidates), last_box)
    return out


def track_selected(frames: list[list[dict]], cuts: list[bool], selections: list[tuple[int, int]], fps: float,
                   aspect: float, backward: bool = True) -> list[dict]:
    """selections: [(frame_index, detection_index_in_that_frame)] in any order; each is an explicit coach choice
    and starts a new epoch that runs until the next selection. Returns one record per frame with
    epoch and segment ids added. Frames no selection reaches are NOT_SELECTED."""
    count = len(frames)
    ordered = sorted(selections)
    records: list[dict | None] = [None] * count
    for epoch, (frame_index, detection_index) in enumerate(ordered, start=1):
        end = ordered[epoch][0] if epoch < len(ordered) else count
        forward = follow(frames, cuts, list(range(frame_index, end)), detection_index, fps, aspect)
        for i, r in forward.items():
            records[i] = {**r, "epoch": epoch, "direction": "FORWARD"}
        if epoch == 1 and backward and frame_index > 0:
            back = follow(frames, cuts, list(range(frame_index, -1, -1)), detection_index, fps, aspect)
            for i, r in back.items():
                if i != frame_index:
                    records[i] = {**r, "epoch": epoch, "direction": "BACKWARD"}
    segment, previous = 0, None
    for i in range(count):
        r = records[i]
        if r is None:
            records[i] = {**_record(NOT_SELECTED, "NO_TARGET_SELECTED_YET"), "epoch": None, "segment": None, "direction": None}
            previous = None
            continue
        if r["state"] == LOCKED:
            if previous is None or previous["state"] != LOCKED or previous["epoch"] != r["epoch"]:
                segment += 1
            r["segment"] = segment
        else:
            r["segment"] = None
        previous = r
    return records


def evidence_segments(records: list[dict], times: list[float]) -> list[dict]:
    """Runs of the same state and epoch: what the report shows as Segment 1, Segment 2, ..."""
    runs: list[dict] = []
    for i, r in enumerate(records):
        key = (r["state"], r["epoch"])
        if runs and runs[-1]["_key"] == key and runs[-1]["end_frame"] == i - 1:
            runs[-1]["end_frame"], runs[-1]["end_s"] = i, times[i]
        else:
            runs.append({"_key": key, "state": r["state"], "epoch": r["epoch"], "reason": None if r["state"] == LOCKED else r["reason"], "start_frame": i,
                         "end_frame": i, "start_s": times[i], "end_s": times[i]})
    for n, run in enumerate(runs, start=1):
        run["segment_number"] = n
        del run["_key"]
    return runs


def measurable_window(records: list[dict], start_frame: int, end_frame: int) -> bool:
    """True only when every frame of the window is LOCKED in one epoch and one unbroken run."""
    if start_frame < 0 or end_frame >= len(records) or end_frame < start_frame:
        return False
    first = records[start_frame]
    return all(records[i]["state"] == LOCKED and records[i]["epoch"] == first["epoch"]
               and records[i]["segment"] == first["segment"] for i in range(start_frame, end_frame + 1))


def tracking_confidence(detection_confidence: float | None, state: str) -> float:
    """Heuristic, NOT a calibrated probability: the detector's confidence scaled by how trustworthy identity is."""
    if detection_confidence is None:
        return 0.0
    return round(detection_confidence * IDENTITY["continuity_factor"].get(state, 0.0), 4)
