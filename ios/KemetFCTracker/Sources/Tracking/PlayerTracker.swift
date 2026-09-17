//
//  PlayerTracker.swift
//  KemetFCTracker
//
//  STATUS: Compiled AND run with the real Swift 6.3.3 compiler on this
//  machine (`swiftc`, standalone executable — see FramingCalculator.swift's
//  header for why not `swift test`). This file imports only Foundation/
//  Combine, no iOS-only framework, so that's possible outside Xcode.
//  A comprehensive standalone test (continuity, never-switch-to-a-closer-
//  stranger, same-kit collision resolved by motion, genuine-ambiguity
//  flagging, scale-continuity rejection, and the full LOCKED ->
//  TEMPORARILY_LOST -> SEARCHING -> LOST -> reset() progression) found 3
//  genuine state-machine bugs on first run (11/14 passed): two related
//  timer bugs in handleMiss() that delayed the TEMPORARILY_LOST ->
//  SEARCHING transition, and a missing terminal-state guard that let a
//  matching detection silently "re-lock" the tracker even while
//  state == .lost, contradicting the spec's explicit re-selection
//  requirement. All 3 fixed; re-run result: 14/14 checks passed. See
//  Tests/PlayerTrackerTests.swift for the ported XCTest assertions.
//  NOT run on a device — see TrackTypes.swift header for the module-wide
//  caveat.
//
//  Maintains continuity for the ONE coach-selected player across frames
//  using non-biometric signals only (spec section 7/9): bounding-box IoU,
//  motion/velocity prediction, body-scale continuity, and a two-region
//  (torso + legs) color-based appearance signature. Never facial
//  recognition, never a database-wide search — this tracker only ever
//  compares candidates in THIS frame against the ONE track it already
//  has (spec sections 7, 13, 29).
//
//  Phase 2 rewrite of the appearance model and same-team collision
//  handling (spec sections 9-10): the Phase 1 signature was a single
//  whole-body mean color, too coarse to say anything useful about two
//  players in the same kit. This version separates torso (jersey) from
//  legs (shorts/socks) and, critically, DYNAMICALLY REWEIGHTS motion vs.
//  appearance based on how discriminative appearance actually is between
//  THIS frame's candidates — when two candidates look nearly identical
//  (same-team collision), appearance's influence is reduced and motion/
//  scale continuity dominate instead, rather than trusting a
//  non-discriminative signal at its normal weight.
//

import Combine
import Foundation

/// A coarse, explicitly non-biometric visual signature: mean color in
/// two body regions (torso ~ jersey, legs ~ shorts+socks combined).
/// Documented weights below, not learned — see PlayerTracker.update for
/// how discriminativeness across candidates additionally modulates how
/// much this signature is trusted per frame.
public struct AppearanceSignature: Sendable {
    public var torsoColor: (r: Double, g: Double, b: Double)
    public var legsColor: (r: Double, g: Double, b: Double)

    public init(torsoColor: (r: Double, g: Double, b: Double), legsColor: (r: Double, g: Double, b: Double)) {
        self.torsoColor = torsoColor
        self.legsColor = legsColor
    }

    /// Convenience for callers that only have one flat color (e.g. a
    /// quick placeholder before real per-region sampling is wired) —
    /// applies it to both regions so similarity() degrades gracefully
    /// rather than comparing garbage against garbage.
    public init(flatColor: (r: Double, g: Double, b: Double)) {
        self.torsoColor = flatColor
        self.legsColor = flatColor
    }

    private func colorDistance(_ a: (r: Double, g: Double, b: Double), _ b: (r: Double, g: Double, b: Double)) -> Double {
        let dr = a.r - b.r, dg = a.g - b.g, db = a.b - b.b
        return (dr * dr + dg * dg + db * db).squareRoot()
    }

    /// Weighted 0...1 similarity. Torso (jersey) is weighted higher than
    /// legs (0.6 / 0.4): the jersey is larger in frame at typical
    /// assessment framing distance and less prone to motion blur than
    /// legs during a sprint, so it's the more reliable of the two coarse
    /// signals — still both are just mean colors, not textures/patterns,
    /// so two players in identical kit will score ~1.0 here regardless.
    /// That weak-discriminative case is exactly why PlayerTracker.update
    /// additionally checks the SPREAD of this score across candidates
    /// before trusting it at full weight.
    public func similarity(to other: AppearanceSignature) -> Double {
        let maxDistance = 255 * 1.732
        let torsoSim = max(0, 1 - colorDistance(torsoColor, other.torsoColor) / maxDistance)
        let legsSim = max(0, 1 - colorDistance(legsColor, other.legsColor) / maxDistance)
        return 0.6 * torsoSim + 0.4 * legsSim
    }
}

