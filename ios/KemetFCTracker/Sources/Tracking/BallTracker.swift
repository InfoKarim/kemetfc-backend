//
//  BallTracker.swift
//  KemetFCTracker
//
//  Temporal association for the ball. DETECTION answers "where might a ball
//  be in this frame?"; TRACKING answers "which detection is the same ball as
//  before?". This file is only the second one — and it is deliberately
//  skeptical, because the only detector that exists today is the classical
//  bright-blob heuristic (cones, chalk, socks and white kit all look like
//  balls to it). Nothing here makes a detector better than it is.
//
//  Guarantees (all covered by BallTrackerTests):
//    * DETECTED vs NOT DETECTED are distinct. `currentBoundingBox` is non-nil
//      only when a ball was detected THIS frame; a stale position is never
//      reported as a detection. `lastKnownBoundingBox` exists separately, for
//      framing continuity only.
//    * A new track needs `acquisitionConfirmFrames` consistent detections in a
//      row — one bright flicker never becomes a ball.
//    * A candidate that would need a physically implausible speed to be the
//      same ball is rejected, not followed. The plausibility gate grows with
//      the time since the last trusted observation, bounded by the search window.
//    * Two competing candidates that the motion evidence cannot separate yield
//      NO detection for that frame (never a guess).
//    * `currentTrackId` is the tracker's real identity: it stays the same while
//      the ball is followed (including short dropouts) and changes when a new
//      track starts after the ball was lost. It is nil whenever the ball is not
//      detected. It is never a hard-coded constant.
//    * A bright object that sits still at the same spot next to a ball that is being
//      followed (a cone, chalk, a sock) is remembered as a decoy and ignored, so it
//      cannot be adopted as the ball when the real ball drops out. A decoy that first
//      appears AFTER the ball vanished cannot be told from the ball and is subject
//      only to the plausibility gate and to confirmation as a new track.
//    * Detection confidence is preserved as reported; below `minimumConfidence`
//      a detection is ignored.
//

import Combine
import Foundation

@MainActor
public final class BallTracker: ObservableObject {
    public static let algorithmVersion = "ball-tracker-v2-confirmed-acquisition"

    @Published public private(set) var state: BallTrackState = .lost
    /// The ball detected THIS frame — nil when it was not detected.
    @Published public private(set) var currentBoundingBox: NormalizedRect?
    @Published public private(set) var currentConfidence: Double = 0
    /// Identity of the followed ball, nil whenever the ball is not detected this frame.
    @Published public private(set) var currentTrackId: Int?
    /// Read-only mirror of the private `velocity` below — same normalized
    /// image-space units/second, same EMA smoothing, same reset/hold
    /// behavior. Exists only so a downstream consumer (Player Analysis) can
    /// read it; nothing here can feed back into or alter BallTracker's own
    /// matching/prediction, which reads only the private `velocity`.
    @Published public private(set) var currentVelocity = NormalizedPoint(x: 0, y: 0)

    /// Where the ball was last actually detected (nil after the track is lost).
    /// For framing continuity only — NOT a detection.
    public private(set) var lastKnownBoundingBox: NormalizedRect?
    public private(set) var lastObservedAt: TimeInterval?
    /// Confidence of that last detection, as the detector reported it.
    public private(set) var lastObservedConfidence: Double = 0

    /// Number of ball tracks acquired so far in this tracker's life.
    public private(set) var acquiredTrackCount = 0
    private var activeTrackId: Int?

    private var velocity = NormalizedPoint(x: 0, y: 0)
    private struct Pending {
        var center: NormalizedPoint
        var time: TimeInterval
        var count: Int
        var detection: BallDetection
    }
    private var pending: Pending?

    private struct Decoy {
        var center: NormalizedPoint
        var seenFrames: Int
        var lastSeen: TimeInterval
    }
    private var decoys: [Decoy] = []

