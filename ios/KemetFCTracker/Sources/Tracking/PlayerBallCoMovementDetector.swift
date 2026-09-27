//
//  PlayerBallCoMovementDetector.swift
//  KemetFCTracker
//
//  Player ↔ Ball Co-Movement — a LOW-LEVEL measurement of whether the
//  tracked player and tracked ball are moving together in image space with
//  sufficient evidence. Pure downstream consumer of `PlayerLiveAnalysisSnapshot`
//  (V1), exactly like `PlayerAnalysisEventDetector` (V2.0) — this file owns no
//  camera, AVFoundation, Vision, recording, DockKit, or tracking logic,
//  performs no new detection, and never re-derives a value V1 already
//  computes.
//
//  This is explicitly NOT, and must never become, any of: possession,
//  dribbling, carrying, receiving, passing, touching the ball. Co-movement is
//  only evidence a FUTURE, separately-validated football-event layer may use.
//
//  CAMERA/GIMBAL MOTION — READ BEFORE RELYING ON THIS:
//  Both player and ball velocities are image-space measurements; the gimbal
//  actively pans/tilts toward the player, uncompensated (see
//  PLAYER_ANALYSIS_V2_EVENT_DESIGN.md section 7). Relative-position drift
//  (this file's `relativePositionDrift` evidence) is MORE robust to a common
//  camera translation than either object's absolute velocity, because a pure
//  pan/tilt shifts both objects' image-space position similarly — but it is
//  NOT camera-motion-invariant: if the player and ball are BOTH physically
//  stationary in the real world while the camera pans, their apparent
//  velocities align (high cosine similarity) AND their relative position
//  stays stable (low drift) AND they may already be close — this can still
//  satisfy every gate below. This is a KNOWN, UNFIXED limitation, not hidden
//  by tuning: fixing it would require real gimbal-orientation telemetry to
//  subtract camera motion, which does not exist in this codebase today (see
//  the audit's Part 1 findings) and is explicitly out of scope for this phase.
//

import Foundation

// MARK: - Calibration

/// UNVALIDATED CALIBRATION CONSTANTS — same convention as
/// `PlayerAnalysisV1Calibration`/`PlayerAnalysisEventCalibration`: reasoned
/// starting points, not learned or validated against real footage. Reuses
/// existing V1 thresholds wherever the SAME underlying question already has
/// an established answer, rather than inventing new, uncoordinated numbers.
public enum PlayerBallCoMovementCalibration {
    /// Minimum player speed (normalized image-space units/second) before its
    /// direction is considered meaningful at all. Reuses `PlayerAnalysisV1
    /// Calibration.movementSpeedThreshold` directly — the SAME "is the player
    /// moving at all" bar V1 already established, rather than a second,
    /// uncoordinated threshold for the same underlying question.
    public static let minimumPlayerSpeedForCoMovement = PlayerAnalysisV1Calibration.movementSpeedThreshold
    /// Minimum ball speed before its direction is meaningful. No prior ball-
    /// movement threshold exists in V1 (V1 never classifies ball movement);
    /// set equal to the player's own threshold for consistency (same units,
    /// same reasoning), not independently derived.
    public static let minimumBallSpeedForCoMovement: Double = PlayerAnalysisV1Calibration.movementSpeedThreshold
    /// Directional-similarity floor (cosine of the angle between the player
    /// and ball velocity vectors). ~0.7 corresponds to roughly a 45° cone
    /// either side of identical direction.
    public static let minimumDirectionalSimilarity: Double = 0.7
    /// Sliding window over which relative-position drift (Part 5) is
    /// measured, in seconds.
    public static let relativePositionDriftWindowSeconds: TimeInterval = 1.0
    /// Maximum drift of the ball-relative-to-player vector over the window
    /// above, in body-widths (the SAME unit as V1's proximity thresholds),
    /// before the relationship is no longer considered geometrically stable.
    public static let maximumRelativePositionDriftBodyWidths: Double = 0.5
    /// Minimum continuous supporting duration before `.candidate` is
    /// promoted to `.sustained` — mirrors `PlayerAnalysisEventCalibration
    /// .nearSustainedMinDurationSeconds`'s own 1.0s choice for the same kind
    /// of "don't fire from a blip" reasoning.
    public static let minimumSupportingDurationSeconds: TimeInterval = 1.0
    /// Brief unavailable-data dropouts up to this long do not reset an
    /// in-progress candidate/sustained streak — mirrors V2.0's own tolerance
    /// for a brief ball dropout during an already-near relationship (design
    /// doc section 9). Measured from the LAST genuinely supporting tick, not
    /// renewed by chained dropouts.
    public static let dropoutGraceDurationSeconds: TimeInterval = 0.5
}

