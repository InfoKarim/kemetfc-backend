"""A1.5: the coach picks the player in the video, and the system never guesses who they are afterwards.

Synthetic scenes only: actors with a path and a colour descriptor. No video, no model, no real child.
(The iPhone tap-to-frame coordinate mapping is tested in tests/test_ios_target_selection.py.)
"""
import random

import pytest

from app.video_ai import geometry, proxies, target_selection as ts, target_tracking as tt
from app.video_ai.config import IDENTITY

FPS = 60.0
ASPECT = 0.5625          # a portrait 1080 x 1920 frame


def kit(seed: int, noise: float = 0.03, rng_seed: int = 0) -> list[float]:
    """A colour descriptor: the same `seed` is the same clothing (up to noise); another seed is another kit."""
    base = random.Random(seed)
    rng = random.Random(rng_seed * 7919 + seed)
    return [base.random() + rng.uniform(-noise, noise) for _ in range(24)]


class Actor:
    def __init__(self, name, path, seed, height=0.30, width=0.10):
        self.name, self.path, self.seed, self.height, self.width = name, path, seed, height, width

    def box(self, f):
        p = self.path(f)
        if p is None:
            return None
        x, y = p                                          # centre of the box
        return (x - self.width / 2, y - self.height / 2, x + self.width / 2, y + self.height / 2)


def scene(n, actors, absent=None, cuts=()):
    """frames[i] = detections; also returns the ground truth so a test can check WHO was followed."""
    absent = absent or {}
    frames, truth = [], []
    for f in range(n):
        dets, who = [], []
        for a in actors:
            if f in absent.get(a.name, ()):
                continue
            b = a.box(f)
            if b is None:
                continue
            dets.append({"bbox_norm": b, "confidence": 0.9, "appearance": kit(a.seed, rng_seed=f)})
            who.append(a.name)
        frames.append(dets)
        truth.append(who)
    return frames, truth, [f in set(cuts) for f in range(n)]


def line(x0, y0, x1, y1, n):
    return lambda f: (x0 + (x1 - x0) * f / n, y0 + (y1 - y0) * f / n)


def run(frames, cuts, selections, backward=True):
    return tt.track_selected(frames, cuts, selections, FPS, ASPECT, backward=backward)


def followed(records, frames, truth, f):
    """Name of the actor the LOCKED frame's target box actually is."""
    box = records[f]["target"]["bbox_norm"]
    for k, d in enumerate(frames[f]):
        if tuple(d["bbox_norm"]) == tuple(box):
            return truth[f][k]
    return None


def states(records):
    return [r["state"] for r in records]


# ---- 1-3 the tap ----------------------------------------------------------------------------------------------------

DETS = [{"bbox_norm": (0.10, 0.20, 0.30, 0.80), "confidence": 0.9},      # an adult
        {"bbox_norm": (0.12, 0.45, 0.22, 0.75), "confidence": 0.8},      # a child standing in front of the adult
        {"bbox_norm": (0.60, 0.30, 0.75, 0.80), "confidence": 0.7}]


def test_1_a_tap_inside_a_person_selects_that_person():
    assert ts.resolve_tap(DETS, (0.67, 0.55)) == (2, None)
    assert ts.resolve_tap(DETS, (0.20, 0.25)) == (0, None)


def test_2_a_tap_outside_everyone_selects_no_one_and_says_so():
    index, message = ts.resolve_tap(DETS, (0.45, 0.50))
    assert index is None and message == "No player detected at that point. Tap directly on the player."
    # close to a person is still no one: nothing is guessed
    assert ts.resolve_tap(DETS, (0.31, 0.50))[0] is None
    assert ts.resolve_tap(DETS, (1.5, 0.5))[0] is None and ts.resolve_tap([], (0.5, 0.5))[0] is None


def test_3_overlapping_boxes_use_one_documented_deterministic_rule():
    # (0.17, 0.60) is inside both the adult and the child: it is the child's own centre, so the child wins
    assert ts.resolve_tap(DETS, (0.17, 0.60))[0] == 1
    # the adult's body above the child's box is only the adult
    assert ts.resolve_tap(DETS, (0.17, 0.30))[0] == 0
    # identical offsets: the smaller box, then higher confidence, then the lower index
    twins = [{"bbox_norm": (0.2, 0.2, 0.4, 0.6), "confidence": 0.5}, {"bbox_norm": (0.2, 0.2, 0.4, 0.6), "confidence": 0.9}]
    assert ts.resolve_tap(twins, (0.3, 0.4))[0] == 1
    twins[1]["confidence"] = 0.5
    assert ts.resolve_tap(twins, (0.3, 0.4))[0] == 0
    # the same input always gives the same answer
    assert {ts.resolve_tap(DETS, (0.17, 0.60))[0] for _ in range(20)} == {1}