    // Tuning (documented, not learned). Normalized image units per second.
    /// Fastest plausible ball, including a hard shot across a wide frame.
    private let maxPlausibleSpeedUnitsPerSecond = 2.5
    /// Slack for detection jitter around the predicted position.
    private let baseGate = 0.03
    /// How long a track survives without a detection before it is lost.
    private let maxSearchSeconds: TimeInterval = 2.0
    /// A ball changes direction constantly: extrapolate its velocity only briefly.
    private let predictionCapSeconds: TimeInterval = 0.25
    /// Detections below this confidence are ignored entirely.
    private let minimumConfidence = 0.15
    /// Consecutive consistent detections required to start a new track.
    private let acquisitionConfirmFrames = 2
    /// Two candidates whose distances to the prediction differ by less than
    /// this cannot be told apart by motion.
    private let ambiguityDistanceMargin = 0.02
    /// Between two acquisition candidates, the more confident one must lead by this.
    private let acquisitionConfidenceMargin = 0.10
    private let velocitySmoothing = 0.5
    /// A non-selected candidate that stays within this distance of the same spot is 'the same object'.
    private let decoyRadius = 0.012
    /// Frames a candidate must sit still (beside a followed ball) to count as a decoy.
    private let decoyMinFrames = 5
    private let decoyExpirySeconds: TimeInterval = 3.0

    public init() {}

    public func update(detections: [BallDetection], at time: TimeInterval) {
        let confident = detections.filter { $0.confidence >= minimumConfidence }
        let usable = confident.filter { !isKnownDecoy($0) }

        if let last = lastKnownBoundingBox, let observedAt = lastObservedAt, state != .lost {
            followExistingTrack(usable: usable, last: last, observedAt: observedAt, time: time)
        } else {
            acquireNewTrack(usable: usable, time: time)
        }
    }

    /// The last detected box if it is at most `maxAge` seconds old — for framing
    /// continuity across a one-frame dropout. Never use it as telemetry.
    public func heldBoundingBox(at time: TimeInterval, maxAge: TimeInterval = 0.4) -> NormalizedRect? {
        guard let box = lastKnownBoundingBox, let observedAt = lastObservedAt, time - observedAt <= maxAge else { return nil }
        return box
    }

    // MARK: - Following an existing track

    private func followExistingTrack(usable: [BallDetection], last: NormalizedRect, observedAt: TimeInterval, time: TimeInterval) {
        let elapsed = max(time - observedAt, 0)
        if elapsed > maxSearchSeconds {
            loseTrack()
            acquireNewTrack(usable: usable, time: time)
            return
        }

        let lookahead = min(elapsed, predictionCapSeconds)
        let predicted = NormalizedPoint(x: last.center.x + velocity.x * lookahead, y: last.center.y + velocity.y * lookahead)
        let gate = baseGate + maxPlausibleSpeedUnitsPerSecond * elapsed

        let plausible = usable
            .map { (detection: $0, distance: predicted.distance(to: $0.boundingBox.center)) }
            .filter { $0.distance <= gate }
            .sorted { $0.distance < $1.distance }

        guard let best = plausible.first else {   // includes impossible jumps
            noteDecoys(among: usable, chosen: nil, at: time)
            return markMissed()
        }
        if plausible.count > 1, plausible[1].distance - best.distance < ambiguityDistanceMargin {
            return markMissed()   // two look-alikes the motion cannot separate: report nothing
        }
        noteDecoys(among: usable, chosen: best.detection, at: time)
        record(best.detection, at: time, previous: last.center, elapsed: elapsed)
    }

    // MARK: - Acquiring a new track (needs confirmation)