// MARK: - Output model (Part 7/8)

/// Deliberately NOT `dribbling`/`possession`/any football label. Distinguishes
/// NO DATA (`.unavailable`) from VALID DATA THAT DOES NOT SUPPORT co-movement
/// (`.notSupported`) — this distinction is mandatory (Part 7).
public enum PlayerBallCoMovementState: String, Codable, Sendable, Equatable {
    /// One or more required signals could not be measured this tick (player
    /// not tracked, ball not visible, either velocity invalid, or distance
    /// not measurable). NEVER treated as negative evidence.
    case unavailable
    /// All required signals WERE measurable, but they do not currently
    /// support co-movement (e.g. stationary, opposite directions, too far
    /// apart, or the relationship is not yet geometrically stable).
    case notSupported
    /// Currently meets every supporting criterion, but has not yet held long
    /// enough to be reported as sustained.
    case candidate
    /// Has met every supporting criterion continuously (tolerating brief
    /// dropouts) for at least `minimumSupportingDurationSeconds`.
    case sustained
}

/// The raw numbers behind a `PlayerBallCoMovementState`, for debugging and
/// any future, separately-validated football-event layer — never combined
/// into a single opaque score (Part 8). Every field is nil exactly when it
/// was not measurable this tick, same idiom as `PlayerLiveAnalysisSnapshot`.
public struct PlayerBallCoMovementEvidence: Equatable, Sendable {
    public var playerSpeed: Double?
    public var ballSpeed: Double?
    /// cos(angle) between the player and ball velocity vectors, clamped to
    /// [-1, 1]. 1 = identical direction, -1 = opposite, 0 = perpendicular.
    public var directionalSimilarity: Double?
    public var normalizedPlayerBallDistance: Double?
    /// How far the ball-relative-to-player vector has moved over the last
    /// `relativePositionDriftWindowSeconds`, in body-widths. nil until the
    /// window has accumulated enough history to be trusted (never assumed
    /// stable from insufficient history).
    public var relativePositionDrift: Double?
    /// How long the CURRENT supporting streak (if any) has held, including
    /// time bridged by a tolerated dropout. nil when not currently in a
    /// supporting streak.
    public var supportingDurationSeconds: TimeInterval?

    public init(
        playerSpeed: Double? = nil,
        ballSpeed: Double? = nil,
        directionalSimilarity: Double? = nil,
        normalizedPlayerBallDistance: Double? = nil,
        relativePositionDrift: Double? = nil,
        supportingDurationSeconds: TimeInterval? = nil
    ) {
        self.playerSpeed = playerSpeed
        self.ballSpeed = ballSpeed
        self.directionalSimilarity = directionalSimilarity
        self.normalizedPlayerBallDistance = normalizedPlayerBallDistance
        self.relativePositionDrift = relativePositionDrift
        self.supportingDurationSeconds = supportingDurationSeconds
    }
}

public struct PlayerBallCoMovementSnapshot: Equatable, Sendable {
    public var timestamp: TimeInterval
    public var state: PlayerBallCoMovementState
    public var evidence: PlayerBallCoMovementEvidence

    public init(timestamp: TimeInterval, state: PlayerBallCoMovementState, evidence: PlayerBallCoMovementEvidence = PlayerBallCoMovementEvidence()) {
        self.timestamp = timestamp
        self.state = state
        self.evidence = evidence
    }
}

// MARK: - The detector

