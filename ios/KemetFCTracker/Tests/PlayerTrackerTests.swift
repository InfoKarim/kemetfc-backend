//
//  PlayerTrackerTests.swift
//  KemetFCTrackerTests
//
//  STATUS: GENUINELY VERIFIED — see FramingMathTests.swift's header for
//  the exact methodology (standalone `swiftc` executable, not `swift
//  test`). Result when last run: 14/14 checks passed, after this exact
//  test sequence caught and led to fixing 3 real PlayerTracker
//  state-machine bugs (see PlayerTracker.swift's header). These XCTest
//  cases are a direct port of that verified standalone runner.
//

import XCTest
@testable import KemetFCTracker

final class PlayerTrackerTests: XCTestCase {
    private let redJersey = AppearanceSignature(torsoColor: (200, 30, 30), legsColor: (20, 20, 20))
    private let blueJersey = AppearanceSignature(torsoColor: (30, 30, 200), legsColor: (240, 240, 240))

    private func det(_ x: Double, _ y: Double, w: Double = 0.15, h: Double = 0.4, conf: Double = 0.9) -> PersonDetection {
        PersonDetection(boundingBox: NormalizedRect(x: x, y: y, width: w, height: h), confidence: conf)
    }

    @MainActor
    func testBasicLockAndContinuity() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        XCTAssertEqual(tracker.state, .locked)

        tracker.update(
            detections: [det(0.41, 0.3), det(0.9, 0.9)],
            appearanceSignatures: [redJersey, blueJersey],
            at: 0.05
        )
        XCTAssertEqual(tracker.state, .locked)
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.41)
    }

    @MainActor
    func testNeverSwitchesToAClosterButDifferentStranger() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.3, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.31, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // A completely different, much closer/bigger person appears far
        // from the locked player's predicted position — must NOT be matched.
        tracker.update(
            detections: [det(0.8, 0.8, w: 0.4, h: 0.8), det(0.32, 0.3)],
            appearanceSignatures: [blueJersey, redJersey],
            at: 0.10
        )
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.32)
        XCTAssertEqual(tracker.state, .locked)
    }

    @MainActor
    func testSameKitCollisionResolvedByMotionContinuity() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // Two candidates in IDENTICAL kit: one continues the predicted
        // motion smoothly (the real player), one is a teleport-implausible
        // decoy placed deliberately far away.
        tracker.update(
            detections: [det(0.42, 0.3), det(0.9, 0.9)],
            appearanceSignatures: [redJersey, redJersey],
            at: 0.10
        )
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.42)
        XCTAssertEqual(tracker.state, .locked)
    }

    @MainActor
    func testGenuinelyAmbiguousSameKitOverlapIsFlagged() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // Two same-kit candidates BOTH near the predicted position — must
        // not confidently guess (spec 10).
        tracker.update(
            detections: [det(0.415, 0.30, conf: 0.9), det(0.405, 0.31, conf: 0.9)],
            appearanceSignatures: [redJersey, redJersey],
            at: 0.10
        )
        XCTAssertTrue(tracker.lastMatchWasAmbiguous)
    }

    @MainActor
    func testWildlyDifferentScaleAtSamePositionIsNotTrustedAtFullConfidence() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3, w: 0.15, h: 0.4)], appearanceSignatures: [redJersey], at: 0.05)
        // Same center-ish position, but 5x the area — implausible for the
        // same physical player one frame later.
        tracker.update(
            detections: [det(0.41, 0.3, w: 0.5, h: 0.9)],
            appearanceSignatures: [redJersey],
            at: 0.10
        )
        XCTAssertTrue(tracker.lastMatchWasAmbiguous || tracker.state != .locked || tracker.currentBoundingBox?.width != 0.5)
    }

    @MainActor
    func testMissToTemporarilyLostToSearchingToLostProgression() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        var t: TimeInterval = 0.0
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .temporarilyLost)

        t += 2.0 // past maxTemporaryLossSeconds (1.5s)
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .searching)

        t += 7.0 // past maxSearchingSeconds (6.0s)
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .lost)

        // LOST requires explicit reset(), never auto-recovers from update().
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: t + 0.1)
        XCTAssertEqual(tracker.state, .lost)

        tracker.reset(to: NormalizedRect(x: 0.5, y: 0.5, width: 0.15, height: 0.4), appearance: redJersey)
        XCTAssertEqual(tracker.state, .locked)
    }
}

