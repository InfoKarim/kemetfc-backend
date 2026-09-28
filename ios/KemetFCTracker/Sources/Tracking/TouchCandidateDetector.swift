//
//  TouchCandidateDetector.swift
//  KemetFCTracker
//
//  Touch Candidate — the first LOW-LEVEL football-interaction candidate,
//  answering only: "is there measurable evidence that the tracked player's
//  interaction may have changed the ball's motion?" This is explicitly NOT a
//  confirmed football Touch, and NEVER Receive/Carry/Dribble/Possession/
//  Pass/Shot. Pure downstream consumer of `PlayerLiveAnalysisSnapshot` (V1)
//  and `PlayerBallCoMovementSnapshot` — this file owns no camera,
//  AVFoundation, Vision, recording, DockKit, or tracking logic, performs no
//  new detection (no new Vision request, no pose/foot-landmark use — see the
//  2026-09-27 audit: ankle joints exist in Vision but are not currently
//  plumbed into Player Analysis, and wiring them in would be a real
//  interpretive expansion, not plumbing — deliberately out of scope here).
//
//  CAMERA/GIMBAL MOTION — READ BEFORE RELYING ON THIS:
//  `preBallSpeed`/`postBallSpeed`/`directionSimilarity` are computed from the
//  ball's own RAW image-space velocity, uncompensated for camera motion,
//  exactly like `PlayerBallCoMovementDetector`'s own documented limitation.
//  Unlike co-movement's relative-position drift (which partially cancels a
//  SHARED, CONSTANT camera translation), a touch candidate compares the
//  ball's absolute velocity BEFORE vs AFTER — if the gimbal's own correction
//  behavior changes between those two windows (e.g. it starts/stops
//  correcting differently as the ball nears the player), that alone can
//  manufacture an apparent speed/direction change with no real touch. Using
//  `relativeBallPosition`/player velocity/co-movement state as CORROBORATING
//  evidence (stored on the event) does NOT eliminate this — see
//  `TouchCandidateDetectorTests.testCommonCameraMotionChangeCanStillManufactureACandidateKnownLimitation`
//  for an explicit, undisguised regression test of this confound. Do NOT
//  attempt gimbal compensation here — out of scope for this phase.
//

import Foundation

// MARK: - Calibration

/// UNVALIDATED CALIBRATION CONSTANTS — same convention as
/// `PlayerAnalysisV1Calibration`/`PlayerAnalysisEventCalibration`/
/// `PlayerBallCoMovementCalibration`. Reuses existing constants wherever the
/// SAME underlying question already has an established answer.
public enum TouchCandidateCalibration {
    /// How far back a "before" velocity sample may be drawn from, relative to
    /// the moment the ball enters the near zone — a short rolling buffer is
    /// always maintained (regardless of proximity) so a real "before" value
    /// exists the instant near-zone entry is detected.
    public static let preWindowSeconds: TimeInterval = 0.5
    /// How long after near-zone entry "after" samples are collected, unless
    /// the ball leaves the near zone first (see the state machine doc below).
    public static let postWindowSeconds: TimeInterval = 0.5
    /// After an episode concludes (emitted or rejected), no new episode may
    /// arm for this long — prevents machine-gunning re-evaluations of the
    /// SAME still-near ball every tick, while still allowing a genuinely
    /// later, distinct interaction within one long continuous near-zone
    /// stretch to be evaluated once the cooldown clears.
    public static let cooldownDurationSeconds: TimeInterval = 0.5
    /// Brief ball-unavailable dropouts up to this long, DURING post-window
    /// collection, do not abort the episode outright — reuses
    /// `PlayerBallCoMovementCalibration`'s own value for the identical
    /// "brief dropout tolerance" question, rather than a fourth,
    /// uncoordinated grace constant.
    public static let dropoutGraceDurationSeconds = PlayerBallCoMovementCalibration.dropoutGraceDurationSeconds
    /// Minimum |Δspeed| (normalized image-space units/second) between the
    /// averaged pre- and post-window ball speeds to count as a meaningful
    /// speed change. Reasoned as roughly 2x `PlayerAnalysisV1Calibration
    /// .movementSpeedThreshold` (the existing "is anything moving at all"
    /// bar), not independently derived.
    public static let minimumSpeedChangeForTouchCandidate: Double = 0.08
    /// Maximum cosine similarity between the averaged pre- and post-window
    /// ball velocity VECTORS to count as a meaningful direction change
    /// (lower cosine = bigger angle). ~0.5 corresponds to a ≥60° change.
    public static let maximumDirectionSimilarityForTouchCandidate: Double = 0.5
    /// A velocity's direction is only meaningful above this speed — reuses
    /// `PlayerBallCoMovementCalibration.minimumBallSpeedForCoMovement`
    /// directly (the SAME "is the ball's motion fast enough to have a real
    /// direction" question already answered there).
    public static let minimumSpeedForMeaningfulDirection = PlayerBallCoMovementCalibration.minimumBallSpeedForCoMovement
}

