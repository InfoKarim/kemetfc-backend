//
//  PlayerAnalysisEngine.swift
//  KemetFCTracker
//
//  Player Live Analysis — the layer between the existing CV/tracking pipeline
//  and the UI, per PLAYER_ANALYSIS_CAPABILITY_AUDIT.md. This file owns NOTHING
//  about the camera, AVFoundation, Vision, recording, or the gimbal — it only
//  derives honest, clearly-labeled observations from values TrackingCoordinator
//  already computes every frame (PlayerTracker/BallTracker's own state). It
//  performs no detection itself, allocates no CIContext, and never becomes a
//  second source of truth for tracking.
//
//  Every field here is either:
//    (a) a REAL measurement already produced by the CV pipeline
//        (trackingConfidence, ballConfidence), or
//    (b) a DERIVED, DOCUMENTED, HONEST state (moving/stationary/uncertain,
//        near/away/unavailable) with an explicit threshold/rule below.
//
//  This deliberately does NOT claim: tactical positioning, space awareness,
//  decision making, true scanning, possession, touches, passes, shots, or
//  real-world speed/distance (see the capability audit, sections 3/7/14).
//  "Near"/"away" is ball PROXIMITY, never possession or control.
//
//  No 0-100 score exists anywhere in this file. Per the capability audit's
//  Phase 9, a defensible score needs a raw measurement + normalization +
//  baseline + confidence rule — none of that exists yet, so states are
//  reported as honest labels only.
//

import Foundation

// MARK: - Calibration (pre-commit review, 2026-09-27)

/// UNVALIDATED V1 CALIBRATION CONSTANTS.
///
/// None of these are learned or validated against real assessment footage —
/// they are reasoned starting points only (the same "documented, not learned"
/// convention `PlayerTracker`/`BallTracker` already use for their own tuning
/// constants), centralized here so a future calibration pass touches only
/// this type, never the analysis logic in `PlayerAnalysisEngine` itself.
public enum PlayerAnalysisV1Calibration {
    /// Movement classification threshold, in the SAME units PlayerTracker
    /// already reports `currentVelocity` in: normalized image-space distance
    /// per second (fraction of frame width/height per second) — NOT mph,
    /// km/h, or meters/second. Image-space only (capability audit section 7):
    /// camera/gimbal motion is not compensated for.
    public static let movementSpeedThreshold: Double = 0.04
    /// Consecutive frames the SAME candidate movement classification must
    /// persist before it becomes the reported state — hysteresis so per-frame
    /// detector jitter does not flicker moving/stationary/moving/stationary.
    public static let movementConfirmFrames = 3

    /// Ball-proximity thresholds, in body-widths of the player's own
    /// bounding-box width (the same body-relative-scale idea PlayerTracker
    /// already uses for its own radii, e.g. `contestRadiusScale`) — not a
    /// flat normalized distance, so proximity stays meaningful across
    /// different framing/zoom. Two different thresholds (Schmitt trigger):
    /// becoming "near" requires crossing the tighter bound; staying "near"
    /// tolerates drifting out to the looser bound.
    public static let nearProximityBodyWidths: Double = 1.5
    public static let awayProximityBodyWidths: Double = 2.0

    /// Minimum ball-detector confidence a frame's ball must have before
    /// PROXIMITY is analyzed at all. Set to the classical detector's own
    /// documented output floor (`ClassicalCVBallDetector.confidence`:
    /// `0.20 + 0.20 * discLikeness`, i.e. never below 0.20) — not an
    /// arbitrary extra cut on top of the detector's existing semantics, and
    /// not a change to what counts as `ballVisible`/`ballConfidence` (both
    /// still report the detector's real value regardless). Below this, a
    /// ball IS still reported as visible with its real confidence, but
    /// proximity becomes `.unavailable` rather than a confident near/away
    /// claim built on a reading the detector itself would not call reliable.
    /// Exists mainly for a future, more permissive/less-permissive trained
    /// detector — the current classical detector never actually emits below
    /// this floor, so this constant changes nothing about today's real output.
    public static let minimumBallConfidenceForProximityAnalysis: Double = 0.20
}