// MARK: - Identity safety: same-kit crossing, occlusion, reacquisition
//
//  STATUS: written BEFORE any PlayerTracker change, to describe the
//  behavior a safe youth-player tracker must have — thresholds were
//  deliberately NOT tuned to make these pass. A failure here is a real
//  finding about PlayerTracker, not a broken test. Every scenario is a
//  deterministic frame-by-frame simulation (15 Hz, no randomness) with
//  ground truth: candidate label "A" is the coach-selected target,
//  anything else ("B", ...) is a different child. The tracker is never
//  told the labels; the harness recovers which candidate the tracker
//  committed to by matching its bounding box to the frame's candidates.
//
//  Rule under test (brief, "false identity continuity is worse than
//  temporary loss"): the tracker may follow A, go temporarilyLost /
//  searching, or report ambiguity — it must NEVER commit to B.

private let frameDT = 1.0 / 15.0
private let sameKit = AppearanceSignature(torsoColor: (30, 90, 200), legsColor: (20, 20, 60))
private let similarKit = AppearanceSignature(torsoColor: (34, 86, 205), legsColor: (24, 18, 64))

private enum IdentityFailure: String {
    case idSwitch = "ID SWITCH"
    case falseReacquisition = "FALSE REACQUISITION"
    case ambiguityCommittedAsLocked = "AMBIGUITY COMMITTED AS LOCKED"
    case ambiguityNotReported = "AMBIGUITY NOT REPORTED"
    case failureToReacquire = "FAILURE TO REACQUIRE"
    case targetNotRetained = "TARGET NOT RETAINED"
}

private struct SimCandidate {
    let label: String
    let box: NormalizedRect
    let signature: AppearanceSignature
    let confidence: Double
}

private struct SimFrame {
    let index: Int
    let time: TimeInterval
    let note: String
    let candidates: [SimCandidate]
    let truthA: NormalizedRect?
}

private struct FrameOutcome {
    let frame: SimFrame
    let stateBefore: PlayerTrackState
    let stateAfter: PlayerTrackState
    let selected: String?
    let ambiguous: Bool
    let confidence: Double
    let trackerBox: NormalizedRect?
}

private func personBox(cx: Double, cy: Double = 0.5, w: Double = 0.10, h: Double = 0.30) -> NormalizedRect {
    NormalizedRect(x: cx - w / 2, y: cy - h / 2, width: w, height: h)
}

private func cand(
    _ label: String, cx: Double, cy: Double = 0.5, w: Double = 0.10, h: Double = 0.30,
    kit: AppearanceSignature = sameKit, conf: Double = 0.9
) -> SimCandidate {
    SimCandidate(label: label, box: personBox(cx: cx, cy: cy, w: w, h: h), signature: kit, confidence: conf)
}

/// Deterministic pseudo-noise in [-1, 1] — a fixed function of the frame
/// index, so "jitter" scenarios are reproducible run to run.
private func jit(_ k: Int, _ salt: Double) -> Double {
    sin(Double(k) * 12.9898 + salt * 78.233)
}

private func makeFrames(
    _ count: Int,
    _ build: (Int) -> (note: String, candidates: [SimCandidate], truthA: NormalizedRect?)
) -> [SimFrame] {
    (1...count).map { k in
        let built = build(k)
        return SimFrame(index: k, time: Double(k) * frameDT, note: built.note, candidates: built.candidates, truthA: built.truthA)
    }
}

private func fmt(_ value: Double) -> String { String(format: "%.3f", value) }

private func candidateSummary(_ frame: SimFrame) -> String {
    let parts = frame.candidates.map { "\($0.label)(cx=\(fmt($0.box.center.x)),conf=\(String(format: "%.2f", $0.confidence)))" }
    return parts.isEmpty ? "none" : parts.joined(separator: " ")
}

private func failureMessage(
    scenario: String, kind: IdentityFailure, expected: String, outcome o: FrameOutcome, detail: String, violatingFrames: [Int]
) -> String {
    let truth = o.frame.truthA.map { "A truth cx=\(fmt($0.center.x))" } ?? "A truth=not visible"
    let trackerAt = o.trackerBox.map { "tracker box cx=\(fmt($0.center.x))" } ?? "tracker box=nil"
    return "[\(scenario)] \(kind.rawValue) at frame \(o.frame.index) (t=\(fmt(o.frame.time))s, \(o.frame.note)): "
        + "expected target=\(expected); selected candidate=\(o.selected ?? "none"); "
        + "tracker state \(o.stateBefore.rawValue) -> \(o.stateAfter.rawValue); ambiguous=\(o.ambiguous); "
        + "confidence=\(String(format: "%.2f", o.confidence)); candidates=[\(candidateSummary(o.frame))]; \(truth); \(trackerAt). "
        + "\(detail) Violating frames: \(violatingFrames)"
}

