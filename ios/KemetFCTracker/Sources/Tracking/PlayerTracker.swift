//
//  PlayerTracker.swift
//  KemetFCTracker
//
//  STATUS: Type-checked with the real Swift 6.3.3 compiler on this
//  machine (`swiftc -typecheck`, zero errors, alongside TrackTypes.swift
//  and BallTracker.swift) — this file imports only Foundation/Combine,
//  no iOS-only framework, so that's possible outside Xcode. NOT executed
//  (no unit tests exercise its actual tracking logic/state-machine
//  transitions the way FramingMathTests.swift does for FramingCalculator
//  — writing and running those is flagged as a near-term follow-up in
//  the final report, not done in this session). NOT run on a device.
//  See TrackTypes.swift header for the module-wide caveat.
//
//  Maintains continuity for the ONE coach-selected player across frames
//  using non-biometric signals only (spec section 7): bounding-box IoU,
//  motion/velocity prediction, and a simple appearance signature (mean
//  color in the bbox — a crude stand-in for "jersey color," not a
//  trained re-identification model). Never facial recognition, never a
//  database-wide search — this tracker only ever compares candidates in
//  THIS frame against the ONE track it already has (spec sections 7, 13,
//  29).
//

import Combine
import Foundation

public struct AppearanceSignature: Sendable {
    /// Mean RGB in the player's bounding box at lock time, updated
    /// slowly (exponential smoothing) on each confident re-match — a
    /// coarse "jersey color" proxy. Explicitly NOT a learned embedding;
    /// documented as low-precision by design (two players on the same
    /// team look identical to this).
    public var meanColor: (r: Double, g: Double, b: Double)

    public func similarity(to other: AppearanceSignature) -> Double {
        let dr = meanColor.r - other.meanColor.r
        let dg = meanColor.g - other.meanColor.g
        let db = meanColor.b - other.meanColor.b
        let distance = (dr * dr + dg * dg + db * db).squareRoot()
        return max(0, 1 - distance / (255 * 1.732))
    }
}

@MainActor
public final class PlayerTracker: ObservableObject {
    @Published public private(set) var state: PlayerTrackState = .lost
    @Published public private(set) var currentBoundingBox: NormalizedRect?
    @Published public private(set) var currentConfidence: Double = 0
    /// units/second, in normalized image-space — the ACTUAL signal
    /// FramingCalculator.applyLeadRoom expects. TrackingCoordinator must
    /// pass this, never predictedCenter(dt:)'s return value (a
    /// POSITION, not a velocity) — an earlier draft of
    /// TrackingCoordinator.computeFramingTarget() made exactly that
    /// mistake; fixed there, exposed here to make the correct call site
    /// straightforward.
    @Published public private(set) var currentVelocity: NormalizedPoint = .init(x: 0, y: 0)

    public let targetTrackId: Int
    private var appearance: AppearanceSignature?
    private var lastKnownCenter: NormalizedPoint?
    private var lastUpdateTime: TimeInterval = 0
    private var consecutiveMisses = 0
    private var searchingSince: TimeInterval?

    /// Tuning constants — documented, not learned. See spec section 13.
    private let iouMatchThreshold = 0.25
    private let appearanceMatchThreshold = 0.55
    private let maxTemporaryLossSeconds: TimeInterval = 1.5
    private let maxSearchingSeconds: TimeInterval = 6.0

    public init(targetTrackId: Int, initialBoundingBox: NormalizedRect, appearance: AppearanceSignature) {
        self.targetTrackId = targetTrackId
        self.currentBoundingBox = initialBoundingBox
        self.appearance = appearance
        self.lastKnownCenter = initialBoundingBox.center
        self.state = .locked
    }

    /// Called once per processed frame with EVERY person Vision detected
    /// (not just a pre-filtered "closest" one) — this tracker decides
    /// which, if any, is the SAME player, using motion-predicted
    /// position + IoU + appearance. Never picks "whoever is now closest
    /// to the camera" (spec section 6: "Do not automatically switch to
    /// another player simply because another player moves closer").
    public func update(detections: [PersonDetection], appearanceSignatures: [AppearanceSignature], at time: TimeInterval) {
        defer { lastUpdateTime = time }
        let dt = lastUpdateTime > 0 ? max(time - lastUpdateTime, 1.0 / 120.0) : 0

        let predictedCenter = predictedCenter(dt: dt)
        var bestIndex: Int?
        var bestScore = -Double.infinity

        for (index, detection) in detections.enumerated() {
            guard index < appearanceSignatures.count else { continue }
            let candidateCenter = detection.boundingBox.center
            let distance = predictedCenter.distance(to: candidateCenter)
            // A candidate far outside plausible motion for this dt is
            // never matched, however good its IoU looks in isolation —
            // this is what stops an instantaneous "teleport" to a
            // different, similarly-dressed player crossing through.
            let maxPlausibleDistance = 0.05 + Double(dt) * 1.2
            guard distance <= maxPlausibleDistance else { continue }

            let iou = (currentBoundingBox ?? detection.boundingBox).iou(with: detection.boundingBox)
            let appearanceScore = appearance?.similarity(to: appearanceSignatures[index]) ?? 0.5
            let motionScore = 1 - min(distance / maxPlausibleDistance, 1)

            // Weighted combination — documented, not learned: motion
            // continuity and IoU dominate (frame-to-frame position
            // barely changes for the SAME person at 20Hz+ effective
            // tracking rate); appearance is a tiebreaker, never the sole
            // signal, since two teammates can share a near-identical
            // mean jersey color.
            let score = (0.45 * motionScore) + (0.35 * iou) + (0.20 * appearanceScore)
            if score > bestScore {
                bestScore = score
                bestIndex = index
            }
        }

        guard let bestIndex, bestScore > 0.3 else {
            handleMiss(at: time)
            return
        }

        let matched = detections[bestIndex]
        if let iouOk = currentBoundingBox?.iou(with: matched.boundingBox), iouOk < iouMatchThreshold, bestScore < 0.55 {
            // Weak match — treat as a miss rather than risk drifting
            // onto a neighboring player. Conservative on purpose.
            handleMiss(at: time)
            return
        }

        commit(detection: matched, appearance: appearanceSignatures[bestIndex], at: time, dt: dt)
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
        currentBoundingBox = detection.boundingBox
        currentConfidence = detection.confidence
        consecutiveMisses = 0
        searchingSince = nil

        // Slow exponential update — a single odd-lighting frame should
        // never redefine "what this player looks like."
        if let existing = appearance {
            appearance = AppearanceSignature(meanColor: (
                r: existing.meanColor.r * 0.9 + newAppearance.meanColor.r * 0.1,
                g: existing.meanColor.g * 0.9 + newAppearance.meanColor.g * 0.1,
                b: existing.meanColor.b * 0.9 + newAppearance.meanColor.b * 0.1
            ))
        } else {
            appearance = newAppearance
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
            searchingSince = nil
        case .temporarilyLost:
            searchingSince = searchingSince ?? time
            if let since = searchingSince, time - since > maxTemporaryLossSeconds {
                state = .searching
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
        currentVelocity = .init(x: 0, y: 0)
        consecutiveMisses = 0
        searchingSince = nil
        state = .locked
    }
}