@MainActor
public final class PlayerTracker: ObservableObject {
    /// Reported as `tracker_algorithm_version` at tracking-session-create
    /// time (spec section 14) — bump this whenever the matching/scoring
    /// algorithm changes materially (e.g. this Phase 2 two-region
    /// appearance + dynamic reweighting rewrite), so past sessions stay
    /// attributable to the exact algorithm that produced them.
    public static let algorithmVersion = "player-tracker-appearance-v2-dynamic-reweight"

    @Published public private(set) var state: PlayerTrackState = .lost
    @Published public private(set) var currentBoundingBox: NormalizedRect?
    @Published public private(set) var currentConfidence: Double = 0
    /// units/second, in normalized image-space — the ACTUAL signal
    /// FramingCalculator.applyLeadRoom expects (never predictedCenter(dt:)'s
    /// return value, a POSITION — see TrackingCoordinator's own note on
    /// this exact historical mistake).
    @Published public private(set) var currentVelocity: NormalizedPoint = .init(x: 0, y: 0)
    /// Set whenever update(...) had to resolve genuine ambiguity between
    /// two-or-more visually-similar candidates (spec section 10) — the
    /// UI/telemetry can surface this even when a match was still
    /// accepted, as an early warning the same-team-collision case is
    /// happening, before it ever escalates to an actual miss.
    @Published public private(set) var lastMatchWasAmbiguous = false

    public let targetTrackId: Int
    private var appearance: AppearanceSignature?
    private var lastKnownCenter: NormalizedPoint?
    private var lastKnownArea: Double?
    private var lastUpdateTime: TimeInterval = 0
    private var consecutiveMisses = 0
    /// When TEMPORARILY_LOST started — independent of searchingSince
    /// below, so maxTemporaryLossSeconds and maxSearchingSeconds each
    /// measure their OWN phase's duration, not a combined/confused one.
    /// (Phase 2 fix: an earlier version reused a single timestamp for
    /// both phases and set it lazily on the second miss rather than the
    /// first, so the TEMPORARILY_LOST->SEARCHING transition fired one
    /// full interval late — caught only by actually running the state
    /// machine, not by reading it; see the standalone executable
    /// referenced in this file's STATUS header.)
    private var lostSince: TimeInterval?
    private var searchingSince: TimeInterval?

    /// Tuning constants — documented, not learned. See spec section 13.
    private let iouMatchThreshold = 0.25
    private let maxTemporaryLossSeconds: TimeInterval = 1.5
    private let maxSearchingSeconds: TimeInterval = 6.0
    /// If the best and second-best candidate scores are closer than
    /// this, the match is "ambiguous" (spec section 10's same-team
    /// collision case) — appearance's weight is reduced and the
    /// acceptance bar is raised for that frame rather than guessing.
    private let ambiguityScoreGap = 0.08
    /// A same-person bounding box shouldn't change area by more than
    /// this factor between consecutive samples at typical assessment
    /// framing distance — a sudden much-larger/smaller box is itself
    /// evidence of a possible wrong-player lock (a different, closer or
    /// farther person), independent of position/appearance.
    private let maxPlausibleAreaRatioChange = 1.8

    public init(targetTrackId: Int, initialBoundingBox: NormalizedRect, appearance: AppearanceSignature) {
        self.targetTrackId = targetTrackId
        self.currentBoundingBox = initialBoundingBox
        self.appearance = appearance
        self.lastKnownCenter = initialBoundingBox.center
        self.lastKnownArea = initialBoundingBox.width * initialBoundingBox.height
        self.state = .locked
    }