@MainActor
private func makeTracker(initial: NormalizedRect, kit: AppearanceSignature = sameKit) -> PlayerTracker {
    PlayerTracker(targetTrackId: 1, initialBoundingBox: initial, appearance: kit)
}

@MainActor
private func runScenario(tracker: PlayerTracker, frames: [SimFrame]) -> [FrameOutcome] {
    var outcomes: [FrameOutcome] = []
    for frame in frames {
        let before = tracker.state
        tracker.update(
            detections: frame.candidates.map { PersonDetection(boundingBox: $0.box, confidence: $0.confidence) },
            appearanceSignatures: frame.candidates.map(\.signature),
            at: frame.time
        )
        // A commit this frame == confidence > 0 (a miss zeroes it) and the
        // tracker's box is exactly one of this frame's candidate boxes.
        let selected: String? = tracker.currentConfidence > 0
            ? frame.candidates.first(where: { $0.box == tracker.currentBoundingBox })?.label
            : nil
        outcomes.append(FrameOutcome(
            frame: frame, stateBefore: before, stateAfter: tracker.state, selected: selected,
            ambiguous: tracker.lastMatchWasAmbiguous, confidence: tracker.currentConfidence,
            trackerBox: tracker.currentBoundingBox
        ))
    }
    return outcomes
}

/// THE safety property: the tracker never commits to anyone but A.
@MainActor
private func assertNeverCommitsToDecoy(
    _ scenario: String, _ outcomes: [FrameOutcome], file: StaticString = #filePath, line: UInt = #line
) {
    let violations = outcomes.filter { $0.selected != nil && $0.selected != "A" }
    guard let first = violations.first else { return }
    let kind: IdentityFailure
    if first.stateBefore == .temporarilyLost || first.stateBefore == .searching {
        kind = .falseReacquisition
    } else if first.ambiguous {
        kind = .ambiguityCommittedAsLocked
    } else {
        kind = .idSwitch
    }
    XCTFail(failureMessage(
        scenario: scenario, kind: kind, expected: "A (or no commit)", outcome: first,
        detail: "Tracker committed to decoy \(first.selected ?? "?").", violatingFrames: violations.map(\.frame.index)
    ), file: file, line: line)
}

/// Liveness: within `range`, the tracker must be following A.
@MainActor
private func assertFollowsTarget(
    _ scenario: String, _ outcomes: [FrameOutcome], frames range: ClosedRange<Int>,
    kind: IdentityFailure = .failureToReacquire, file: StaticString = #filePath, line: UInt = #line
) {
    let ok: Set<PlayerTrackState> = [.locked, .reacquired]
    let misses = outcomes.filter { range.contains($0.frame.index) && !($0.selected == "A" && ok.contains($0.stateAfter)) }
    guard let first = misses.first else { return }
    XCTFail(failureMessage(
        scenario: scenario, kind: kind, expected: "A, state locked/reacquired", outcome: first,
        detail: "Target A was visible and consistent with its trajectory but is not being followed.",
        violatingFrames: misses.map(\.frame.index)
    ), file: file, line: line)
}

@MainActor
final class PlayerTrackerIdentityTests: XCTestCase {

    // MARK: Primary scenario

    /// A (selected) and B (same kit, same scale) walk toward each other,
    /// overlap, A is fully occluded for 4 frames while B occupies its
    /// predicted position, they cross, and separate on opposite sides.
    private func primaryCrossing() -> (initial: NormalizedRect, frames: [SimFrame]) {
        let perFrame = 0.30 * frameDT
        let frames = makeFrames(30) { k in
            let a = 0.25 + perFrame * Double(k)
            let b = 0.75 - perFrame * Double(k)
            let occluded = (11...14).contains(k)
            var list: [SimCandidate] = []
            if !occluded { list.append(cand("A", cx: a)) }
            list.append(cand("B", cx: b, cy: 0.506))
            let note: String
            switch k {
            case ..<9: note = "approaching, separated"
            case 9...10: note = "approaching, boxes touching"
            case 11...14: note = "A fully occluded behind B, crossing"
            case 15...17: note = "just crossed, separating"
            default: note = "separated, A continues right"
            }
            return (note, list, personBox(cx: a))
        }
        return (personBox(cx: 0.25), frames)
    }

    func testPrimary_sameKitCrossingWithOcclusion_neverCommitsToPlayerB() {
        let s = primaryCrossing()
        let outcomes = runScenario(tracker: makeTracker(initial: s.initial), frames: s.frames)
        assertNeverCommitsToDecoy("primary crossing + occlusion", outcomes)
    }