// MARK: - Public state enums (audit section 10)

public enum TrackingState: String, Codable, Sendable, Equatable {
    case locked
    case temporarilyLost = "temporarily_lost"
    case lost
    /// No player has ever been locked yet this session — distinct from `.lost`
    /// (a lock existed and was lost). "Every state must support unavailable/
    /// uncertain data honestly" (capability audit, Phase 9).
    case unavailable
}

public enum MovementState: String, Codable, Sendable, Equatable {
    case moving
    case stationary
    /// The player was not actively observed THIS frame (not locked/reacquired
    /// with a real commit) — reporting a movement state from a stale/predicted
    /// position would not be honest, so this is reported instead of a guess.
    case uncertain
}

public enum BallProximityState: String, Codable, Sendable, Equatable {
    case near
    case away
    /// The ball, the player, or both were not observed this frame, OR the
    /// ball's confidence was below `minimumBallConfidenceForProximityAnalysis`.
    case unavailable
}

// MARK: - Live snapshot (audit section 10 — PlayerLiveAnalysisSnapshot)

/// One frame's worth of player-analysis observation. Never contains a backend
/// URL, HTTP status, internal model name, thermal state, or raw engineering
/// diagnostic — those stay in `BackendDiagnostics`/`FieldTestOverlay` (debug-only).
public struct PlayerLiveAnalysisSnapshot: Equatable, Sendable {
    /// Matches the existing `sessionElapsedSeconds`/`t_seconds` convention used
    /// throughout the tracking/telemetry pipeline (TrackingCoordinator,
    /// TrackingSample) — not a wall-clock `Date`, for consistency with the data
    /// this is derived from.
    public var timestamp: TimeInterval

    public var trackingState: TrackingState
    /// Real `PlayerTracker.currentConfidence`. nil exactly when trackingState
    /// is `.lost` or `.unavailable` (PlayerTracker itself zeroes confidence on
    /// a miss — this mirrors that honestly, never holding a stale number).
    public var trackingConfidence: Double?

    public var movementState: MovementState

    /// Reports the detector's own semantics unmodified: true whenever a ball
    /// was detected this frame, at whatever confidence the detector reports —
    /// this is NEVER gated by `minimumBallConfidenceForProximityAnalysis`
    /// (that gate applies only to `ballProximityState`, not to visibility).
    public var ballVisible: Bool
    /// Real ball-detector confidence, including its deliberately low ceiling
    /// (~0.20-0.40 for the current classical heuristic — see BallDetector.swift)
    /// — never inflated, rescaled, or raised here. nil when the ball was not
    /// detected this frame.
    public var ballConfidence: Double?
    public var ballProximityState: BallProximityState

    public init(
        timestamp: TimeInterval,
        trackingState: TrackingState,
        trackingConfidence: Double?,
        movementState: MovementState,
        ballVisible: Bool,
        ballConfidence: Double?,
        ballProximityState: BallProximityState
    ) {
        self.timestamp = timestamp
        self.trackingState = trackingState
        self.trackingConfidence = trackingConfidence
        self.movementState = movementState
        self.ballVisible = ballVisible
        self.ballConfidence = ballConfidence
        self.ballProximityState = ballProximityState
    }
}

// MARK: - Bounded history (audit section 10/7; pre-commit review section 4)