    /// Called once per processed frame with EVERY person Vision detected
    /// (not just a pre-filtered "closest" one) — this tracker decides
    /// which, if any, is the SAME player, using motion-predicted
    /// position + IoU + scale continuity + appearance, with appearance's
    /// weight reduced when it fails to discriminate between candidates
    /// (spec section 10). Never picks "whoever is now closest to the
    /// camera" (spec section 6).
    public func update(detections: [PersonDetection], appearanceSignatures: [AppearanceSignature], at time: TimeInterval) {
        // LOST is terminal until an explicit reset(to:appearance:) — the
        // coach must tap to reselect (spec sections 6/13). Phase 1's
        // code had this as a documented INTENT ("requires an explicit
        // coach re-selection") in handleMiss's .lost case, but nothing
        // actually stopped update() itself from matching a detection and
        // silently re-locking anyway; a real test run caught this gap.
        guard state != .lost else { return }
        defer { lastUpdateTime = time }
        let dt = lastUpdateTime > 0 ? max(time - lastUpdateTime, 1.0 / 120.0) : 0

        let predictedCenter = predictedCenter(dt: dt)
        var candidates: [(index: Int, score: Double, appearanceScore: Double)] = []

        for (index, detection) in detections.enumerated() {
            guard index < appearanceSignatures.count else { continue }
            let candidateCenter = detection.boundingBox.center
            let distance = predictedCenter.distance(to: candidateCenter)
            // A candidate far outside plausible motion for this dt is
            // never matched, however good its IoU looks in isolation —
            // this is what stops an instantaneous "teleport" to a
            // different, similarly-dressed player crossing through.
            let maxPlausibleDistance = 0.05 + dt * 1.2
            guard distance <= maxPlausibleDistance else { continue }

            let iou = (currentBoundingBox ?? detection.boundingBox).iou(with: detection.boundingBox)
            let appearanceScore = appearance?.similarity(to: appearanceSignatures[index]) ?? 0.5
            let motionScore = 1 - min(distance / maxPlausibleDistance, 1)
            let scaleScore = scaleContinuityScore(for: detection.boundingBox)

            let score = combinedScore(
                motionScore: motionScore, iouScore: iou,
                appearanceScore: appearanceScore, scaleScore: scaleScore,
                allAppearanceScores: appearanceSignatures.indices.compactMap { i in
                    appearance?.similarity(to: appearanceSignatures[i])
                }
            )
            candidates.append((index, score, appearanceScore))
        }

        candidates.sort { $0.score > $1.score }

        guard let best = candidates.first, best.score > 0.3 else {
            lastMatchWasAmbiguous = false
            handleMiss(at: time)
            return
        }

        // Same-team collision guard (spec section 10): if the top two
        // candidates are nearly tied, this frame is genuinely ambiguous
        // — require a HIGHER bar to accept the top candidate than usual,
        // and if it doesn't clear that bar, prefer a miss (which can
        // still recover via TEMPORARILY_LOST/SEARCHING) over guessing.
        let isAmbiguous = candidates.count > 1 && (candidates[0].score - candidates[1].score) < ambiguityScoreGap
        lastMatchWasAmbiguous = isAmbiguous
        let acceptanceThreshold = isAmbiguous ? 0.62 : 0.3
        guard best.score >= acceptanceThreshold else {
            handleMiss(at: time)
            return
        }

        let matched = detections[best.index]
        if let iouOk = currentBoundingBox?.iou(with: matched.boundingBox), iouOk < iouMatchThreshold, best.score < 0.55 {
            // Weak match — treat as a miss rather than risk drifting
            // onto a neighboring player. Conservative on purpose.
            handleMiss(at: time)
            return
        }

        commit(detection: matched, appearance: appearanceSignatures[best.index], at: time, dt: dt)
    }

    /// Documented weighting (spec section 9: "Document weights and
    /// thresholds"). Base weights favor motion/IoU over appearance, as
    /// in Phase 1 — the new behavior is REDUCING appearance's weight
    /// further (redistributed to motion+scale) whenever appearance
    /// fails to discriminate between this frame's candidates, per spec
    /// section 10: "When appearance similarity is weakly discriminative,
    /// increase dependence on motion continuity... previous body scale."
    private func combinedScore(
        motionScore: Double, iouScore: Double, appearanceScore: Double, scaleScore: Double,
        allAppearanceScores: [Double]
    ) -> Double {
        let discriminative = appearanceIsDiscriminative(allAppearanceScores)
        let (motionWeight, iouWeight, appearanceWeight, scaleWeight): (Double, Double, Double, Double)
        if discriminative {
            (motionWeight, iouWeight, appearanceWeight, scaleWeight) = (0.40, 0.30, 0.20, 0.10)
        } else {
            // Appearance can't tell these candidates apart (e.g. same
            // team kit) — its weight is nearly zeroed and redistributed
            // to the three signals that don't depend on clothing color.
            (motionWeight, iouWeight, appearanceWeight, scaleWeight) = (0.50, 0.35, 0.05, 0.10)
        }
        return motionWeight * motionScore + iouWeight * iouScore + appearanceWeight * appearanceScore + scaleWeight * scaleScore
    }

    /// True when at least two candidates' appearance scores differ by
    /// enough to be USEFUL for telling them apart — false when every
    /// candidate looks about equally (dis)similar to the locked
    /// player's signature, which is exactly the same-team-kit case.
    private func appearanceIsDiscriminative(_ scores: [Double]) -> Bool {
        guard scores.count >= 2 else { return true }
        let sorted = scores.sorted(by: >)
        return (sorted[0] - sorted[1]) >= 0.15
    }