    func testPrimary_afterSeparation_playerAIsFollowedAgain() {
        let s = primaryCrossing()
        let outcomes = runScenario(tracker: makeTracker(initial: s.initial), frames: s.frames)
        // 10+ frames after the crossing, A is 0.2+ away from B and moving
        // on a smooth, predictable path: the tracker should be back on A.
        assertFollowsTarget("primary crossing: reacquisition after separation", outcomes, frames: 27...30)
    }

    // MARK: A. Different speeds

    private func speedCrossing(aSpeed: Double, bSpeed: Double) -> (NormalizedRect, [SimFrame]) {
        let frames = makeFrames(30) { k in
            let a = 0.25 + aSpeed * frameDT * Double(k)
            let b = 0.75 - bSpeed * frameDT * Double(k)
            var list: [SimCandidate] = []
            if !(12...13).contains(k) { list.append(cand("A", cx: a)) }   // dropped at closest approach
            list.append(cand("B", cx: b, cy: 0.506))
            return ("A=\(fmt(a)) B=\(fmt(b))", list, personBox(cx: a))
        }
        return (personBox(cx: 0.25), frames)
    }

    func testA1_crossingAtDifferentSpeeds_targetFastDecoySlow() {
        let s = speedCrossing(aSpeed: 0.45, bSpeed: 0.15)
        let outcomes = runScenario(tracker: makeTracker(initial: s.0), frames: s.1)
        assertNeverCommitsToDecoy("A1 crossing, target fast / decoy slow", outcomes)
    }

    func testA2_crossingAtDifferentSpeeds_targetSlowDecoyFast() {
        let s = speedCrossing(aSpeed: 0.15, bSpeed: 0.45)
        let outcomes = runScenario(tracker: makeTracker(initial: s.0), frames: s.1)
        assertNeverCommitsToDecoy("A2 crossing, target slow / decoy fast", outcomes)
    }

    // MARK: B. Moving-target reacquisition after multiple missed frames