/// Pure derivation over `PlayerLiveAnalysisSnapshot` ticks V1 already
/// produces (extended 2026-09-27 with `playerVelocity`/`relativeBallPosition`
/// specifically to support this detector). Call `observe(snapshot:)` once per
/// tick, in the same order V1's `PlayerAnalysisEngine.observe(...)` already
/// runs — never re-ordered.
public final class PlayerBallCoMovementDetector {
    public static let algorithmVersion = "player-ball-co-movement-v0.1"

    private var lastTimestamp: TimeInterval?
    /// Start of the CURRENT supporting streak; nil when not currently
    /// supporting. Not cleared by a tolerated dropout (see `handleGap`).
    private var supportingStreakStart: TimeInterval?
    /// The last tick that was genuinely SUPPORTING (not merely gap-tolerated)
    /// — the grace window is measured from this, never renewed by chained
    /// dropouts, so a "brief" dropout cannot be extended indefinitely.
    private var lastSupportingTimestamp: TimeInterval?

    private struct RelativePositionSample {
        let timestamp: TimeInterval
        let position: NormalizedPoint
    }
    private var relativePositionSamples: [RelativePositionSample] = []

    public init() {}

    /// Discards ALL in-progress state. Called alongside `PlayerAnalysisEngine
    /// .reset()`/`PlayerAnalysisEventDetector.reset()` whenever a player is
    /// (re)locked (Part 9 scenario 9), so one player's co-movement evidence
    /// can never leak into a different player's session.
    public func reset() {
        lastTimestamp = nil
        supportingStreakStart = nil
        lastSupportingTimestamp = nil
        relativePositionSamples.removeAll(keepingCapacity: true)
    }

    /// Call once per V1 tick, in timestamp order.
    @discardableResult
    public func observe(snapshot: PlayerLiveAnalysisSnapshot) -> PlayerBallCoMovementSnapshot {
        // Timestamp validity (Part 3 gate 9): a non-advancing/duplicate/out-
        // of-order timestamp is unavailable-this-tick — never negative
        // evidence, and never allowed to corrupt the drift window. Does not
        // update `lastTimestamp` (a bad timestamp should not become the new
        // baseline other checks compare against).
        if let last = lastTimestamp, snapshot.timestamp <= last {
            return PlayerBallCoMovementSnapshot(timestamp: snapshot.timestamp, state: .unavailable)
        }
        lastTimestamp = snapshot.timestamp

        // Availability gates (Part 3, #1/2/3/4/7): pure DATA gaps. No
        // opinion this tick; an in-progress streak survives within the
        // dropout grace duration (handled in `handleGap`), never longer.
        guard snapshot.trackingState == .locked,
              snapshot.ballVisible,
              let ballVelocity = snapshot.ballVelocity,
              let playerVelocity = snapshot.playerVelocity,
              let distance = snapshot.normalizedPlayerBallDistance,
              let relativePosition = snapshot.relativeBallPosition
        else {
            return handleGap(at: snapshot.timestamp)
        }

        // From here on every input is REAL, measured data — any criterion
        // that fails below is active counter-evidence, not a gap, and resets
        // the streak immediately (Part 9 scenarios 1-5).
        recordRelativePositionSample(relativePosition, at: snapshot.timestamp)
        let drift = relativePositionDrift(at: snapshot.timestamp)

        let playerSpeed = magnitude(playerVelocity)
        let ballSpeed = magnitude(ballVelocity)
        let cosTheta = directionalSimilarity(playerVelocity, ballVelocity, playerSpeed: playerSpeed, ballSpeed: ballSpeed)

        let closeEnough = distance <= PlayerAnalysisV1Calibration.nearProximityBodyWidths
        let fastEnough = playerSpeed >= PlayerBallCoMovementCalibration.minimumPlayerSpeedForCoMovement
            && ballSpeed >= PlayerBallCoMovementCalibration.minimumBallSpeedForCoMovement
        let directionallySimilar = (cosTheta ?? -1) >= PlayerBallCoMovementCalibration.minimumDirectionalSimilarity
        // No full drift window yet => NOT assumed stable (never claim
        // stability from insufficient history).
        let driftStable = drift.map { $0 <= PlayerBallCoMovementCalibration.maximumRelativePositionDriftBodyWidths } ?? false

        let isSupporting = closeEnough && fastEnough && directionallySimilar && driftStable

        let evidence = PlayerBallCoMovementEvidence(
            playerSpeed: playerSpeed, ballSpeed: ballSpeed, directionalSimilarity: cosTheta,
            normalizedPlayerBallDistance: distance, relativePositionDrift: drift,
            supportingDurationSeconds: (isSupporting ? (supportingStreakStart ?? snapshot.timestamp) : nil)
                .map { snapshot.timestamp - $0 }
        )

        if isSupporting {
            if supportingStreakStart == nil { supportingStreakStart = snapshot.timestamp }
            lastSupportingTimestamp = snapshot.timestamp
            let duration = snapshot.timestamp - (supportingStreakStart ?? snapshot.timestamp)
            let state: PlayerBallCoMovementState = duration >= PlayerBallCoMovementCalibration.minimumSupportingDurationSeconds ? .sustained : .candidate
            return PlayerBallCoMovementSnapshot(timestamp: snapshot.timestamp, state: state, evidence: evidence)
        } else {
            // Real counter-evidence: reset the streak outright, no grace.
            supportingStreakStart = nil
            lastSupportingTimestamp = nil
            return PlayerBallCoMovementSnapshot(timestamp: snapshot.timestamp, state: .notSupported, evidence: evidence)
        }
    }