// MARK: - Output model (Step 7)

/// Why an episode concluded WITHOUT a candidate — explicit, never silently
/// dropped (Step 7: "rejection/insufficient-evidence reason where
/// appropriate").
public enum TouchCandidateRejectionReason: String, Codable, Sendable, Equatable {
    /// The rolling pre-window buffer had no valid ball-velocity sample at
    /// the moment the ball entered the near zone (e.g. the ball had only
    /// just appeared/reappeared — see `PlayerLiveAnalysisSnapshot
    /// .ballVelocity`'s first-observation gating).
    case insufficientPreSamples
    /// No valid ball-velocity sample was collected during the post-window
    /// (e.g. the ball was lost for the entire window, or left the near zone
    /// before ever being re-observed with a valid velocity).
    case insufficientPostSamples
    /// Both a real pre- and post-window average existed, but neither the
    /// speed-change nor the direction-change criterion was met.
    case noMeaningfulMotionChange
}

/// The raw numbers behind a candidate (or a rejection) — never combined into
/// a single opaque score (Step 7/8, mirrors `PlayerBallCoMovementEvidence`'s
/// own philosophy).
public struct TouchCandidateEvidence: Equatable, Sendable {
    public var preBallSpeed: Double?
    public var postBallSpeed: Double?
    /// `postBallSpeed - preBallSpeed` (signed: positive = sped up).
    public var speedChange: Double?
    /// cos(angle) between the averaged pre- and post-window ball velocity
    /// vectors. nil when either average's magnitude was below
    /// `minimumSpeedForMeaningfulDirection` (direction is undefined for a
    /// near-zero vector — never reported as "similar" or "changed" by fiat).
    public var directionSimilarity: Double?
    /// The smallest `normalizedPlayerBallDistance` observed anywhere during
    /// this episode (arming through conclusion).
    public var minimumPlayerBallDistance: Double?
    public var coMovementBefore: PlayerBallCoMovementState?
    public var coMovementAfter: PlayerBallCoMovementState?
    public var preSampleCount: Int
    public var postSampleCount: Int

    public init(
        preBallSpeed: Double? = nil, postBallSpeed: Double? = nil, speedChange: Double? = nil,
        directionSimilarity: Double? = nil, minimumPlayerBallDistance: Double? = nil,
        coMovementBefore: PlayerBallCoMovementState? = nil, coMovementAfter: PlayerBallCoMovementState? = nil,
        preSampleCount: Int = 0, postSampleCount: Int = 0
    ) {
        self.preBallSpeed = preBallSpeed
        self.postBallSpeed = postBallSpeed
        self.speedChange = speedChange
        self.directionSimilarity = directionSimilarity
        self.minimumPlayerBallDistance = minimumPlayerBallDistance
        self.coMovementBefore = coMovementBefore
        self.coMovementAfter = coMovementAfter
        self.preSampleCount = preSampleCount
        self.postSampleCount = postSampleCount
    }
}