    func testB_movingTargetReacquiredAfterMissedFrames() {
        let perFrame = 0.35 * frameDT
        let frames = makeFrames(30) { k in
            let a = 0.15 + perFrame * Double(k)
            var list: [SimCandidate] = []
            if !(11...15).contains(k) { list.append(cand("A", cx: a)) }   // 5 missed frames (0.33 s)
            list.append(cand("B", cx: 0.85, cy: 0.72))                    // far, static, same kit
            return ((11...15).contains(k) ? "A missing" : "A visible", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.15)), frames: frames)
        assertNeverCommitsToDecoy("B moving-target reacquisition", outcomes)
        // A reappears at 16 exactly where its motion says it should be.
        assertFollowsTarget("B moving-target reacquisition", outcomes, frames: 18...30)
    }

    // MARK: C. Partial occlusion

    func testC_partialOcclusionByDecoy_neverCommitsToPlayerB() {
        let w = 0.10
        let frames = makeFrames(32) { k in
            let a = 0.30 + 0.20 * frameDT * Double(k)
            let b = 0.70 - 0.20 * frameDT * Double(k)   // B is nearer the camera: it occludes A
            var list: [SimCandidate] = []
            // Visible part of A = A's extent minus B's extent.
            let aLeft = a - w / 2, aRight = a + w / 2, bLeft = b - w / 2, bRight = b + w / 2
            var visLeft = aLeft, visRight = aRight
            if a <= b { visRight = min(aRight, bLeft) } else { visLeft = max(aLeft, bRight) }
            let visW = visRight - visLeft
            if visW >= 0.03 {
                list.append(cand("A", cx: (visLeft + visRight) / 2, w: visW, conf: 0.5 + 0.4 * min(visW / w, 1)))
            }
            list.append(cand("B", cx: b, cy: 0.506))
            return ("A visible width=\(fmt(max(visW, 0)))", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.30)), frames: frames)
        assertNeverCommitsToDecoy("C partial occlusion", outcomes)
    }

    // MARK: D. Target missing for several frames, then returns

    func testD_targetMissingForSeveralFramesThenReturns_isReacquired() {
        let perFrame = 0.03 * frameDT   // slow drift: A is nearly stationary
        let frames = makeFrames(40) { k in
            let a = 0.50 + perFrame * Double(k)
            var list: [SimCandidate] = []
            if !(6...33).contains(k) { list.append(cand("A", cx: a)) }   // ~1.9 s: TEMP_LOST -> SEARCHING
            return ((6...33).contains(k) ? "A missing" : "A visible", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.50)), frames: frames)
        assertNeverCommitsToDecoy("D target returns", outcomes)
        assertFollowsTarget("D target returns", outcomes, frames: 36...40)
    }

    // MARK: E. Detection jitter

    func testE_detectionJitter_targetRetainedNoDecoyCommit() {
        let perFrame = 0.20 * frameDT
        let frames = makeFrames(40) { k in
            let a = 0.40 + perFrame * Double(k)
            let aBox = cand(
                "A", cx: a + 0.008 * jit(k, 1), cy: 0.5 + 0.008 * jit(k, 2),
                w: 0.10 + 0.006 * jit(k, 3), h: 0.30 + 0.006 * jit(k, 4),
                conf: 0.75 + 0.20 * jit(k, 5)
            )
            let bBox = cand(
                "B", cx: a - 0.30 + 0.008 * jit(k, 6), cy: 0.55 + 0.008 * jit(k, 7),
                w: 0.10 + 0.006 * jit(k, 8), h: 0.30 + 0.006 * jit(k, 9),
                conf: 0.75 + 0.20 * jit(k, 10)
            )
            return ("jittered detections", [aBox, bBox], personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.40)), frames: frames)
        assertNeverCommitsToDecoy("E jitter", outcomes)
        assertFollowsTarget("E jitter", outcomes, frames: 1...40, kind: .targetNotRetained)
    }

    // MARK: F. Low-confidence candidate near the predicted target

    func testF_lowConfidenceGhostNearPredictedTarget_isNotCommitted() {
        let perFrame = 0.20 * frameDT
        let frames = makeFrames(20) { k in
            let a = 0.30 + perFrame * Double(k)
            let aLast = 0.30 + perFrame * 8
            var list: [SimCandidate] = []
            if !(9...12).contains(k) { list.append(cand("A", cx: a)) }   // A occluded 9-12
            if (9...12).contains(k) {
                list.append(cand("B", cx: aLast + perFrame * Double(k - 8) + 0.02, conf: 0.15))   // ghost at predicted spot
            }
            return ((9...12).contains(k) ? "A occluded, 0.15-confidence ghost near prediction" : "A visible", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.30)), frames: frames)
        assertNeverCommitsToDecoy("F low-confidence ghost", outcomes)
    }

    // MARK: G. High-confidence wrong player appearing at the last-known location

    func testG_highConfidenceWrongPlayerAtLastKnownLocation_isNotCommitted() {
        let perFrame = 0.45 * frameDT   // A walks quickly
        let lastSeen = 8
        let frames = makeFrames(30) { k in
            let a = 0.15 + perFrame * Double(k)
            let aLastCx = 0.15 + perFrame * Double(lastSeen)
            var list: [SimCandidate] = []
            if !(9...14).contains(k) { list.append(cand("A", cx: a)) }
            if k >= 10 {   // wrong child walks in at A's last-known spot, heading the other way
                list.append(cand("B", cx: aLastCx - 0.10 * frameDT * Double(k - 10), cy: 0.52, kit: similarKit, conf: 0.97))
            }
            return ((9...14).contains(k) ? "A hidden, B at A's last-known spot" : "A visible", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.15)), frames: frames)
        assertNeverCommitsToDecoy("G wrong player at last-known location", outcomes)
    }

    // MARK: H. Two nearly equal candidates during crossing

    /// While A and B are near-indistinguishable (centers within 0.02, same
    /// kit, same scale), a commit must be flagged ambiguous and must never
    /// land on B.
    @MainActor
    private func assertAmbiguityReported(_ scenario: String, _ outcomes: [FrameOutcome], file: StaticString = #filePath, line: UInt = #line) {
        let nearEqual = outcomes.filter { o in
            guard let a = o.frame.candidates.first(where: { $0.label == "A" }),
                  let b = o.frame.candidates.first(where: { $0.label == "B" }) else { return false }
            return a.box.center.distance(to: b.box.center) <= 0.02
        }
        let unreported = nearEqual.filter { $0.selected != nil && !$0.ambiguous }
        guard let first = unreported.first else { return }
        XCTFail(failureMessage(
            scenario: scenario, kind: .ambiguityNotReported, expected: "ambiguity flagged (or no commit) while A/B are indistinguishable",
            outcome: first, detail: "A and B centers within 0.02 with identical kit/scale, yet the tracker committed with no ambiguity flag.",
            violatingFrames: unreported.map(\.frame.index)
        ), file: file, line: line)
    }

    func testH1_nearlyEqualCandidates_decoyOvertakesTarget() {
        let frames = makeFrames(30) { k in
            let a = 0.50 + 0.06 * frameDT * Double(k)
            let b = 0.42 + 0.14 * frameDT * Double(k)   // B catches up from behind and passes A
            return ("A=\(fmt(a)) B=\(fmt(b))", [cand("A", cx: a), cand("B", cx: b, cy: 0.506)], personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.50)), frames: frames)
        assertNeverCommitsToDecoy("H1 near-equal candidates (overtake)", outcomes)
        assertAmbiguityReported("H1 near-equal candidates (overtake)", outcomes)
    }

    func testH2_nearlyEqualCandidates_slowHeadOnCrossing() {
        let frames = makeFrames(40) { k in
            let a = 0.45 + 0.04 * frameDT * Double(k)
            let b = 0.55 - 0.04 * frameDT * Double(k)
            return ("A=\(fmt(a)) B=\(fmt(b))", [cand("A", cx: a), cand("B", cx: b, cy: 0.506)], personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.45)), frames: frames)
        assertNeverCommitsToDecoy("H2 near-equal candidates (head-on)", outcomes)
        assertAmbiguityReported("H2 near-equal candidates (head-on)", outcomes)
    }

    // MARK: I. Target and decoy swap sides

    func testI_targetAndDecoySwapSidesAfterCrossing_neverCommitsToDecoy() {
        let frames = makeFrames(40) { k in
            let a = 0.15 + 0.25 * frameDT * Double(k)
            let b = 0.85 - 0.25 * frameDT * Double(k)
            return ("A=\(fmt(a)) B=\(fmt(b)) A is \(a < b ? "left" : "right") of B",
                    [cand("A", cx: a), cand("B", cx: b, cy: 0.506)], personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.15)), frames: frames)
        assertNeverCommitsToDecoy("I side swap", outcomes)
        // Any commit after the swap must be on A's side (right of the crossing).
        let wrongSide = outcomes.filter { o in
            guard o.frame.index >= 26, let box = o.trackerBox, o.selected != nil else { return false }
            return box.center.x < 0.5
        }
        if let first = wrongSide.first {
            XCTFail(failureMessage(
                scenario: "I side swap", kind: .idSwitch, expected: "A on the right of the crossing", outcome: first,
                detail: "Committed box is on B's side of the crossing.", violatingFrames: wrongSide.map(\.frame.index)
            ))
        }
    }

    // MARK: J. Same appearance + similar scale + overlapping trajectories

    func testJ_overlappingParallelTrajectories_neverCommitsToDecoy() {
        let aPer = 0.24 * frameDT
        let frames = makeFrames(35) { k in
            let a = 0.30 + aPer * Double(k)
            let bAt20 = 0.30 + aPer * 20 + 0.03
            let b = k <= 20 ? a + 0.03 : bAt20 + 0.50 * frameDT * Double(k - 20)   // walks with A, then pulls away
            return ("A=\(fmt(a)) B=\(fmt(b))", [cand("A", cx: a), cand("B", cx: b, cy: 0.506)], personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.30)), frames: frames)
        assertNeverCommitsToDecoy("J overlapping parallel trajectories", outcomes)
        assertFollowsTarget("J overlapping parallel trajectories", outcomes, frames: 33...35, kind: .targetNotRetained)
    }
}