def test_a_selection_is_a_temporary_label_never_a_roster_id_and_only_a_coach_tap_is_verified():
    sel, index, message = ts.make_selection(DETS, (0.67, 0.55), frame_index=10, timestamp_s=0.1667, label="TEST_TARGET",
                                            source=ts.SOURCE_SYNTHETIC_TEST_TAP)
    assert message is None and index == 2 and sel.label == "TEST_TARGET" and sel.bbox_norm == (0.60, 0.30, 0.75, 0.80)
    assert not sel.identity_verified_by_coach
    coach, _, _ = ts.make_selection(DETS, (0.67, 0.55), frame_index=10, timestamp_s=0.1667, label="TARGET-1", source=ts.SOURCE_COACH_TAP)
    assert coach.identity_verified_by_coach
    none, _, why = ts.make_selection(DETS, (0.45, 0.5), frame_index=10, timestamp_s=0.1, label="X", source=ts.SOURCE_COACH_TAP)
    assert none is None and why == ts.NO_PLAYER_AT_TAP_MESSAGE
    assert "PILOT_PLAYER" not in str(sel.as_json())


# ---- 9-10 normal movement, neighbours ---------------------------------------------------------------------------

def test_9_the_target_stays_the_same_person_through_normal_movement():
    a = Actor("A", line(0.2, 0.5, 0.7, 0.5, 300), 1)
    frames, truth, cuts = scene(300, [a])
    r = run(frames, cuts, [(0, 0)])
    assert set(states(r)) == {tt.LOCKED}
    assert len({x["segment"] for x in r}) == 1 and all(x["epoch"] == 1 for x in r)
    assert all(followed(r, frames, truth, f) == "A" for f in range(300))


def test_10_a_nearby_person_does_not_steal_the_identity_even_in_the_same_kit():
    a = Actor("A", line(0.30, 0.5, 0.60, 0.5, 300), 1)
    for other_seed, name in ((2, "different kit"), (1, "same kit")):
        b = Actor("B", line(0.45, 0.5, 0.75, 0.5, 300), other_seed)        # walks beside the target, 0.15 apart
        frames, truth, cuts = scene(300, [a, b])
        r = run(frames, cuts, [(0, 0)])
        wrong = [f for f in range(300) if r[f]["state"] == tt.LOCKED and followed(r, frames, truth, f) != "A"]
        assert wrong == [], name
        assert r[0]["state"] == tt.LOCKED


def test_the_most_prominent_person_is_not_chosen_the_tapped_small_person_is():
    big = Actor("big", line(0.3, 0.5, 0.3, 0.5, 100), 1, height=0.7, width=0.3)
    small = Actor("small", line(0.7, 0.55, 0.7, 0.55, 100), 2, height=0.2, width=0.07)
    frames, truth, cuts = scene(100, [big, small])
    tap = tuple(((frames[0][1]["bbox_norm"][0] + frames[0][1]["bbox_norm"][2]) / 2, (frames[0][1]["bbox_norm"][1] + frames[0][1]["bbox_norm"][3]) / 2))
    index, _ = ts.resolve_tap(frames[0], tap)
    r = run(frames, cuts, [(0, index)])
    assert all(followed(r, frames, truth, f) == "small" for f in range(100) if r[f]["state"] == tt.LOCKED)


# ---- 11 crossing -----------------------------------------------------------------------------------------------------

def crossing(seed_b):
    a = Actor("A", line(0.2, 0.5, 0.8, 0.5, 240), 1)
    b = Actor("B", line(0.8, 0.5, 0.2, 0.5, 240), seed_b)
    return scene(240, [a, b])


def test_11a_crossing_in_different_kits_never_switches_and_the_target_is_taken_back_only_when_provable():
    frames, truth, cuts = crossing(2)
    r = run(frames, cuts, [(0, 0)])
    assert all(followed(r, frames, truth, f) == "A" for f in range(240) if r[f]["state"] == tt.LOCKED)
    assert tt.UNCERTAIN in states(r)                                          # the overlap was noticed
    assert r[0]["state"] == tt.LOCKED