/// A candidate, NOT a confirmed Touch. Timestamps use the SAME
/// `sessionElapsedSeconds` convention as everything else in Player Analysis —
/// NOT guaranteed to equal the recorded video's own internal timestamp
/// exactly (see PLAYER_ANALYSIS_V2_EVENT_DESIGN.md section 12's measured
/// ~0.15-0.2s offset finding). Any future validation against human-labeled
/// video timestamps must account for that offset, not assume equality.
public struct TouchCandidateEvent: Equatable, Sendable {
    public var id: UUID
    /// When the ball entered the near zone (the episode/arming moment).
    public var startTime: TimeInterval
    /// The single instant treated as "the interaction" — currently the same
    /// as `startTime` (near-zone entry); this is the most defensible single
    /// instant available without frame-precise touch localization.
    public var interactionTime: TimeInterval
    /// When post-window evidence collection concluded.
    public var endTime: TimeInterval
    public var ballTrackId: Int?
    public var evidence: TouchCandidateEvidence

    public init(
        id: UUID = UUID(), startTime: TimeInterval, interactionTime: TimeInterval, endTime: TimeInterval,
        ballTrackId: Int? = nil, evidence: TouchCandidateEvidence
    ) {
        self.id = id
        self.startTime = startTime
        self.interactionTime = interactionTime
        self.endTime = endTime
        self.ballTrackId = ballTrackId
        self.evidence = evidence
    }
}

// MARK: - The detector

/// State machine (Step 8): `.idle` (armed; a short rolling pre-window buffer
/// of valid ball-velocity samples is always maintained) → on near-zone entry
/// (and not in cooldown), capture `Vbefore` and move to `.collectingPostWindow`
/// → concludes on `postWindowSeconds` elapsed OR the ball leaving the near
/// zone, whichever first → emits a `TouchCandidateEvent` if the motion change
/// is meaningful, else records a rejection reason → `.cooldown` for
/// `cooldownDurationSeconds` → back to `.idle`. Exactly one evaluation per
/// continuous near-zone episode is guaranteed by the cooldown (a touch
/// occurring in the LATER part of a near-zone stretch that outlasts
/// `postWindowSeconds + cooldownDurationSeconds` CAN still be caught by a
/// fresh arming once the cooldown clears — this is a deliberate, documented
/// simplification, not an attempt at continuous re-evaluation).
public final class TouchCandidateDetector {
    public static let algorithmVersion = "touch-candidate-v0.1"

    private enum Phase {
        case idle
        case collectingPostWindow(episode: Episode)
        case cooldown(until: TimeInterval)
    }

    private struct VelocitySample {
        let timestamp: TimeInterval
        let velocity: NormalizedPoint
    }

    private struct Episode {
        let startTime: TimeInterval
        let preAverage: NormalizedPoint
        let preSampleCount: Int
        /// Captured at arming time, for explainability only (Step 7).
        let ballTrackId: Int?
        var postSamples: [VelocitySample] = []
        var minimumDistance: Double?
        var coMovementBefore: PlayerBallCoMovementState?
        var coMovementAfter: PlayerBallCoMovementState?
        /// Last tick's timestamp the ball was genuinely near/available —
        /// used to apply the dropout grace tolerance during collection.
        var lastAvailableTimestamp: TimeInterval
    }

    private var phase: Phase = .idle
    private var preWindowSamples: [VelocitySample] = []
    private var lastTimestamp: TimeInterval?

    /// Safety bound only — real assessments produce orders of magnitude
    /// fewer candidates than this; never expected to bind.
    private let eventCapacity: Int
    private(set) var events: [TouchCandidateEvent] = []
    /// Set whenever an episode concludes WITHOUT emitting a candidate;
    /// cleared the moment a new episode arms. Debug/explainability only.
    private(set) var lastRejectionReason: TouchCandidateRejectionReason?

    /// Immutable for the lifetime of the instance (2026-09-28, configuration
    /// injection) — set once at init, never mutated, so two detector
    /// instances constructed with different configurations can never
    /// influence one another. Defaults to `.baseline`, which is itself
    /// derived directly from `TouchCandidateCalibration` — the live
    /// production detector (`TouchCandidateDetector()`, zero-arg, as
    /// `TrackingCoordinator` constructs it) is therefore byte-for-byte
    /// equivalent to today's behavior; see
    /// `TouchCandidateReplayEngineTests`'s baseline-equivalence suite for
    /// the regression proof.
    private let configuration: TouchCandidateDetectorConfigurationSnapshot