    private func acquireNewTrack(usable: [BallDetection], time: TimeInterval) {
        guard let candidate = unambiguousAcquisitionCandidate(usable) else {
            pending = nil
            return markMissed()
        }

        if var current = pending {
            let dt = max(time - current.time, 1.0 / 120.0)
            let reach = baseGate + maxPlausibleSpeedUnitsPerSecond * dt
            if current.center.distance(to: candidate.boundingBox.center) <= reach {
                current.count += 1
                current.center = candidate.boundingBox.center
                current.time = time
                current.detection = candidate
                pending = current
            } else {
                pending = Pending(center: candidate.boundingBox.center, time: time, count: 1, detection: candidate)
            }
        } else {
            pending = Pending(center: candidate.boundingBox.center, time: time, count: 1, detection: candidate)
        }

        guard let confirmed = pending, confirmed.count >= acquisitionConfirmFrames else {
            return markMissed()   // not yet a ball: still "not detected"
        }
        pending = nil
        acquiredTrackCount += 1
        activeTrackId = acquiredTrackCount
        velocity = NormalizedPoint(x: 0, y: 0)
        currentVelocity = velocity
        record(confirmed.detection, at: time, previous: nil, elapsed: 0)
    }

    private func unambiguousAcquisitionCandidate(_ usable: [BallDetection]) -> BallDetection? {
        let ranked = usable.sorted { $0.confidence > $1.confidence }
        guard let first = ranked.first else { return nil }
        if ranked.count > 1, first.confidence - ranked[1].confidence < acquisitionConfidenceMargin {
            // Several equally convincing bright circles and no motion history:
            // do not pick one.
            return nil
        }
        return first
    }

    // MARK: - Decoys (static look-alikes beside a followed ball)

    private func isKnownDecoy(_ detection: BallDetection) -> Bool {
        decoys.contains { $0.seenFrames >= decoyMinFrames && $0.center.distance(to: detection.boundingBox.center) <= decoyRadius }
    }

    /// Candidates that were not the followed ball and keep appearing at the same
    /// spot. Only learned while a track is being followed (there is then a moving
    /// reference to be different from).
    private func noteDecoys(among candidates: [BallDetection], chosen: BallDetection?, at time: TimeInterval) {
        decoys.removeAll { time - $0.lastSeen > decoyExpirySeconds }
        for candidate in candidates {
            if let chosen, candidate.boundingBox == chosen.boundingBox { continue }
            let center = candidate.boundingBox.center
            if let index = decoys.firstIndex(where: { $0.center.distance(to: center) <= decoyRadius }) {
                decoys[index].seenFrames += 1
                decoys[index].lastSeen = time
            } else {
                decoys.append(Decoy(center: center, seenFrames: 1, lastSeen: time))
            }
        }
    }

    // MARK: - Bookkeeping

    private func record(_ detection: BallDetection, at time: TimeInterval, previous: NormalizedPoint?, elapsed: TimeInterval) {
        if let previous, elapsed > 0 {
            let measured = NormalizedPoint(
                x: (detection.boundingBox.center.x - previous.x) / elapsed,
                y: (detection.boundingBox.center.y - previous.y) / elapsed
            )
            velocity = NormalizedPoint(
                x: velocity.x * (1 - velocitySmoothing) + measured.x * velocitySmoothing,
                y: velocity.y * (1 - velocitySmoothing) + measured.y * velocitySmoothing
            )
            currentVelocity = velocity
        }
        lastKnownBoundingBox = detection.boundingBox
        lastObservedAt = time
        lastObservedConfidence = detection.confidence
        currentBoundingBox = detection.boundingBox
        currentConfidence = detection.confidence
        currentTrackId = activeTrackId
        state = .tracked
    }

    /// The ball was not detected this frame. The search window is enforced on the
    /// next update, against the caller's clock.
    private func markMissed() {
        currentBoundingBox = nil
        currentConfidence = 0
        currentTrackId = nil
        if state == .tracked { state = .searching }
    }

    private func loseTrack() {
        state = .lost
        lastKnownBoundingBox = nil
        lastObservedAt = nil
        lastObservedConfidence = 0
        activeTrackId = nil
        velocity = NormalizedPoint(x: 0, y: 0)
        currentVelocity = velocity
        currentBoundingBox = nil
        currentConfidence = 0
        currentTrackId = nil
    }
}