def test_11b_crossing_in_the_same_kit_never_silently_switches_it_asks_to_reselect():
    frames, truth, cuts = crossing(1)
    r = run(frames, cuts, [(0, 0)])
    assert all(followed(r, frames, truth, f) == "A" for f in range(240) if r[f]["state"] == tt.LOCKED)
    assert r[-1]["state"] in (tt.RESELECTION_REQUIRED, tt.UNCERTAIN, tt.LOST)
    assert tt.RESELECTION_REQUIRED in states(r)
    first_bad = states(r).index(tt.RESELECTION_REQUIRED)
    assert all(s == tt.RESELECTION_REQUIRED for s in states(r)[first_bad:])    # nothing recovers by itself


# ---- 12-13 leaving and re-entering -----------------------------------------------------------------------------------

def leaving_scene(gap):
    """A stands touching the left edge, is out of the picture for `gap` frames from frame 60, then is back."""
    return Actor("A", lambda f: (0.06, 0.5) if (f < 60 or f >= 60 + gap) else None, 1, width=0.1)


def test_12_the_target_leaving_the_frame_is_lost_then_reselection_is_required_on_return():
    a = leaving_scene(gap=90)                    # 1.5 s away, last seen touching the left edge
    frames, truth, cuts = scene(240, [a])
    r = run(frames, cuts, [(0, 0)])
    assert r[59]["state"] == tt.LOCKED and r[60]["state"] == tt.UNCERTAIN
    assert tt.LOST in states(r)[60:150]
    assert all(x["target"] is None for x in r[60:150])
    assert r[150]["state"] == tt.RESELECTION_REQUIRED and r[150]["reason"] == "TARGET_LEFT_THE_FRAME_RETURN_NOT_PROVEN"


def test_13_a_person_who_re_enters_is_not_adopted_even_if_they_look_the_same():
    a = leaving_scene(gap=30)                    # back after half a second, same kit, same edge
    frames, truth, cuts = scene(240, [a])
    r = run(frames, cuts, [(0, 0)])
    assert r[60 + 30]["state"] == tt.RESELECTION_REQUIRED
    assert all(x["state"] == tt.RESELECTION_REQUIRED for x in r[90:])


def test_13b_a_similar_stranger_arriving_while_the_target_is_gone_is_not_adopted():
    a = Actor("A", lambda f: (0.5, 0.5) if f < 60 else None, 1)
    stranger = Actor("S", lambda f: (0.5, 0.5) if f >= 100 else None, 1)      # same kit, appears where A was, 0.67 s later
    frames, truth, cuts = scene(200, [a, stranger])
    r = run(frames, cuts, [(0, 0)])
    assert all(x["target"] is None for x in r[60:])
    assert all(s in (tt.LOST, tt.UNCERTAIN, tt.RESELECTION_REQUIRED) for s in states(r[60:]))
    assert r[100]["state"] == tt.RESELECTION_REQUIRED and r[100]["reason"] == "TARGET_RETURN_NOT_PROVEN_AFTER_LOSS"


def test_a_target_hidden_behind_an_obstacle_and_back_in_the_same_place_is_taken_back_without_a_guess():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(200, [a], absent={"A": set(range(60, 80))})      # a third of a second, mid-picture
    r = run(frames, cuts, [(0, 0)])
    assert r[59]["state"] == tt.LOCKED and r[60]["state"] in (tt.UNCERTAIN, tt.LOST)
    assert r[80]["state"] == tt.LOCKED and r[80]["reason"] == "RECOVERED"
    assert r[59]["segment"] != r[80]["segment"]                                # the doubt breaks the run


# ---- 14-15 scene cuts --------------------------------------------------------------------------------------------------

def test_14_a_scene_cut_ends_continuity_even_if_a_person_is_standing_in_the_same_place():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(200, [a], cuts=(100,))
    r = run(frames, cuts, [(0, 0)])
    assert all(x["state"] == tt.LOCKED for x in r[:100])
    assert r[100]["state"] == tt.RESELECTION_REQUIRED and r[100]["reason"] == "SCENE_CUT"
    assert all(x["state"] == tt.RESELECTION_REQUIRED and x["target"] is None for x in r[100:])