/// The minimum retained per-frame record needed for (a) recent-window/UI use
/// and (b) FUTURE OFFLINE CALIBRATION of the V1 thresholds above — never a
/// CMSampleBuffer or image. This buffer is bounded and, per the pre-commit
/// review, is NOT the source of `PlayerAnalysisSessionSummary` (see the
/// engine's streaming counters below) — it exists for recent-history/
/// calibration purposes only, so a long assessment cannot silently make the
/// full-session summary represent only its most recent slice.
public struct PlayerAnalysisHistoryEntry: Equatable, Sendable {
    public var timestamp: TimeInterval
    public var trackingState: TrackingState
    public var movementState: MovementState
    /// Raw image-space speed (units/second, same as `PlayerLiveAnalysisSnapshot`'s
    /// movement classification is derived from) — retained ONLY for future
    /// offline threshold calibration, never surfaced as a coach-facing physical
    /// measurement. nil when the player was not observed this frame.
    public var rawSpeed: Double?
    public var ballVisible: Bool
    public var ballConfidence: Double?
    /// Normalized player-ball distance in body-widths (same unit the proximity
    /// threshold is defined in) — retained for future calibration. nil whenever
    /// it was not measurable this frame (ball/player not observed, or the
    /// ball's confidence was below the proximity-analysis floor).
    public var normalizedPlayerBallDistance: Double?
}

// MARK: - Post-assessment summary (audit section 10 — PlayerAnalysisSessionSummary)

/// Only measurements defensible from current data — deliberately named as
/// measurements, never tactical conclusions (capability audit, Phase 9/14):
/// `ballNearPlayerFraction` is NOT "possession percentage",
/// `movementActiveFraction` is NOT "work rate score".
///
/// DENOMINATORS (pre-commit review section 1 — each is documented explicitly
/// so "unavailable" data can never silently count as a negative observation):
/// - `trackingAvailableFraction` / `trackingLostFraction`: of ALL samples in
///   the whole session (an `.unavailable` sample counts in neither numerator,
///   so their sum can be < 1 — the remainder IS the unavailable fraction,
///   never hidden, just not separately named as a metric here).
/// - `movementActiveFraction` / `stationaryFraction`: of ALL samples. Their
///   sum is < 1 exactly by the `.uncertain` fraction.
/// - `ballVisibleFraction`: of ALL samples (ball detection does not require
///   the player to be tracked, so the whole session is the right denominator).
/// - `ballNearPlayerFraction`: of samples where proximity was actually
///   MEASURABLE (`normalizedPlayerBallDistance != nil` — i.e. NOT of all
///   samples, and NOT of only ball-visible samples if confidence was too low
///   for proximity, per `minimumBallConfidenceForProximityAnalysis`). nil when
///   that denominator is zero — proximity was unavailable for the entire
///   session — never reported as 0%, which would falsely claim "measured and
///   found to be never near."
public struct PlayerAnalysisSessionSummary: Equatable, Sendable {
    public var sampleCount: Int
    /// Last observed timestamp minus first — 0 when fewer than 2 samples exist.
    public var assessmentDurationSeconds: Double

    public var trackingAvailableFraction: Double
    public var trackingLostFraction: Double

    public var movementActiveFraction: Double
    public var stationaryFraction: Double

    public var ballVisibleFraction: Double
    /// Denominator is `proximityMeasurableSampleCount`, NOT `sampleCount` —
    /// see the type-level doc above. nil when `proximityMeasurableSampleCount == 0`.
    public var ballNearPlayerFraction: Double?
    /// How many samples proximity was actually measurable in — exposed so a
    /// caller can judge how much evidence `ballNearPlayerFraction` rests on
    /// (e.g. "near 80% of 3 samples" is not the same claim as "of 3000").
    public var proximityMeasurableSampleCount: Int

    // Intentionally NOT included in V1 (see PLAYER_ANALYSIS_CAPABILITY_AUDIT.md
    // and the implementation report): directionChangeCount. Counting direction
    // changes robustly from noisy per-frame velocity needs a validated
    // angle-delta + noise-rejection rule this project does not have yet —
    // per the instruction "if it cannot yet be made robustly, leave it out
    // rather than fake it," it is left out rather than shipped as a guess.