// MARK: - Fix #2 scenarios: moving-target prediction + safe reacquisition
//
//  Written BEFORE the Fix #2 production change. Same rules as above: A is
//  the coach-selected target, other labels are different children, and
//  "never commits to a decoy" is the invariant. Liveness assertions
//  ("A is reacquired within 3 frames of returning") describe the wanted
//  recovery behavior; safety assertions never depend on them.

@MainActor
final class PlayerTrackerMotionReacquisitionTests: XCTestCase {

    /// A walks right at `speed` from `start`; last trusted frame is 10.
    private func aX(_ k: Int, start: Double = 0.15, speed: Double = 0.35) -> Double {
        start + speed * frameDT * Double(k)
    }

    // M1 — moving target vanishes for 3-5 frames and continues in the same direction.
    func testM1_movingTargetDisappearsThreeToFiveFrames_isReacquiredOnItsPath() {
        for gap in 3...5 {
            let missing = 11...(10 + gap)
            let frames = makeFrames(30) { k in
                var list: [SimCandidate] = []
                if !missing.contains(k) { list.append(cand("A", cx: aX(k))) }
                list.append(cand("B", cx: 0.85, cy: 0.72))   // far, static, same kit
                return (missing.contains(k) ? "A missing" : "A visible", list, personBox(cx: aX(k)))
            }
            let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
            assertNeverCommitsToDecoy("M1 gap=\(gap) frames", outcomes)
            assertFollowsTarget("M1 gap=\(gap) frames", outcomes, frames: (10 + gap + 4)...30)   // returns at 11+gap; 3 frames of grace
        }
    }