def test_15_a_cut_to_a_different_child_is_never_joined_to_the_target():
    before = Actor("A", lambda f: (0.5, 0.5), 1)
    after = Actor("B", lambda f: (0.52, 0.5), 2)               # a different child, almost where A was, other kit
    frames = [[{"bbox_norm": before.box(f), "confidence": 0.9, "appearance": kit(1, rng_seed=f)}] if f < 100
              else [{"bbox_norm": after.box(f), "confidence": 0.9, "appearance": kit(2, rng_seed=f)}] for f in range(200)]
    cuts = [f == 100 for f in range(200)]
    r = run(frames, cuts, [(0, 0)])
    assert all(x["target"] is None for x in r[100:])
    # even a same-kit child in the same place is not joined across the cut
    same = [[{"bbox_norm": before.box(f), "confidence": 0.9, "appearance": kit(1, rng_seed=f)}] for f in range(200)]
    r2 = run(same, cuts, [(0, 0)])
    assert r2[100]["state"] == tt.RESELECTION_REQUIRED and r2[150]["target"] is None


# ---- 16-17 recovery and re-selection --------------------------------------------------------------------------------------

def test_16_a_short_dropout_recovers_only_with_strong_evidence_and_never_with_weak_evidence():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(200, [a], absent={"A": set(range(60, 66))})
    r = run(frames, cuts, [(0, 0)])
    assert r[66]["state"] == tt.LOCKED and r[66]["reason"] == "RECOVERED"
    # the same dropout, but the person reappears in different clothing: not taken back
    odd = [list(f) for f in frames]
    for f in range(66, 200):
        odd[f] = [{**d, "appearance": kit(9, rng_seed=f)} for d in odd[f]]
    r2 = run(odd, cuts, [(0, 0)])
    assert all(x["target"] is None for x in r2[66:])


def test_17_explicit_reselection_starts_a_new_epoch_and_never_bridges_the_old_one():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    b = Actor("B", lambda f: (0.5, 0.5), 2)
    frames = [[{"bbox_norm": (a if f < 100 else b).box(f), "confidence": 0.9, "appearance": kit(1 if f < 100 else 2, rng_seed=f)}] for f in range(300)]
    cuts = [f == 100 for f in range(300)]
    r = run(frames, cuts, [(0, 0)])
    assert r[150]["state"] == tt.RESELECTION_REQUIRED
    r = run(frames, cuts, [(0, 0), (150, 0)])                 # the coach taps again, on a later frame
    assert r[149]["state"] == tt.RESELECTION_REQUIRED          # the gap before the new tap is still unresolved
    assert r[150]["state"] == tt.LOCKED and r[150]["epoch"] == 2 and r[150]["reason"] == "COACH_SELECTED"
    assert r[299]["state"] == tt.LOCKED and r[299]["epoch"] == 2
    assert r[50]["epoch"] == 1 and r[50]["segment"] != r[200]["segment"]
    assert not tt.measurable_window(r, 50, 200)


def test_selection_in_the_middle_tracks_backwards_too_and_stops_at_a_cut():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(200, [a], cuts=(40,))
    r = run(frames, cuts, [(100, 0)])
    assert r[100]["state"] == tt.LOCKED and r[41]["state"] == tt.LOCKED and r[199]["state"] == tt.LOCKED
    assert r[40]["state"] == tt.LOCKED or r[39]["state"] == tt.RESELECTION_REQUIRED
    assert all(x["state"] == tt.RESELECTION_REQUIRED for x in r[:40])
    off = run(frames, cuts, [(100, 0)], backward=False)
    assert off[10]["state"] == tt.NOT_SELECTED


# ---- 18-20 analysis is blocked and measurements never bridge --------------------------------------------------------

def series_from(records):
    out = []
    for f, x in enumerate(records):
        out.append({"t": f / FPS, "state": x["state"], "segment": x["segment"],
                    "center": (0.5 + 0.001 * f + (0.2 if f >= 160 else 0.0), 0.5) if x["state"] == tt.LOCKED else None,
                    "camera_moving": False,
                    "angles": {name: (140.0 + (f % 7)) if x["state"] == tt.LOCKED else None for name in proxies.DERIVED_ANGLES}})
    return out


def test_18_analysis_is_blocked_while_the_target_is_lost():
    assert tt.ANALYSIS_ALLOWED_STATES == (tt.LOCKED,)
    a = Actor("A", lambda f: (0.5, 0.5) if f < 60 or f >= 140 else None, 1)
    frames, truth, cuts = scene(200, [a])
    r = run(frames, cuts, [(0, 0)])
    lost = [f for f in range(60, 140) if r[f]["state"] == tt.LOST]
    assert lost
    angles = geometry.compute_angles(None, identity_ok=False, identity_reason="IDENTITY_LOST")
    assert all(v["state"] == "NOT_MEASURABLE" for v in angles.values())
    out = proxies.per_frame_proxies(series_from(r), ASPECT)
    assert all(out[f]["speed_proxy_frame_heights_per_s"]["state"] == "NOT_MEASURABLE" for f in lost)