    public init(
        sampleCount: Int,
        assessmentDurationSeconds: Double,
        trackingAvailableFraction: Double,
        trackingLostFraction: Double,
        movementActiveFraction: Double,
        stationaryFraction: Double,
        ballVisibleFraction: Double,
        ballNearPlayerFraction: Double?,
        proximityMeasurableSampleCount: Int
    ) {
        self.sampleCount = sampleCount
        self.assessmentDurationSeconds = assessmentDurationSeconds
        self.trackingAvailableFraction = trackingAvailableFraction
        self.trackingLostFraction = trackingLostFraction
        self.movementActiveFraction = movementActiveFraction
        self.stationaryFraction = stationaryFraction
        self.ballVisibleFraction = ballVisibleFraction
        self.ballNearPlayerFraction = ballNearPlayerFraction
        self.proximityMeasurableSampleCount = proximityMeasurableSampleCount
    }
}

// MARK: - The engine

/// Pure derivation over already-produced CV/tracking state. Does not access
/// the camera, does not own an AVCaptureSession, does not modify tracking,
/// recording, or gimbal behavior, and performs no UI work. Call `observe(...)`
/// once per processed CV frame with the same values TrackingCoordinator's own
/// `apply(result:)` already computed — never re-detects anything itself.
///
/// FULL-SESSION AGGREGATION (pre-commit review section 2): `summarize()` is
/// computed from STREAMING RUNNING COUNTERS updated on every `observe()` call,
/// NOT from the bounded `history` buffer — a session far longer than
/// `historyCapacity` still produces a summary over the ENTIRE session, with
/// memory bounded by a small fixed number of counters, independent of
/// duration. `history` itself remains bounded and is for recent-window/UI/
/// future-calibration use only.
public final class PlayerAnalysisEngine {
    public static let algorithmVersion = "player-analysis-engine-v1"

    private let historyCapacity: Int
    private(set) var history: [PlayerAnalysisHistoryEntry] = []

    // MARK: Streaming full-session aggregate counters — O(1) memory regardless
    // of assessment duration. This is the ONLY source `summarize()` reads from.
    private var totalSampleCount = 0
    private var firstTimestamp: TimeInterval?
    private var lastTimestamp: TimeInterval?
    private var lockedCount = 0
    private var lostOrTemporarilyLostCount = 0
    private var movingCount = 0
    private var stationaryCount = 0
    private var ballVisibleCount = 0
    private var proximityMeasurableCount = 0
    private var proximityNearCount = 0

    private var confirmedMovementState: MovementState = .uncertain
    private var movementCandidate: MovementState = .uncertain
    private var movementCandidateStreak = 0

    private var confirmedProximityState: BallProximityState = .unavailable

    public init(historyCapacity: Int = 20_000) {
        self.historyCapacity = max(1, historyCapacity)
    }