    public init(configuration: TouchCandidateDetectorConfigurationSnapshot = .baseline, eventCapacity: Int = 500) {
        self.configuration = configuration
        self.eventCapacity = max(1, eventCapacity)
    }

    /// Discards ALL in-progress state. Called alongside the other analysis
    /// detectors' `reset()` whenever a player is (re)locked, so one player's
    /// in-progress episode can never leak into a different player's session.
    public func reset() {
        phase = .idle
        preWindowSamples.removeAll(keepingCapacity: true)
        lastTimestamp = nil
        events.removeAll(keepingCapacity: true)
        lastRejectionReason = nil
    }

    /// Call once per tick, fed the SAME-tick V1 snapshot and co-movement
    /// result, in timestamp order. Returns a NEWLY emitted candidate on the
    /// (rare) tick one fires, else nil.
    @discardableResult
    public func observe(analysis: PlayerLiveAnalysisSnapshot, coMovement: PlayerBallCoMovementSnapshot) -> TouchCandidateEvent? {
        let timestamp = analysis.timestamp
        // Timestamp validity: a non-advancing/duplicate/out-of-order
        // timestamp is a total no-op — never corrupts window state, never
        // counted as elapsed time for windows/cooldown.
        if let last = lastTimestamp, timestamp <= last {
            return nil
        }
        lastTimestamp = timestamp

        let validBallVelocity = analysis.ballVisible ? analysis.ballVelocity : nil
        updatePreWindowBuffer(with: validBallVelocity, at: timestamp)

        switch phase {
        case .idle:
            return handleIdleTick(analysis: analysis, coMovement: coMovement)

        case .collectingPostWindow(var episode):
            episode.minimumDistance = minimum(episode.minimumDistance, analysis.normalizedPlayerBallDistance)
            episode.coMovementAfter = coMovement.state

            if let velocity = validBallVelocity {
                episode.postSamples.append(VelocitySample(timestamp: timestamp, velocity: velocity))
                episode.lastAvailableTimestamp = timestamp
            }

            let elapsed = timestamp - episode.startTime
            let leftNearZone = analysis.ballProximityState == .away
            let graceExceeded = timestamp - episode.lastAvailableTimestamp > configuration.dropoutGraceDurationSeconds
            let windowElapsed = elapsed >= configuration.postWindowSeconds

            if leftNearZone || graceExceeded || windowElapsed {
                phase = .cooldown(until: timestamp + configuration.cooldownDurationSeconds)
                return conclude(episode, endTime: timestamp)
            } else {
                phase = .collectingPostWindow(episode: episode)
                return nil
            }

        case .cooldown(let until):
            if timestamp >= until {
                phase = .idle
                // Re-evaluate this SAME tick as a fresh idle tick (NOT by
                // recursing into `observe` — that would immediately bail out
                // on the duplicate-timestamp guard above, since
                // `lastTimestamp` was already updated to THIS tick's value),
                // so a ball that has remained continuously near through the
                // whole cooldown can immediately arm a new episode rather
                // than waiting for the NEXT tick.
                return handleIdleTick(analysis: analysis, coMovement: coMovement)
            }
            return nil
        }
    }

    // MARK: - Private

    private func handleIdleTick(analysis: PlayerLiveAnalysisSnapshot, coMovement: PlayerBallCoMovementSnapshot) -> TouchCandidateEvent? {
        guard analysis.ballProximityState == .near else { return nil }
        return arm(
            at: analysis.timestamp, distance: analysis.normalizedPlayerBallDistance,
            ballTrackId: analysis.ballTrackId, coMovement: coMovement.state
        )
    }

    private func updatePreWindowBuffer(with velocity: NormalizedPoint?, at timestamp: TimeInterval) {
        if let velocity {
            preWindowSamples.append(VelocitySample(timestamp: timestamp, velocity: velocity))
        }
        preWindowSamples.removeAll { timestamp - $0.timestamp > configuration.preWindowSeconds }
    }