    // MARK: - Private

    private func handleGap(at timestamp: TimeInterval) -> PlayerBallCoMovementSnapshot {
        if let last = lastSupportingTimestamp, timestamp - last <= PlayerBallCoMovementCalibration.dropoutGraceDurationSeconds {
            // Within grace: preserve the streak's bookkeeping untouched, but
            // this TICK itself still honestly reports `.unavailable` (Part 7
            // mandates NO DATA is never silently reported as candidate/
            // sustained, even while it is quietly tolerated internally).
        } else {
            // Grace exceeded (or no streak existed): a genuine gap must not
            // let an artificially old streak silently resume later, and
            // stale relative-position history must not corrupt a future
            // drift calculation (Part 5: "a gap must not create artificial
            // co-movement").
            supportingStreakStart = nil
            lastSupportingTimestamp = nil
            relativePositionSamples.removeAll(keepingCapacity: true)
        }
        return PlayerBallCoMovementSnapshot(timestamp: timestamp, state: .unavailable)
    }

    private func recordRelativePositionSample(_ position: NormalizedPoint, at timestamp: TimeInterval) {
        relativePositionSamples.append(RelativePositionSample(timestamp: timestamp, position: position))
        relativePositionSamples.removeAll { timestamp - $0.timestamp > PlayerBallCoMovementCalibration.relativePositionDriftWindowSeconds }
    }

    /// Distance between the newest and oldest retained sample — nil unless
    /// the retained window spans at least half the target duration, so a
    /// freshly (re)started window is never mistaken for a stable one.
    private func relativePositionDrift(at timestamp: TimeInterval) -> Double? {
        guard let oldest = relativePositionSamples.first, let newest = relativePositionSamples.last else { return nil }
        let coveredSeconds = newest.timestamp - oldest.timestamp
        guard coveredSeconds >= PlayerBallCoMovementCalibration.relativePositionDriftWindowSeconds * 0.5 else { return nil }
        return oldest.position.distance(to: newest.position)
    }

    private func magnitude(_ vector: NormalizedPoint) -> Double {
        (vector.x * vector.x + vector.y * vector.y).squareRoot()
    }

    /// dot(Vp, Vb) / (|Vp| * |Vb|), clamped to [-1, 1] for numerical safety.
    /// nil when either vector's magnitude is (numerically) zero — direction
    /// is undefined for a zero vector, never reported as "similar" or
    /// "dissimilar" by fiat.
    private func directionalSimilarity(_ playerVelocity: NormalizedPoint, _ ballVelocity: NormalizedPoint, playerSpeed: Double, ballSpeed: Double) -> Double? {
        guard playerSpeed > 0, ballSpeed > 0 else { return nil }
        let dot = playerVelocity.x * ballVelocity.x + playerVelocity.y * ballVelocity.y
        let cosTheta = dot / (playerSpeed * ballSpeed)
        return min(1, max(-1, cosTheta))
    }
}