    /// Call once per processed frame, downstream of TrackingCoordinator's own
    /// PlayerTracker/BallTracker updates. Every parameter is a value the CV
    /// pipeline already computed — this function detects nothing new, touches
    /// no camera/AVFoundation/Vision/DockKit API, and triggers no additional
    /// inference (pre-commit review section 8).
    @discardableResult
    public func observe(
        playerState: PlayerTrackState?,
        playerConfidence: Double,
        playerBox: NormalizedRect?,
        playerVelocity: NormalizedPoint,
        ballBox: NormalizedRect?,
        ballConfidence: Double,
        at timestamp: TimeInterval
    ) -> PlayerLiveAnalysisSnapshot {
        let trackingState = Self.mapTrackingState(playerState)
        // Matches FrameTelemetryAssembler.isPlayerObserved exactly — the
        // existing, single definition of "a real commit happened this frame",
        // reused rather than re-derived, per "consume existing tracker state,
        // do not create another tracking algorithm."
        let playerObserved = FrameTelemetryAssembler.isPlayerObserved(
            state: playerState ?? .lost, confidence: playerConfidence
        )
        // Detector's own semantics, unmodified — never gated by the proximity
        // confidence floor (that floor only affects ballProximityState below).
        let ballVisible = ballBox != nil && ballConfidence > 0

        var rawSpeed: Double?
        let movementCandidateState: MovementState
        if playerObserved {
            let speed = (playerVelocity.x * playerVelocity.x + playerVelocity.y * playerVelocity.y).squareRoot()
            rawSpeed = speed
            movementCandidateState = speed >= PlayerAnalysisV1Calibration.movementSpeedThreshold ? .moving : .stationary
        } else {
            movementCandidateState = .uncertain
        }
        let movementState = updateMovementHysteresis(candidate: movementCandidateState)

        // Proximity eligibility: requires a real player observation, a visible
        // ball, AND the ball's confidence clearing the proximity-analysis floor
        // — a low-confidence ball can still be reported as `ballVisible`, but
        // never turned into a confident near/away claim (pre-commit review
        // section 6). Never raises the detector's own confidence to qualify.
        var normalizedDistance: Double?
        if playerObserved, ballVisible, let playerBox, let ballBox, playerBox.width > 0,
           ballConfidence >= PlayerAnalysisV1Calibration.minimumBallConfidenceForProximityAnalysis {
            normalizedDistance = playerBox.center.distance(to: ballBox.center) / playerBox.width
        }
        let proximityState = updateProximityHysteresis(distanceInBodyWidths: normalizedDistance)

        let snapshot = PlayerLiveAnalysisSnapshot(
            timestamp: timestamp,
            trackingState: trackingState,
            trackingConfidence: playerObserved ? playerConfidence : nil,
            movementState: movementState,
            ballVisible: ballVisible,
            ballConfidence: ballVisible ? ballConfidence : nil,
            ballProximityState: proximityState
        )

        recordAggregates(
            trackingState: trackingState, movementState: movementState,
            ballVisible: ballVisible, proximityMeasurable: normalizedDistance != nil,
            proximityNear: normalizedDistance.map { $0 <= PlayerAnalysisV1Calibration.nearProximityBodyWidths } ?? false,
            timestamp: timestamp
        )

        appendHistory(
            PlayerAnalysisHistoryEntry(
                timestamp: timestamp,
                trackingState: trackingState,
                movementState: movementState,
                rawSpeed: rawSpeed,
                ballVisible: ballVisible,
                ballConfidence: snapshot.ballConfidence,
                normalizedPlayerBallDistance: normalizedDistance
            )
        )

        return snapshot
    }

    /// Discards ALL state — bounded history, hysteresis, AND the streaming
    /// full-session counters. Called automatically whenever a player is
    /// (re)locked (see `TrackingCoordinator.lockPlayer`), so one player's data
    /// can never leak into a different player's summary — including a
    /// coach re-selecting a different player mid-recording, not only a brand
    /// new `TrackingCoordinator` instance.
    public func reset() {
        history.removeAll(keepingCapacity: true)
        totalSampleCount = 0
        firstTimestamp = nil
        lastTimestamp = nil
        lockedCount = 0
        lostOrTemporarilyLostCount = 0
        movingCount = 0
        stationaryCount = 0
        ballVisibleCount = 0
        proximityMeasurableCount = 0
        proximityNearCount = 0
        confirmedMovementState = .uncertain
        movementCandidate = .uncertain
        movementCandidateStreak = 0
        confirmedProximityState = .unavailable
    }