    // M2 — moving target vanishes; a same-kit decoy appears at A's OLD position.
    func testM2_decoyAtTargetsOldPositionWhileTargetMissing_isNotReacquired() {
        let lastSeen = aX(10)
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(11...15).contains(k) { list.append(cand("A", cx: aX(k))) }
            if k >= 11 { list.append(cand("B", cx: lastSeen, cy: 0.5, conf: 0.95)) }   // stands where A was last seen
            return ((11...15).contains(k) ? "A missing, B at A's old spot" : "A visible", list, personBox(cx: aX(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M2 decoy at old position", outcomes)
        assertFollowsTarget("M2 decoy at old position", outcomes, frames: 19...30)
    }

    // M2r — same as M2, but B appears one frame AFTER the tracker has registered
    // the loss (the case "reacquired" describes). M2 itself puts B on the very
    // frame A vanishes while the tracker is still `locked`; see the Fix #2 report.
    func testM2r_decoyAtOldPositionAfterLossIsRegistered_isNotReacquired() {
        let lastSeen = aX(10)
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(11...15).contains(k) { list.append(cand("A", cx: aX(k))) }
            if k >= 12 { list.append(cand("B", cx: lastSeen, cy: 0.5, conf: 0.95)) }
            return ((11...15).contains(k) ? "A missing, B at A's old spot" : "A visible", list, personBox(cx: aX(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M2r decoy at old position (after loss)", outcomes)
        assertFollowsTarget("M2r decoy at old position (after loss)", outcomes, frames: 19...30)
    }

    // M5r — same as M5, but the wrong-direction decoy appears after the loss is registered.
    func testM5r_wrongDirectionDecoyAfterLossIsRegistered_isNotReacquired() {
        func a(_ k: Int) -> Double { k <= 6 ? 0.50 + 0.02 * Double(k) : 0.62 - 0.02 * Double(k - 6) }
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(13...16).contains(k) { list.append(cand("A", cx: a(k))) }
            if k >= 14 { list.append(cand("B", cx: 0.50 + 0.02 * Double(k - 12), cy: 0.506)) }
            return ((13...16).contains(k) ? "A missing" : "A visible", list, personBox(cx: a(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.50)), frames: frames)
        assertNeverCommitsToDecoy("M5r direction change (after loss)", outcomes)
        assertFollowsTarget("M5r direction change (after loss)", outcomes, frames: 21...30)
    }

    // M3 — A returns near (not exactly on) the predicted position: still reacquired.
    func testM3_targetReturnsNearPredictedPosition_isReacquired() {
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(11...14).contains(k) {
                let dx = k >= 15 ? 0.02 : 0.0   // slight speed-up while hidden
                list.append(cand("A", cx: aX(k) + dx, cy: k >= 15 ? 0.51 : 0.5, conf: k >= 15 ? 0.8 : 0.9))
            }
            return ((11...14).contains(k) ? "A missing" : "A visible", list, personBox(cx: aX(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M3 return near prediction", outcomes)
        assertFollowsTarget("M3 return near prediction", outcomes, frames: 18...30)
    }

    // M4 — A and B cross (contested), then separate: A is reacquired only once
    // the evidence is discriminative again — never during the occlusion.
    func testM4_crossThenSeparate_targetReacquiredOnlyAfterSeparation() {
        let perFrame = 0.30 * frameDT
        let frames = makeFrames(30) { k in
            let a = 0.25 + perFrame * Double(k)
            let b = 0.75 - perFrame * Double(k)
            var list: [SimCandidate] = []
            if !(11...14).contains(k) { list.append(cand("A", cx: a)) }
            list.append(cand("B", cx: b, cy: 0.506))
            return ("A=\(fmt(a)) B=\(fmt(b))", list, personBox(cx: a))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.25)), frames: frames)
        assertNeverCommitsToDecoy("M4 cross then separate", outcomes)
        if let early = outcomes.first(where: { (12...14).contains($0.frame.index) && $0.selected != nil }) {
            XCTFail(failureMessage(
                scenario: "M4 cross then separate", kind: .idSwitch, expected: "no commit while A/B are occluded and indistinguishable",
                outcome: early, detail: "Tracker committed during the contested occlusion.", violatingFrames: [early.frame.index]
            ))
        }
        assertFollowsTarget("M4 cross then separate", outcomes, frames: 20...30)
    }

    // M5 — A reverses direction before vanishing: prediction follows the LAST
    // trusted velocity, and a decoy on the pre-reversal path is not accepted.
    func testM5_targetReversesBeforeDisappearing_predictionFollowsLastVelocity() {
        func a(_ k: Int) -> Double { k <= 6 ? 0.50 + 0.02 * Double(k) : 0.62 - 0.02 * Double(k - 6) }   // right, then left
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(13...16).contains(k) { list.append(cand("A", cx: a(k))) }
            if k >= 13 { list.append(cand("B", cx: 0.50 + 0.02 * Double(k - 12), cy: 0.506)) }   // keeps going right
            return ((13...16).contains(k) ? "A missing" : "A visible", list, personBox(cx: a(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.50)), frames: frames)
        assertNeverCommitsToDecoy("M5 direction change", outcomes)
        assertFollowsTarget("M5 direction change", outcomes, frames: 21...30)
    }

    // M6 — long absence: prediction is no longer trustworthy, so a same-kit
    // candidate sitting where the (capped) prediction lands must not be locked.
    func testM6_longAbsence_predictionAloneDoesNotReacquire() {
        let frames = makeFrames(45) { k in
            var list: [SimCandidate] = []
            if k <= 8 { list.append(cand("A", cx: aX(k, start: 0.10))) }
            if k >= 40 {   // ~2.1 s after A vanished, walking with A's old velocity from the capped-prediction spot
                list.append(cand("B", cx: aX(8, start: 0.10) + 0.35 * 1.5 + 0.35 * frameDT * Double(k - 40), cy: 0.5))
            }
            return (k <= 8 ? "A visible" : "A missing (long absence)", list, personBox(cx: aX(k, start: 0.10)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: 0.10)), frames: frames)
        assertNeverCommitsToDecoy("M6 long absence", outcomes)
    }

    // M7 — A slows and stops while hidden: a safe false loss is acceptable,
    // a wrong-player lock is not.
    func testM7_targetStopsWhileHidden_neverLocksTheWrongPlayer() {
        let stoppedAt = aX(10) + 0.05
        let frames = makeFrames(35) { k in
            var list: [SimCandidate] = []
            if k <= 10 { list.append(cand("A", cx: aX(k))) }
            if k >= 17 { list.append(cand("A", cx: stoppedAt)) }   // stopped, reappears
            list.append(cand("B", cx: 0.85, cy: 0.72))
            let truth = k <= 10 ? aX(k) : stoppedAt
            return (k <= 10 ? "A moving" : (k < 17 ? "A hidden, stopped" : "A stopped, visible"), list, personBox(cx: truth))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M7 target stops", outcomes)
    }

    // M8 — low-confidence detections on the predicted path never reacquire
    // (0.15 fails the Fix #1 floor; 0.40 passes it but is below the stricter
    // reacquisition confidence), and A is still recovered afterwards.
    func testM8_lowConfidenceDetectionsNearPrediction_areNotReacquired() {
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(11...16).contains(k) { list.append(cand("A", cx: aX(k))) }
            if (11...14).contains(k) { list.append(cand("B", cx: aX(k), cy: 0.5, conf: 0.15)) }
            if (15...16).contains(k) { list.append(cand("B", cx: aX(k), cy: 0.5, conf: 0.40)) }
            return ((11...16).contains(k) ? "A missing, low-confidence ghost on path" : "A visible", list, personBox(cx: aX(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M8 low-confidence near prediction", outcomes)
        assertFollowsTarget("M8 low-confidence near prediction", outcomes, frames: 21...30)
    }

    // M9 — a same-kit decoy that only APPROXIMATELY follows A's predicted
    // trajectory (right place, wrong movement) must not cause a switch.
    func testM9_decoyApproximatelyOnPredictedPath_predictionAloneDoesNotSwitch() {
        let frames = makeFrames(30) { k in
            var list: [SimCandidate] = []
            if !(11...17).contains(k) { list.append(cand("A", cx: aX(k))) }
            if (12...22).contains(k) {   // starts within a few hundredths of the predicted spot, then loiters
                list.append(cand("B", cx: aX(12) + 0.02 + 0.05 * frameDT * Double(k - 12), cy: 0.5, conf: 0.9))
            }
            return ((11...17).contains(k) ? "A missing, B loitering near predicted spot" : "A visible", list, personBox(cx: aX(k)))
        }
        let outcomes = runScenario(tracker: makeTracker(initial: personBox(cx: aX(0))), frames: frames)
        assertNeverCommitsToDecoy("M9 approximate decoy", outcomes)
        assertFollowsTarget("M9 approximate decoy", outcomes, frames: 22...30)
    }
}
