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
    public static let algorithmVersion = "player-tracker-v4-motion-reacquisition"

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

    // Identity-safety tuning (v3): losing the target briefly is always
    // preferred over committing to another child. Documented, not learned.
    /// A detection below this is not a candidate at all — a 0.15-confidence
    /// ghost near the predicted spot must never become the selected player.
    private let minDetectionConfidence = 0.30
    /// Another person within this many body-widths of the target is a
    /// "rival": the tracker can no longer prove identity by proximity alone
    /// if the rival (or the target) then drops out of the detections.
    private let contestRadiusScale = 1.5
    /// Two people closer than this many body-widths, with indistinguishable
    /// appearance, cannot be told apart by position — flagged ambiguous even
    /// when one scores slightly higher.
    private let confusableDistanceScale = 0.5
    private let appearanceDiscriminationGap = 0.15
    /// A committed-while-ambiguous match must at least be an unbroken
    /// frame-to-frame continuation of the previous box.
    private let ambiguousCommitMinIoU = 0.5
    /// After a contested frame, a lone surviving detection is accepted only
    /// if it is at least this much nearer the target's last position than
    /// the rival's (ratio of distances).
    private let survivorNearnessRatio = 0.5

    // Motion prediction + safe reacquisition tuning (v4). Prediction is
    // SEARCH GUIDANCE ONLY: it decides where to look and how far to look,
    // never whether a candidate IS the target.
    /// How far the constant-velocity prediction is trusted, in seconds since
    /// the last trusted observation — the existing temporary-loss window.
    /// Past it the predicted position stops advancing (never extrapolated
    /// indefinitely) and identity trust does not grow.
    private var maxPredictionHorizonSeconds: TimeInterval { maxTemporaryLossSeconds }
    /// While the target is missing, a candidate must fall within this many
    /// body-widths of the predicted position...
    private let predictionBaseRadiusScale = 0.5
    /// ...plus this many body-widths per second of elapsed time (capped at
    /// the horizon): the search area grows carefully as the prediction ages.
    private let predictionRadiusGrowthScale = 0.25
    /// A detection must be at least this confident to be a REACQUISITION
    /// candidate (stricter than the general floor above): re-establishing a
    /// lost identity needs a clearer observation than following it.
    private let reacquisitionMinConfidence = 0.50
    /// Minimum composite score to reacquire (ordinary tracking only needs 0.3).
    private let reacquisitionMinScore = 0.55
    /// A reacquisition candidate must persist and move consistently with the
    /// target's last trusted velocity for this many processed frames before
    /// it is committed — one frame on the predicted path is not identity.
    private let reacquisitionConfirmFrames = 3
    private let reacquisitionProbationMaxGapSeconds: TimeInterval = 0.5
    /// Allowed difference between a candidate's own velocity and the last
    /// trusted velocity: this many body-widths/second plus this fraction
    /// of the trusted speed.
    private let velocityToleranceScale = 1.5
    private let velocityToleranceFraction = 0.3
    /// Exponential smoothing of the trusted velocity (weight of the newest sample).
    private let velocitySmoothing = 0.3

    /// The nearest other person within contestRadius at the last commit,
    /// or nil if the target was alone. Non-nil while state is temporarily
    /// lost / searching means the loss happened in a contested situation.
    private var rivalBox: NormalizedRect?
    /// A cheap constant-velocity estimate of that rival, from consecutive
    /// commits only (nil until two samples exist) — negative evidence:
    /// "this candidate fits the rival better than the target".
    private var rivalVelocity: NormalizedPoint?
    private var rivalSeenAt: TimeInterval?
    /// Time of the last TRUSTED observation (a committed detection).
    private var lastCommitTime: TimeInterval?
    /// Velocity smoothed from committed, unambiguous, frame-to-frame
    /// observations only. Never fed by rejected, ambiguous, low-confidence
    /// or predicted positions. Drives the search prediction while lost.
    private var trustedVelocity: NormalizedPoint?

    private struct PendingReacquisition {
        var startCenter: NormalizedPoint
        var startTime: TimeInterval
        var lastTime: TimeInterval
        var count: Int
    }
    private var pendingReacquisition: PendingReacquisition?
    /// Width of the box the coach locked — a stable scale for the radii
    /// above even when a partially occluded box shrinks.
    private var referenceWidth: Double

    public init(targetTrackId: Int, initialBoundingBox: NormalizedRect, appearance: AppearanceSignature) {
        self.targetTrackId = targetTrackId
        self.currentBoundingBox = initialBoundingBox
        self.appearance = appearance
        self.lastKnownCenter = initialBoundingBox.center
        self.lastKnownArea = initialBoundingBox.width * initialBoundingBox.height
        self.referenceWidth = initialBoundingBox.width
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

        // v4: while the target is missing, the prediction advances with the
        // time since the LAST TRUSTED observation (not one frame's dt), and
        // the search radius is a bounded, slowly growing function of that
        // same elapsed time. The predicted position is never stored as an
        // observation — lastKnownCenter/currentBoundingBox only change in commit().
        let isMissing = state == .temporarilyLost || state == .searching
        let elapsed = max(time - (lastCommitTime ?? time), 0)
        let predictedCenter = predictedCenter(dt: isMissing ? elapsed : dt)
        let searchRadius = isMissing ? reacquisitionRadius(elapsed: elapsed) : plausibleStepDistance(dt: dt)
        let minimumConfidence = isMissing ? reacquisitionMinConfidence : minDetectionConfidence
        // Where the last trusted box would be if the target kept moving
        // (search guidance for IoU only — not an observation).
        let referenceBox: NormalizedRect? = currentBoundingBox.map { box in
            isMissing
                ? NormalizedRect(x: predictedCenter.x - box.width / 2, y: predictedCenter.y - box.height / 2, width: box.width, height: box.height)
                : box
        }
        var candidates: [(index: Int, score: Double, appearanceScore: Double)] = []

        for (index, detection) in detections.enumerated() {
            guard index < appearanceSignatures.count else { continue }
            guard detection.confidence >= minimumConfidence else { continue }
            let candidateCenter = detection.boundingBox.center
            let distance = predictedCenter.distance(to: candidateCenter)
            // A candidate far outside plausible motion for this dt is
            // never matched, however good its IoU looks in isolation —
            // this is what stops an instantaneous "teleport" to a
            // different, similarly-dressed player crossing through.
            let maxPlausibleDistance = searchRadius
            guard distance <= maxPlausibleDistance else { continue }

            let iou = (referenceBox ?? detection.boundingBox).iou(with: detection.boundingBox)
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
            pendingReacquisition = nil
            handleMiss(at: time)
            return
        }

        // Same-team collision guard (spec section 10): if the top two
        // candidates are nearly tied, this frame is genuinely ambiguous
        // — require a HIGHER bar to accept the top candidate than usual,
        // and if it doesn't clear that bar, prefer a miss (which can
        // still recover via TEMPORARILY_LOST/SEARCHING) over guessing.
        let scoreAmbiguous = candidates.count > 1 && (candidates[0].score - candidates[1].score) < ambiguityScoreGap
        // v3: a near-tie in SCORE is not the only kind of ambiguity — two
        // same-looking people standing within half a body-width of each
        // other cannot be separated by position, however the scores fall.
        let isAmbiguous = scoreAmbiguous || hasConfusableNeighbor(
            of: best.index, detections: detections, appearanceSignatures: appearanceSignatures
        )
        lastMatchWasAmbiguous = isAmbiguous
        let acceptanceThreshold = isAmbiguous ? 0.62 : 0.3
        guard best.score >= acceptanceThreshold else {
            pendingReacquisition = nil
            handleMiss(at: time)
            return
        }

        let matched = detections[best.index]

        if isAmbiguous {
            // Identity is not provable this frame. Never (re)acquire on an
            // ambiguous frame; an already-locked track may only continue
            // if this is an unbroken continuation of the previous box.
            let continuous = (currentBoundingBox?.iou(with: matched.boundingBox) ?? 0) >= ambiguousCommitMinIoU
            if isMissing || !continuous {
                pendingReacquisition = nil
                handleMiss(at: time)
                return
            }
        }

        if isMissing {
            // Reacquisition needs stronger evidence than ordinary tracking
            // (see reacquisitionVerdict). Prediction only chose WHERE to look.
            switch reacquisitionVerdict(
                for: matched, score: best.score, hasCompetitor: candidates.count > 1, elapsed: elapsed, at: time
            ) {
            case .accept:
                break
            case .confirming:
                handleMiss(at: time)   // still not locked: the timers keep running
                return
            case .refuse:
                pendingReacquisition = nil
                handleMiss(at: time)
                return
            case .refuseAmbiguous:
                lastMatchWasAmbiguous = true
                pendingReacquisition = nil
                handleMiss(at: time)
                return
            }
        } else if survivorIsUnverifiable(matchedIndex: best.index, detections: detections) {
            // The person who was next to the target has just vanished from
            // the detections, and the lone survivor is not clearly the
            // target — it may be the rival. Lose the target, don't guess.
            lastMatchWasAmbiguous = true
            handleMiss(at: time)
            return
        }

        if let iouOk = referenceBox?.iou(with: matched.boundingBox), iouOk < iouMatchThreshold, best.score < 0.55 {
            // Weak match — treat as a miss rather than risk drifting
            // onto a neighboring player. Conservative on purpose.
            pendingReacquisition = nil
            handleMiss(at: time)
            return
        }

        commit(
            detection: matched, appearance: appearanceSignatures[best.index], at: time, dt: dt,
            rival: nearestRival(of: best.index, detections: detections),
            isReacquisition: isMissing
        )
    }

    // MARK: - Motion prediction + reacquisition (v4)

    /// The per-frame plausibility gate used for ordinary (not-missing) tracking.
    private func plausibleStepDistance(dt: TimeInterval) -> Double { 0.05 + dt * 1.2 }

    /// Body-relative search radius while the target is missing: a base
    /// tolerance that grows slowly with the (horizon-capped) elapsed time.
    /// Growth widens WHERE we look; it never lowers the evidence required.
    private func reacquisitionRadius(elapsed: TimeInterval) -> Double {
        let width = max(referenceWidth, currentBoundingBox?.width ?? 0)
        let cappedElapsed = min(elapsed, maxPredictionHorizonSeconds)
        return width * (predictionBaseRadiusScale + predictionRadiusGrowthScale * cappedElapsed)
    }

    /// Past the horizon the prediction is only meaningful for a target that
    /// was (nearly) stationary: a moving target's position after this long
    /// is unknowable, so proximity to a stale extrapolation proves nothing.
    private func predictionIsTrustworthy(elapsed: TimeInterval) -> Bool {
        if elapsed <= maxPredictionHorizonSeconds { return true }
        let speed = trustedVelocity.map { ($0.x * $0.x + $0.y * $0.y).squareRoot() } ?? 0
        let width = max(referenceWidth, currentBoundingBox?.width ?? 0)
        return speed * maxPredictionHorizonSeconds <= predictionBaseRadiusScale * width
    }

    private enum ReacquisitionVerdict { case accept, confirming, refuse, refuseAmbiguous }

    /// Everything a returning candidate must satisfy before it becomes the
    /// selected player again. Prediction alone never suffices: it only got
    /// the candidate INTO this function.
    private func reacquisitionVerdict(
        for matched: PersonDetection, score: Double, hasCompetitor: Bool, elapsed: TimeInterval, at time: TimeInterval
    ) -> ReacquisitionVerdict {
        let center = matched.boundingBox.center
        // Bounded horizon: an aged prediction of a moving target is not evidence.
        guard predictionIsTrustworthy(elapsed: elapsed) else { return .refuse }
        // Competition: another plausible candidate near the predicted spot
        // means position cannot say which one is the target.
        guard !hasCompetitor else { return .refuseAmbiguous }
        guard score >= reacquisitionMinScore else { return .refuse }

        // Rival history (Fix #1): a loss that began next to another person is
        // recovered only when the candidate fits the target's trajectory
        // clearly better than the rival's — otherwise it may BE the rival.
        if let rival = rivalBox {
            guard let rivalV = rivalVelocity, let seenAt = rivalSeenAt else { return .refuseAmbiguous }
            let rivalElapsed = min(max(time - seenAt, 0), maxPredictionHorizonSeconds)
            let rivalPredicted = NormalizedPoint(
                x: rival.center.x + rivalV.x * rivalElapsed, y: rival.center.y + rivalV.y * rivalElapsed
            )
            let targetPredicted = predictedCenter(dt: elapsed)
            guard center.distance(to: targetPredicted) < survivorNearnessRatio * center.distance(to: rivalPredicted) else {
                return .refuseAmbiguous
            }
        }

        // Probation: the candidate must persist for reacquisitionConfirmFrames
        // consecutive frames AND move like the target did (trusted velocity).
        // A stranger who merely stands near the predicted spot fails the second test.
        guard var pending = pendingReacquisition, time - pending.lastTime <= reacquisitionProbationMaxGapSeconds,
              time > pending.lastTime else {
            pendingReacquisition = PendingReacquisition(startCenter: center, startTime: time, lastTime: time, count: 1)
            return .confirming
        }
        let span = time - pending.startTime
        let candidateVelocity = NormalizedPoint(
            x: (center.x - pending.startCenter.x) / span, y: (center.y - pending.startCenter.y) / span
        )
        let trusted = trustedVelocity ?? NormalizedPoint(x: 0, y: 0)
        let trustedSpeed = (trusted.x * trusted.x + trusted.y * trusted.y).squareRoot()
        let width = max(referenceWidth, currentBoundingBox?.width ?? 0)
        let tolerance = velocityToleranceScale * width + velocityToleranceFraction * trustedSpeed
        pending.count += 1
        pending.lastTime = time
        if pending.count >= reacquisitionConfirmFrames {
            guard candidateVelocity.distance(to: trusted) <= tolerance else {
                pendingReacquisition = PendingReacquisition(startCenter: center, startTime: time, lastTime: time, count: 1)
                return .refuse
            }
            pendingReacquisition = nil
            return .accept
        }
        pendingReacquisition = pending
        return .confirming
    }

    // MARK: - Identity-safety helpers (v3)

    private func contestRadius(forWidth width: Double) -> Double {
        contestRadiusScale * max(referenceWidth, width)
    }

    /// Another confident detection standing near enough to the chosen one,
    /// looking similar enough to the locked player, that position cannot
    /// separate them.
    private func hasConfusableNeighbor(
        of index: Int, detections: [PersonDetection], appearanceSignatures: [AppearanceSignature]
    ) -> Bool {
        let chosen = detections[index].boundingBox
        let chosenSimilarity = appearance?.similarity(to: appearanceSignatures[index]) ?? 0.5
        for (i, other) in detections.enumerated() where i != index && i < appearanceSignatures.count {
            guard other.confidence >= minDetectionConfidence else { continue }
            guard chosen.center.distance(to: other.boundingBox.center) <= confusableDistanceScale * chosen.width else { continue }
            let otherSimilarity = appearance?.similarity(to: appearanceSignatures[i]) ?? 0.5
            if abs(chosenSimilarity - otherSimilarity) < appearanceDiscriminationGap { return true }
        }
        return false
    }

    /// The nearest other confident person within contestRadius of the
    /// chosen detection, remembered so a later frame can tell whether the
    /// person it now sees is the target or that rival.
    private func nearestRival(of index: Int, detections: [PersonDetection]) -> NormalizedRect? {
        let chosen = detections[index].boundingBox
        let radius = contestRadius(forWidth: chosen.width)
        return detections.enumerated()
            .filter { $0.offset != index && $0.element.confidence >= minDetectionConfidence
                && chosen.center.distance(to: $0.element.boundingBox.center) <= radius }
            .min { chosen.center.distance(to: $0.element.boundingBox.center) < chosen.center.distance(to: $1.element.boundingBox.center) }?
            .element.boundingBox
    }

    /// Locked/reacquired only: true when a rival was adjacent at the last
    /// commit, no other detection is still near that rival (so it — or the
    /// target — has dropped out), and the survivor is not clearly nearer
    /// the target's last position than the rival's.
    private func survivorIsUnverifiable(matchedIndex: Int, detections: [PersonDetection]) -> Bool {
        guard let rival = rivalBox, let targetCenter = lastKnownCenter else { return false }
        let radius = contestRadius(forWidth: rival.width)
        let rivalStillVisible = detections.enumerated().contains { i, d in
            i != matchedIndex && d.confidence >= minDetectionConfidence
                && d.boundingBox.center.distance(to: rival.center) <= radius
        }
        if rivalStillVisible { return false }
        let survivor = detections[matchedIndex].boundingBox.center
        return !(survivor.distance(to: targetCenter) < survivorNearnessRatio * survivor.distance(to: rival.center))
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
        return (sorted[0] - sorted[1]) >= appearanceDiscriminationGap
    }

    private func scaleContinuityScore(for box: NormalizedRect) -> Double {
        guard let lastArea = lastKnownArea, lastArea > 0 else { return 1.0 }
        let area = box.width * box.height
        guard area > 0 else { return 0.0 }
        let ratio = max(area / lastArea, lastArea / area)
        guard ratio <= maxPlausibleAreaRatioChange else { return 0.0 }
        return 1 - (ratio - 1) / (maxPlausibleAreaRatioChange - 1)
    }

    private func commit(
        detection: PersonDetection, appearance newAppearance: AppearanceSignature, at time: TimeInterval, dt: TimeInterval,
        rival: NormalizedRect?, isReacquisition: Bool
    ) {
        let newCenter = detection.boundingBox.center
        // Velocity is measured over the time since the previous TRUSTED
        // observation. (Before v4 this divided a possibly multi-frame
        // displacement by a single frame's dt, spiking the velocity right
        // after any reacquisition.) In ordinary tracking the gap equals dt,
        // so the published value is unchanged there.
        if let previousCenter = lastKnownCenter, let previousTime = lastCommitTime, time - previousTime > 0 {
            let gap = time - previousTime
            let measured = NormalizedPoint(x: (newCenter.x - previousCenter.x) / gap, y: (newCenter.y - previousCenter.y) / gap)
            currentVelocity = measured
            // The trusted (prediction) velocity is fed only by unambiguous,
            // frame-to-frame observations — never by an ambiguous match or
            // by the multi-frame jump of a reacquisition.
            if !lastMatchWasAmbiguous && !isReacquisition {
                if let existing = trustedVelocity {
                    trustedVelocity = NormalizedPoint(
                        x: existing.x * (1 - velocitySmoothing) + measured.x * velocitySmoothing,
                        y: existing.y * (1 - velocitySmoothing) + measured.y * velocitySmoothing
                    )
                } else {
                    trustedVelocity = measured
                }
            }
        }

        // Rival memory: position now, and velocity when the same rival was
        // seen at the previous commit (plausible step) — from existing
        // detections only, no separate tracker.
        if let newRival = rival {
            if let previous = rivalBox, let previousAt = rivalSeenAt, time - previousAt > 0,
               newRival.center.distance(to: previous.center) <= plausibleStepDistance(dt: time - previousAt) {
                let gap = time - previousAt
                let measured = NormalizedPoint(
                    x: (newRival.center.x - previous.center.x) / gap, y: (newRival.center.y - previous.center.y) / gap
                )
                if let existing = rivalVelocity {
                    rivalVelocity = NormalizedPoint(
                        x: existing.x * (1 - velocitySmoothing) + measured.x * velocitySmoothing,
                        y: existing.y * (1 - velocitySmoothing) + measured.y * velocitySmoothing
                    )
                } else {
                    rivalVelocity = measured
                }
            } else {
                rivalVelocity = nil
            }
            rivalSeenAt = time
        } else {
            rivalVelocity = nil
            rivalSeenAt = nil
        }
        rivalBox = rival
        lastCommitTime = time
        pendingReacquisition = nil
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

    /// Dead-reckoning SEARCH position: where the target would be `dt`
    /// seconds after its last trusted observation at its last trusted
    /// velocity, with `dt` capped at the prediction horizon. While the
    /// target is missing, callers pass the time since the last trusted
    /// observation (not one frame's dt). It is search guidance — never
    /// stored as, or reported as, a measured position.
    public func predictedCenter(dt: TimeInterval) -> NormalizedPoint {
        guard let center = lastKnownCenter else { return NormalizedPoint(x: 0.5, y: 0.5) }
        guard state == .temporarilyLost || state == .searching else { return center }
        let velocity = trustedVelocity ?? NormalizedPoint(x: 0, y: 0)
        let horizon = min(max(dt, 0), maxPredictionHorizonSeconds)
        return NormalizedPoint(
            x: center.x + velocity.x * horizon,
            y: center.y + velocity.y * horizon
        )
    }

    /// Manual "Re-select Player" (spec section 25) — the ONLY way out of
    /// .lost. Never automatic.
    public func reset(to boundingBox: NormalizedRect, appearance newAppearance: AppearanceSignature) {
        currentBoundingBox = boundingBox
        appearance = newAppearance
        lastKnownCenter = boundingBox.center
        lastKnownArea = boundingBox.width * boundingBox.height
        referenceWidth = boundingBox.width
        rivalBox = nil
        rivalVelocity = nil
        rivalSeenAt = nil
        lastCommitTime = nil
        trustedVelocity = nil
        pendingReacquisition = nil
        currentVelocity = .init(x: 0, y: 0)
        consecutiveMisses = 0
        lostSince = nil
        searchingSince = nil
        lastMatchWasAmbiguous = false
        state = .locked
    }
}