    /// Aggregates the FULL SESSION (streaming counters, not the bounded
    /// history buffer — see the type-level doc) into `PlayerAnalysisSessionSummary`.
    /// Returns a summary with `sampleCount == 0` and `ballNearPlayerFraction == nil`
    /// when nothing was ever observed — never a fabricated rate from no data.
    public func summarize() -> PlayerAnalysisSessionSummary {
        guard totalSampleCount > 0 else {
            return PlayerAnalysisSessionSummary(
                sampleCount: 0, assessmentDurationSeconds: 0,
                trackingAvailableFraction: 0, trackingLostFraction: 0,
                movementActiveFraction: 0, stationaryFraction: 0,
                ballVisibleFraction: 0, ballNearPlayerFraction: nil,
                proximityMeasurableSampleCount: 0
            )
        }
        let count = Double(totalSampleCount)
        let duration = (lastTimestamp ?? 0) - (firstTimestamp ?? 0)

        return PlayerAnalysisSessionSummary(
            sampleCount: totalSampleCount,
            assessmentDurationSeconds: max(duration, 0),
            trackingAvailableFraction: Double(lockedCount) / count,
            trackingLostFraction: Double(lostOrTemporarilyLostCount) / count,
            movementActiveFraction: Double(movingCount) / count,
            stationaryFraction: Double(stationaryCount) / count,
            ballVisibleFraction: Double(ballVisibleCount) / count,
            ballNearPlayerFraction: proximityMeasurableCount > 0
                ? Double(proximityNearCount) / Double(proximityMeasurableCount)
                : nil,
            proximityMeasurableSampleCount: proximityMeasurableCount
        )
    }

    // MARK: - Private

    private func recordAggregates(
        trackingState: TrackingState, movementState: MovementState,
        ballVisible: Bool, proximityMeasurable: Bool, proximityNear: Bool,
        timestamp: TimeInterval
    ) {
        totalSampleCount += 1
        if firstTimestamp == nil { firstTimestamp = timestamp }
        lastTimestamp = timestamp

        if trackingState == .locked { lockedCount += 1 }
        if trackingState == .lost || trackingState == .temporarilyLost { lostOrTemporarilyLostCount += 1 }
        if movementState == .moving { movingCount += 1 }
        if movementState == .stationary { stationaryCount += 1 }
        if ballVisible { ballVisibleCount += 1 }
        if proximityMeasurable {
            proximityMeasurableCount += 1
            if proximityNear { proximityNearCount += 1 }
        }
    }

    private func appendHistory(_ entry: PlayerAnalysisHistoryEntry) {
        history.append(entry)
        if history.count > historyCapacity {
            history.removeFirst(history.count - historyCapacity)
        }
    }

    private func updateMovementHysteresis(candidate: MovementState) -> MovementState {
        if candidate == .uncertain {
            // Losing observation is reported immediately, never smoothed away
            // — the audit requires honesty about unavailable data over a
            // smooth-looking but stale state.
            movementCandidate = .uncertain
            movementCandidateStreak = 0
            confirmedMovementState = .uncertain
            return .uncertain
        }
        if candidate == movementCandidate {
            movementCandidateStreak += 1
        } else {
            movementCandidate = candidate
            movementCandidateStreak = 1
        }
        if movementCandidateStreak >= PlayerAnalysisV1Calibration.movementConfirmFrames {
            confirmedMovementState = candidate
        }
        return confirmedMovementState
    }

    private func updateProximityHysteresis(distanceInBodyWidths distance: Double?) -> BallProximityState {
        guard let distance else {
            confirmedProximityState = .unavailable
            return .unavailable
        }
        switch confirmedProximityState {
        case .near:
            // Already near: tolerate drifting out to the WIDER bound before
            // reporting "away" (Schmitt trigger — see the constants' doc).
            confirmedProximityState = distance <= PlayerAnalysisV1Calibration.awayProximityBodyWidths ? .near : .away
        case .away, .unavailable:
            // Must cross the TIGHTER bound to newly become "near".
            confirmedProximityState = distance <= PlayerAnalysisV1Calibration.nearProximityBodyWidths ? .near : .away
        }
        return confirmedProximityState
    }

    static func mapTrackingState(_ state: PlayerTrackState?) -> TrackingState {
        guard let state else { return .unavailable }
        switch state {
        case .locked, .reacquired: return .locked
        case .temporarilyLost, .searching: return .temporarilyLost
        case .lost: return .lost
        }
    }
}