def test_19_analysis_is_blocked_while_reselection_is_required():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(200, [a], cuts=(100,))
    r = run(frames, cuts, [(0, 0)])
    out = proxies.per_frame_proxies(series_from(r), ASPECT)
    assert all(out[f]["speed_proxy_frame_heights_per_s"]["state"] == "NOT_MEASURABLE" for f in range(100, 200))
    assert all(o["angular_velocity_deg_per_s"][n]["state"] == "NOT_MEASURABLE" for o in out[100:] for n in proxies.DERIVED_ANGLES)


def test_20_measurements_never_bridge_identity_unsafe_segments():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(400, [a], absent={"A": set(range(150, 160))})
    r = run(frames, cuts, [(0, 0)])
    assert tt.measurable_window(r, 10, 100) and not tt.measurable_window(r, 100, 250)
    assert not tt.measurable_window(r, 140, 170)
    series = series_from(r)
    out = proxies.per_frame_proxies(series, ASPECT)
    windows = proxies.window_proxies(series, FPS)
    for w in windows:
        spans = not tt.measurable_window(r, w["start_frame"], w["end_frame"])
        if spans:
            for key in ("left_knee_dominant_frequency_hz", "right_knee_dominant_frequency_hz", "knee_angle_asymmetry_mean_abs_deg"):
                assert w[key]["state"] == "NOT_MEASURABLE", (w["start_frame"], key)
    # no smoothed derivative may use a frame from the far side of the gap
    assert out[149]["speed_proxy_frame_heights_per_s"]["state"] == "NOT_MEASURABLE"
    assert out[163]["speed_proxy_frame_heights_per_s"]["reason"] == "RUN_EDGE"
    # the first measurable frame after the gap uses only frames after the gap: the 0.2 jump never enters a value
    after = out[164]["speed_proxy_frame_heights_per_s"]
    assert after["state"] == "MEASURED" and abs(after["value"] - 0.001 * FPS * ASPECT) < 1e-3


# ---- evidence segments and frame suggestion --------------------------------------------------------------------------

def test_evidence_is_reported_as_segments_with_times_and_states():
    a = Actor("A", lambda f: (0.5, 0.5), 1)
    frames, truth, cuts = scene(300, [a], cuts=(180,), absent={"A": set(range(100, 110))})
    r = run(frames, cuts, [(0, 0)])
    segs = tt.evidence_segments(r, [f / FPS for f in range(300)])
    names = [(s["state"], s["start_frame"], s["end_frame"]) for s in segs]
    assert names[0] == (tt.LOCKED, 0, 99)
    assert segs[-1]["state"] == tt.RESELECTION_REQUIRED and segs[-1]["start_frame"] == 180 and segs[-1]["end_frame"] == 299
    assert [s["segment_number"] for s in segs] == list(range(1, len(segs) + 1))
    assert tt.UNCERTAIN in {s["state"] for s in segs} or tt.LOST in {s["state"] for s in segs}


def test_frame_suggestion_skips_transitions_dark_blurry_and_empty_frames_and_does_not_favour_frame_zero():
    def frame(people, sharp=100.0, bright=120.0, cut=False):
        return {"detections": [{"bbox_norm": (0.3, 0.3, 0.5, 0.8), "confidence": 0.9}] * people, "sharpness": sharp,
                "brightness": bright, "near_cut": cut}
    frames = [frame(0)] * 10 + [frame(2, bright=5)] * 10 + [frame(2, cut=True)] * 10 + [frame(2, sharp=10)] * 10 + [frame(2)] * 400
    picks = ts.suggest_frames(frames, FPS, count=3)
    assert picks and all(p["frame_index"] >= 40 for p in picks)
    assert all(abs(a["frame_index"] - b["frame_index"]) >= 120 for a, b in zip(picks, picks[1:]))
    assert ts.suggest_frames([frame(0)] * 50, FPS) == []
    tiny = [{"detections": [{"bbox_norm": (0.3, 0.3, 0.32, 0.33), "confidence": 0.95}], "sharpness": 100.0, "brightness": 100.0, "near_cut": False}] * 50
    assert ts.suggest_frames(tiny, FPS) == []