    private func scaleContinuityScore(for box: NormalizedRect) -> Double {
        guard let lastArea = lastKnownArea, lastArea > 0 else { return 1.0 }
        let area = box.width * box.height
        guard area > 0 else { return 0.0 }
        let ratio = max(area / lastArea, lastArea / area)
        guard ratio <= maxPlausibleAreaRatioChange else { return 0.0 }
        return 1 - (ratio - 1) / (maxPlausibleAreaRatioChange - 1)
    }

    private func commit(detection: PersonDetection, appearance newAppearance: AppearanceSignature, at time: TimeInterval, dt: TimeInterval) {
        if let previousCenter = lastKnownCenter, dt > 0 {
            let newCenter = detection.boundingBox.center
            currentVelocity = NormalizedPoint(
                x: (newCenter.x - previousCenter.x) / dt,
                y: (newCenter.y - previousCenter.y) / dt
            )
        }
        lastKnownCenter = detection.boundingBox.center
        lastKnownArea = detection.boundingBox.width * detection.boundingBox.height
        currentBoundingBox = detection.boundingBox
        currentConfidence = detection.confidence
        consecutiveMisses = 0
        searchingSince = nil

        // Slow exponential update — a single odd-lighting frame should
        // never redefine "what this player looks like." Never updated
        // at all on an AMBIGUOUS frame — averaging in a possibly-wrong
        // match's appearance would compound the risk on the next frame.
        if !lastMatchWasAmbiguous {
            if let existing = appearance {
                appearance = AppearanceSignature(
                    torsoColor: (
                        r: existing.torsoColor.r * 0.9 + newAppearance.torsoColor.r * 0.1,
                        g: existing.torsoColor.g * 0.9 + newAppearance.torsoColor.g * 0.1,
                        b: existing.torsoColor.b * 0.9 + newAppearance.torsoColor.b * 0.1
                    ),
                    legsColor: (
                        r: existing.legsColor.r * 0.9 + newAppearance.legsColor.r * 0.1,
                        g: existing.legsColor.g * 0.9 + newAppearance.legsColor.g * 0.1,
                        b: existing.legsColor.b * 0.9 + newAppearance.legsColor.b * 0.1
                    )
                )
            } else {
                appearance = newAppearance
            }
        }

        let previousState = state
        state = previousState == .locked ? .locked : .reacquired
    }

    private func handleMiss(at time: TimeInterval) {
        consecutiveMisses += 1
        currentConfidence = 0

        switch state {
        case .locked, .reacquired:
            state = .temporarilyLost
            lostSince = time
            searchingSince = nil
        case .temporarilyLost:
            if let since = lostSince, time - since > maxTemporaryLossSeconds {
                state = .searching
                searchingSince = time
            }
        case .searching:
            if let since = searchingSince, time - since > maxSearchingSeconds {
                state = .lost
                // Gimbal motor correction must stop once we're this
                // uncertain (spec section 13) — GimbalController.
                // pauseMotorCorrection() is called by TrackingCoordinator
                // when it observes this transition, not from here, to
                // keep this class free of DockKit dependencies.
            }
        case .lost:
            break // requires an explicit coach re-selection — see spec section 6/13
        }
    }

    /// Dead-reckoning position estimate used both for re-acquisition
    /// matching above and, during TEMPORARILY_LOST, to keep the gimbal
    /// gently following the player's last known heading rather than
    /// freezing or searching wildly (spec section 13: "During short
    /// occlusions, use predicted motion").
    public func predictedCenter(dt: TimeInterval) -> NormalizedPoint {
        guard let center = lastKnownCenter else { return NormalizedPoint(x: 0.5, y: 0.5) }
        guard state == .temporarilyLost || state == .searching else { return center }
        return NormalizedPoint(
            x: center.x + currentVelocity.x * dt,
            y: center.y + currentVelocity.y * dt
        )
    }

    /// Manual "Re-select Player" (spec section 25) — the ONLY way out of
    /// .lost. Never automatic.
    public func reset(to boundingBox: NormalizedRect, appearance newAppearance: AppearanceSignature) {
        currentBoundingBox = boundingBox
        appearance = newAppearance
        lastKnownCenter = boundingBox.center
        lastKnownArea = boundingBox.width * boundingBox.height
        currentVelocity = .init(x: 0, y: 0)
        consecutiveMisses = 0
        lostSince = nil
        searchingSince = nil
        lastMatchWasAmbiguous = false
        state = .locked
    }
}