    private func arm(at timestamp: TimeInterval, distance: Double?, ballTrackId: Int?, coMovement: PlayerBallCoMovementState) -> TouchCandidateEvent? {
        lastRejectionReason = nil
        guard !preWindowSamples.isEmpty else {
            lastRejectionReason = .insufficientPreSamples
            phase = .cooldown(until: timestamp + configuration.cooldownDurationSeconds)
            return nil
        }
        let preAverage = average(preWindowSamples.map { $0.velocity })
        let episode = Episode(
            startTime: timestamp, preAverage: preAverage, preSampleCount: preWindowSamples.count,
            ballTrackId: ballTrackId, postSamples: [], minimumDistance: distance,
            coMovementBefore: coMovement, coMovementAfter: coMovement, lastAvailableTimestamp: timestamp
        )
        phase = .collectingPostWindow(episode: episode)
        return nil
    }

    private func conclude(_ episode: Episode, endTime: TimeInterval) -> TouchCandidateEvent? {
        guard !episode.postSamples.isEmpty else {
            lastRejectionReason = .insufficientPostSamples
            return nil
        }
        let postAverage = average(episode.postSamples.map { $0.velocity })
        let preSpeed = magnitude(episode.preAverage)
        let postSpeed = magnitude(postAverage)
        let speedChange = postSpeed - preSpeed
        let directionSimilarity = self.directionSimilarity(episode.preAverage, postAverage, preSpeed: preSpeed, postSpeed: postSpeed)

        let meaningfulSpeedChange = abs(speedChange) >= configuration.minimumSpeedChangeForTouchCandidate
        let meaningfulDirectionChange = (directionSimilarity.map { $0 <= configuration.maximumDirectionSimilarityForTouchCandidate }) ?? false

        let evidence = TouchCandidateEvidence(
            preBallSpeed: preSpeed, postBallSpeed: postSpeed, speedChange: speedChange,
            directionSimilarity: directionSimilarity, minimumPlayerBallDistance: episode.minimumDistance,
            coMovementBefore: episode.coMovementBefore, coMovementAfter: episode.coMovementAfter,
            preSampleCount: episode.preSampleCount, postSampleCount: episode.postSamples.count
        )

        guard meaningfulSpeedChange || meaningfulDirectionChange else {
            lastRejectionReason = .noMeaningfulMotionChange
            return nil
        }

        lastRejectionReason = nil
        let event = TouchCandidateEvent(
            startTime: episode.startTime, interactionTime: episode.startTime, endTime: endTime,
            ballTrackId: episode.ballTrackId, evidence: evidence
        )
        events.append(event)
        if events.count > eventCapacity {
            events.removeFirst(events.count - eventCapacity)
        }
        return event
    }

    private func minimum(_ current: Double?, _ new: Double?) -> Double? {
        guard let new else { return current }
        guard let current else { return new }
        return Swift.min(current, new)
    }

    private func average(_ vectors: [NormalizedPoint]) -> NormalizedPoint {
        guard !vectors.isEmpty else { return NormalizedPoint(x: 0, y: 0) }
        let sum = vectors.reduce(NormalizedPoint(x: 0, y: 0)) { NormalizedPoint(x: $0.x + $1.x, y: $0.y + $1.y) }
        return NormalizedPoint(x: sum.x / Double(vectors.count), y: sum.y / Double(vectors.count))
    }

    private func magnitude(_ vector: NormalizedPoint) -> Double {
        (vector.x * vector.x + vector.y * vector.y).squareRoot()
    }

    private func directionSimilarity(_ before: NormalizedPoint, _ after: NormalizedPoint, preSpeed: Double, postSpeed: Double) -> Double? {
        guard preSpeed >= configuration.minimumSpeedForMeaningfulDirection,
              postSpeed >= configuration.minimumSpeedForMeaningfulDirection
        else { return nil }
        let dot = before.x * after.x + before.y * after.y
        let cosTheta = dot / (preSpeed * postSpeed)
        return min(1, max(-1, cosTheta))
    }
}
