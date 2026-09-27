//
//  PlayerAnalysisEventDetector.swift
//  KemetFCTracker
//
//  Player Analysis V2.0 — Temporal Event Detection Engine, implementing ONLY
//  the "IMPLEMENT IN V2.0" subset from PLAYER_ANALYSIS_V2_EVENT_DESIGN.md
//  section 17. Pure downstream consumer of PlayerAnalysisEngine's (V1)
//  PlayerLiveAnalysisSnapshot — this file owns no camera, AVFoundation,
//  Vision, recording, DockKit, or tracking logic, performs no new detection,
//  and never re-derives a state V1 already classifies (movementState,
//  ballProximityState). It only detects TRANSITIONS/TRENDS in states V1
//  already produces, with its own debounce/cooldown rules on top (design
//  doc sections 3/5/9/10).
//
//  Explicitly NOT implemented here (per the design doc's classification):
//    - Direction change (needs a V1 data-contract extension: velocity ANGLE,
//      not just magnitude — deferred to V2.1).
//    - Real ball co-movement / "ballMovedWithPlayer" (needs BallTracker's
//      velocity exposed publicly — deferred to V2.1).
//    - Any football label (touch/pass/dribble/shot/receive/possession).
//    - Any camera-motion-compensated absolute event.
//  See the design doc section 17 for the full classification.
//

import Foundation

// MARK: - Calibration

/// UNVALIDATED V2.0 CALIBRATION CONSTANTS — same convention as
/// `PlayerAnalysisV1Calibration`: reasoned starting points, not learned or
/// validated against real assessment footage, centralized here so a future
/// calibration pass touches only this type, never the detector logic below.
public enum PlayerAnalysisEventCalibration {
    /// Minimum time V1's `movementState` must hold a NEW confirmed value
    /// before `movementStarted`/`movementStopped` fires — a debounce ON TOP
    /// of V1's own frame-count hysteresis (design doc section 3), because a
    /// single V1-confirmed tick could still be a brief detector blip.
    public static let movementEventMinHoldSeconds: TimeInterval = 0.5
    /// Minimum continuous `.moving` duration before `sustainedMovement`
    /// fires (design doc section 3). Any interruption resets this window.
    public static let sustainedMovementMinDurationSeconds: TimeInterval = 3.0

    /// Minimum continuous "near" duration (tolerating brief ball dropouts,
    /// per design doc section 9) before `ballStayedNear` fires.
    public static let nearSustainedMinDurationSeconds: TimeInterval = 1.0

    /// Consecutive ELIGIBLE (ball-confidence-above-floor AND tracking-locked)
    /// samples required to evaluate an approach/separation window (design
    /// doc section 4). A single ineligible sample resets window collection.
    public static let approachWindowSampleCount = 5
    /// Minimum total distance decrease/increase over a window, in
    /// body-widths, before `ballApproaching`/`ballSeparating` fires — rejects
    /// sub-noise wobble around a stable distance.
    public static let approachMinimumTotalDeltaBodyWidths: Double = 0.3
    /// A step's increase (for approaching) or decrease (for separating), as a
    /// fraction of the window's total delta in the expected direction, that
    /// is still tolerated as noise without breaking "monotonic" (design doc
    /// section 4).
    public static let approachNoiseToleranceFraction: Double = 0.10

    /// Minimum cooldown after a direction-change-class event before a new
    /// candidate of the same family may begin. (Reserved for V2.1's direction
    /// change; unused by the V2.0 subset itself, kept here per the design
    /// doc's section 10 table for a single source of truth on timing constants.)
    public static let directionChangeCooldownSeconds: TimeInterval = 1.0
}

// MARK: - Event model (design doc section 2)

public enum PlayerAnalysisEventType: String, Codable, Sendable, Equatable {
    case movementStarted = "movement_started"
    case movementStopped = "movement_stopped"
    case sustainedMovement = "sustained_movement"
    case ballEnteredNearZone = "ball_entered_near_zone"
    case ballLeftNearZone = "ball_left_near_zone"
    case ballStayedNear = "ball_stayed_near"
    case ballApproaching = "ball_approaching"
    case ballSeparating = "ball_separating"
    case playerTemporarilyLost = "player_temporarily_lost"
    case playerReacquired = "player_reacquired"
    case ballBecameVisible = "ball_became_visible"
    case ballBecameUnavailable = "ball_became_unavailable"
}

/// A plain measurement/evidence-quality label, never a fabricated combined
/// confidence number (design doc section 8).
public enum EventEvidenceQuality: String, Codable, Sendable, Equatable {
    case high
    case medium
    case low
}

/// The raw numbers that justified an event firing — lets a human or a future
/// classifier re-judge an event's quality from full information rather than
/// trusting only `evidenceQuality` (design doc section 8).
public struct PlayerAnalysisEventEvidence: Equatable, Sendable {
    public var sampleCount: Int?
    public var startValue: Double?
    public var endValue: Double?
    public var minTrackingConfidenceObserved: Double?
    public var durationSeconds: Double?

    public init(
        sampleCount: Int? = nil,
        startValue: Double? = nil,
        endValue: Double? = nil,
        minTrackingConfidenceObserved: Double? = nil,
        durationSeconds: Double? = nil
    ) {
        self.sampleCount = sampleCount
        self.startValue = startValue
        self.endValue = endValue
        self.minTrackingConfidenceObserved = minTrackingConfidenceObserved
        self.durationSeconds = durationSeconds
    }
}

public struct PlayerAnalysisEvent: Equatable, Sendable {
    public var id: UUID
    public var type: PlayerAnalysisEventType
    /// Same `sessionElapsedSeconds` clock as V1's snapshots — see design doc
    /// section 12 for the known, measured offset against the recorded
    /// video's own t=0 (not corrected here; a distinct, deferred item).
    public var startTimestamp: TimeInterval
    public var endTimestamp: TimeInterval?
    public var evidenceQuality: EventEvidenceQuality
    public var supportingMeasurements: PlayerAnalysisEventEvidence

    public var duration: TimeInterval? {
        guard let endTimestamp else { return nil }
        return endTimestamp - startTimestamp
    }

    public init(
        id: UUID = UUID(),
        type: PlayerAnalysisEventType,
        startTimestamp: TimeInterval,
        endTimestamp: TimeInterval? = nil,
        evidenceQuality: EventEvidenceQuality,
        supportingMeasurements: PlayerAnalysisEventEvidence = PlayerAnalysisEventEvidence()
    ) {
        self.id = id
        self.type = type
        self.startTimestamp = startTimestamp
        self.endTimestamp = endTimestamp
        self.evidenceQuality = evidenceQuality
        self.supportingMeasurements = supportingMeasurements
    }
}

// MARK: - The detector

/// Pure derivation over `PlayerLiveAnalysisSnapshot` ticks V1 already
/// produces. Call `observe(snapshot:)` once per tick, in the same order V1's
/// `PlayerAnalysisEngine.observe(...)` is called — never re-ordered, never
/// fed a snapshot out of timestamp order. Returns any NEWLY completed events
/// from this tick (usually empty).
public final class PlayerAnalysisEventDetector {
    public static let algorithmVersion = "player-analysis-event-detector-v2.0"

    /// Safety bound only (design doc section 11) — real assessments produce
    /// orders of magnitude fewer events than this; never expected to bind.
    private let eventCapacity: Int
    private(set) var events: [PlayerAnalysisEvent] = []

    // MARK: Movement state
    /// The last CONFIRMED (already held for `movementEventMinHoldSeconds`)
    /// baseline — `.uncertain` means "no baseline established yet this
    /// session/since the last reset", never a real observed state. Event
    /// semantics (design doc section 3, refined 2026-09-27): initial baseline
    /// establishment is NOT an event — `movementStarted`/`movementStopped`
    /// only fire on a genuine transition AWAY FROM an already-established
    /// baseline, never on the first-ever confirmed state. This is why
    /// `.uncertain` is deliberately excluded from ever being the fired
    /// event's "from" state: `UNKNOWN → STATIONARY`/`UNKNOWN → MOVING` both
    /// silently establish the baseline, exactly like `stationary/moving`,
    /// `stationary, stationary, stationary`, ... producing zero events.
    private var establishedMovementState: MovementState = .uncertain
    /// The raw V1 state currently being held as a CANDIDATE for the next
    /// baseline — distinct from `establishedMovementState`, which only
    /// updates once the candidate clears the hold-time debounce.
    private var movementCandidate: MovementState = .uncertain
    private var movementCandidateHeldSince: TimeInterval?
    /// Start of the current UNBROKEN `.moving` stretch, for `sustainedMovement`.
    private var movingStretchStart: TimeInterval?
    private var sustainedMovementFiredForCurrentStretch = false
    private var minTrackingConfidenceDuringMovingStretch: Double?

    // MARK: Near-zone state (design doc section 5)
    private enum ZoneState { case unresolved, near, away }
    private var zoneState: ZoneState = .unresolved
    private var nearSegmentStart: TimeInterval?
    private var nearSustainedFired = false

    // MARK: Approach/separation window (design doc section 4)
    private struct DistanceSample {
        let timestamp: TimeInterval
        let distance: Double
        let trackingConfidence: Double?
    }
    private var approachWindow: [DistanceSample] = []
    /// Whether the player was in (or had most recently been in) a `.near`
    /// relationship at the START of the window currently being collected —
    /// gates `ballSeparating`, per design doc section 4 ("only evaluated once
    /// a near relationship has existed").
    private var windowStartedNear = false

    // MARK: Tracking/ball availability edges
    private var lastTrackingLocked: Bool?
    private var lastBallVisible: Bool?

    public init(eventCapacity: Int = 2_000) {
        self.eventCapacity = max(1, eventCapacity)
    }

    /// Discards ALL in-progress state and the event timeline. Called
    /// alongside `PlayerAnalysisEngine.reset()` whenever a player is
    /// (re)locked, so one player's in-progress candidates/events can never
    /// leak into a different player's session (mirrors V1's own session-
    /// isolation fix; design doc section 11).
    public func reset() {
        events.removeAll(keepingCapacity: true)
        establishedMovementState = .uncertain
        movementCandidate = .uncertain
        movementCandidateHeldSince = nil
        movingStretchStart = nil
        sustainedMovementFiredForCurrentStretch = false
        minTrackingConfidenceDuringMovingStretch = nil
        zoneState = .unresolved
        nearSegmentStart = nil
        nearSustainedFired = false
        approachWindow.removeAll(keepingCapacity: true)
        windowStartedNear = false
        lastTrackingLocked = nil
        lastBallVisible = nil
    }

    /// Call once per V1 tick, in timestamp order. Returns any events newly
    /// completed on this call (commonly empty).
    @discardableResult
    public func observe(snapshot: PlayerLiveAnalysisSnapshot) -> [PlayerAnalysisEvent] {
        var newEvents: [PlayerAnalysisEvent] = []

        updateMovementEvents(snapshot: snapshot, into: &newEvents)
        updateNearZoneEvents(snapshot: snapshot, into: &newEvents)
        updateApproachSeparationEvents(snapshot: snapshot, into: &newEvents)
        updateAvailabilityEvents(snapshot: snapshot, into: &newEvents)

        if !newEvents.isEmpty {
            events.append(contentsOf: newEvents)
            if events.count > eventCapacity {
                events.removeFirst(events.count - eventCapacity)
            }
        }
        return newEvents
    }

    // MARK: - Movement events (design doc section 3)

    private func updateMovementEvents(snapshot: PlayerLiveAnalysisSnapshot, into newEvents: inout [PlayerAnalysisEvent]) {
        let state = snapshot.movementState

        // Any pass through `.uncertain` (tracking gap) voids the in-progress
        // CANDIDATE entirely — never bridges an event across a tracking gap
        // (design doc section 9). The already-ESTABLISHED baseline is
        // deliberately NOT cleared here: a brief gap must not manufacture a
        // transition (stationary → gap → stationary must stay silent), but a
        // genuine transition that straddles a brief gap (stationary → gap →
        // moving) must still be reported once reconfirmed — losing sight of
        // the player briefly doesn't erase what was already known before it.
        if state == .uncertain {
            movementCandidate = .uncertain
            movementCandidateHeldSince = nil
            movingStretchStart = nil
            sustainedMovementFiredForCurrentStretch = false
            minTrackingConfidenceDuringMovingStretch = nil
            return
        }

        if state != movementCandidate {
            // A genuine V1-confirmed value — start a new hold candidate.
            movementCandidate = state
            movementCandidateHeldSince = snapshot.timestamp
        }

        if let heldSince = movementCandidateHeldSince,
           snapshot.timestamp - heldSince >= PlayerAnalysisEventCalibration.movementEventMinHoldSeconds,
           establishedMovementState != state {
            // The candidate has cleared the hold-time debounce AND differs
            // from the current baseline. Only fire an event when a baseline
            // was already established — the very first baseline (established
            // == .uncertain) is establishment only, never an event (2026-09-27
            // semantics refinement: "initial observation establishes baseline
            // state only", never `UNKNOWN → STATIONARY`/`UNKNOWN → MOVING`).
            if establishedMovementState != .uncertain {
                let type: PlayerAnalysisEventType = state == .moving ? .movementStarted : .movementStopped
                newEvents.append(PlayerAnalysisEvent(
                    type: type,
                    startTimestamp: heldSince,
                    evidenceQuality: evidenceQuality(forConfidence: snapshot.trackingConfidence),
                    supportingMeasurements: PlayerAnalysisEventEvidence(
                        minTrackingConfidenceObserved: snapshot.trackingConfidence,
                        durationSeconds: snapshot.timestamp - heldSince
                    )
                ))
            }
            establishedMovementState = state
        }

        // Sustained movement: tracked independently of the debounced
        // started/stopped events, over the UNBROKEN `.moving` stretch.
        if state == .moving {
            if movingStretchStart == nil {
                movingStretchStart = snapshot.timestamp
                sustainedMovementFiredForCurrentStretch = false
                minTrackingConfidenceDuringMovingStretch = snapshot.trackingConfidence
            } else if let confidence = snapshot.trackingConfidence {
                minTrackingConfidenceDuringMovingStretch = min(
                    minTrackingConfidenceDuringMovingStretch ?? confidence, confidence
                )
            }
            if let start = movingStretchStart,
               !sustainedMovementFiredForCurrentStretch,
               snapshot.timestamp - start >= PlayerAnalysisEventCalibration.sustainedMovementMinDurationSeconds {
                newEvents.append(PlayerAnalysisEvent(
                    type: .sustainedMovement,
                    startTimestamp: start,
                    endTimestamp: snapshot.timestamp,
                    evidenceQuality: evidenceQuality(forConfidence: minTrackingConfidenceDuringMovingStretch),
                    supportingMeasurements: PlayerAnalysisEventEvidence(
                        minTrackingConfidenceObserved: minTrackingConfidenceDuringMovingStretch,
                        durationSeconds: snapshot.timestamp - start
                    )
                ))
                sustainedMovementFiredForCurrentStretch = true
            }
        } else {
            // `.stationary` — the unbroken moving stretch ends.
            movingStretchStart = nil
            sustainedMovementFiredForCurrentStretch = false
            minTrackingConfidenceDuringMovingStretch = nil
        }
    }

    // MARK: - Near-zone events (design doc section 5)

    private func updateNearZoneEvents(snapshot: PlayerLiveAnalysisSnapshot, into newEvents: inout [PlayerAnalysisEvent]) {
        switch snapshot.ballProximityState {
        case .near:
            if zoneState != .near {
                newEvents.append(PlayerAnalysisEvent(
                    type: .ballEnteredNearZone,
                    startTimestamp: snapshot.timestamp,
                    evidenceQuality: evidenceQuality(forConfidence: snapshot.ballConfidence),
                    supportingMeasurements: PlayerAnalysisEventEvidence(
                        endValue: snapshot.normalizedPlayerBallDistance
                    )
                ))
                zoneState = .near
                nearSegmentStart = snapshot.timestamp
                nearSustainedFired = false
            }
            if let start = nearSegmentStart, !nearSustainedFired,
               snapshot.timestamp - start >= PlayerAnalysisEventCalibration.nearSustainedMinDurationSeconds {
                newEvents.append(PlayerAnalysisEvent(
                    type: .ballStayedNear,
                    startTimestamp: start,
                    endTimestamp: snapshot.timestamp,
                    evidenceQuality: evidenceQuality(forConfidence: snapshot.ballConfidence),
                    supportingMeasurements: PlayerAnalysisEventEvidence(
                        durationSeconds: snapshot.timestamp - start
                    )
                ))
                nearSustainedFired = true
            }
        case .away:
            if zoneState == .near {
                newEvents.append(PlayerAnalysisEvent(
                    type: .ballLeftNearZone,
                    startTimestamp: snapshot.timestamp,
                    evidenceQuality: evidenceQuality(forConfidence: snapshot.ballConfidence),
                    supportingMeasurements: PlayerAnalysisEventEvidence(
                        endValue: snapshot.normalizedPlayerBallDistance
                    )
                ))
            }
            zoneState = .away
            nearSegmentStart = nil
            nearSustainedFired = false
        case .unavailable:
            // Explicitly a non-event interruption (design doc section 5/9):
            // does not change `zoneState` and does not reset the in-progress
            // near-sustained timer — a single missing/low-confidence ball
            // sample must not break an already-sustained near relationship.
            break
        }
    }

    // MARK: - Ball approach/separation events (design doc section 4)

    private func updateApproachSeparationEvents(snapshot: PlayerLiveAnalysisSnapshot, into newEvents: inout [PlayerAnalysisEvent]) {
        let eligible = snapshot.trackingState == .locked && snapshot.normalizedPlayerBallDistance != nil
        guard eligible, let distance = snapshot.normalizedPlayerBallDistance else {
            // A single ineligible sample breaks the window entirely — the
            // next window starts fresh (design doc section 4/9), never
            // bridges a dropout by interpolating.
            approachWindow.removeAll(keepingCapacity: true)
            return
        }

        if approachWindow.isEmpty {
            windowStartedNear = zoneState == .near
        }
        approachWindow.append(DistanceSample(
            timestamp: snapshot.timestamp, distance: distance, trackingConfidence: snapshot.trackingConfidence
        ))

        guard approachWindow.count >= PlayerAnalysisEventCalibration.approachWindowSampleCount else { return }

        defer {
            // Non-overlapping windows (design doc section 10): once a window
            // is fully evaluated — fired or not — start fresh.
            approachWindow.removeAll(keepingCapacity: true)
        }

        let totalDelta = approachWindow.last!.distance - approachWindow.first!.distance // negative = approaching
        let minConfidence = approachWindow.compactMap { $0.trackingConfidence }.min()

        if totalDelta < 0, abs(totalDelta) >= PlayerAnalysisEventCalibration.approachMinimumTotalDeltaBodyWidths,
           isMonotonic(approachWindow.map { $0.distance }, decreasing: true, totalDelta: abs(totalDelta)) {
            newEvents.append(PlayerAnalysisEvent(
                type: .ballApproaching,
                startTimestamp: approachWindow.first!.timestamp,
                endTimestamp: approachWindow.last!.timestamp,
                evidenceQuality: evidenceQuality(forConfidence: minConfidence),
                supportingMeasurements: PlayerAnalysisEventEvidence(
                    sampleCount: approachWindow.count,
                    startValue: approachWindow.first!.distance,
                    endValue: approachWindow.last!.distance,
                    minTrackingConfidenceObserved: minConfidence,
                    durationSeconds: approachWindow.last!.timestamp - approachWindow.first!.timestamp
                )
            ))
        } else if windowStartedNear, totalDelta > 0, totalDelta >= PlayerAnalysisEventCalibration.approachMinimumTotalDeltaBodyWidths,
                  isMonotonic(approachWindow.map { $0.distance }, decreasing: false, totalDelta: totalDelta) {
            // Separation is only evaluated once a near relationship existed
            // (design doc section 4) — gated by `windowStartedNear`.
            newEvents.append(PlayerAnalysisEvent(
                type: .ballSeparating,
                startTimestamp: approachWindow.first!.timestamp,
                endTimestamp: approachWindow.last!.timestamp,
                evidenceQuality: evidenceQuality(forConfidence: minConfidence),
                supportingMeasurements: PlayerAnalysisEventEvidence(
                    sampleCount: approachWindow.count,
                    startValue: approachWindow.first!.distance,
                    endValue: approachWindow.last!.distance,
                    minTrackingConfidenceObserved: minConfidence,
                    durationSeconds: approachWindow.last!.timestamp - approachWindow.first!.timestamp
                )
            ))
        }
    }

    /// True when consecutive samples move in the expected direction, with
    /// each "wrong-way" step tolerated only up to
    /// `approachNoiseToleranceFraction` of the window's total delta (design
    /// doc section 4).
    private func isMonotonic(_ values: [Double], decreasing: Bool, totalDelta: Double) -> Bool {
        guard totalDelta > 0, values.count >= 2 else { return false }
        let tolerance = totalDelta * PlayerAnalysisEventCalibration.approachNoiseToleranceFraction
        for i in 1..<values.count {
            let step = values[i] - values[i - 1]
            let wrongWayStep = decreasing ? step : -step
            if wrongWayStep > tolerance { return false }
        }
        return true
    }

    // MARK: - Tracking/ball availability edge events

    private func updateAvailabilityEvents(snapshot: PlayerLiveAnalysisSnapshot, into newEvents: inout [PlayerAnalysisEvent]) {
        let locked = snapshot.trackingState == .locked
        if let last = lastTrackingLocked, last != locked {
            newEvents.append(PlayerAnalysisEvent(
                type: locked ? .playerReacquired : .playerTemporarilyLost,
                startTimestamp: snapshot.timestamp,
                evidenceQuality: evidenceQuality(forConfidence: snapshot.trackingConfidence)
            ))
        }
        lastTrackingLocked = locked

        if let last = lastBallVisible, last != snapshot.ballVisible {
            newEvents.append(PlayerAnalysisEvent(
                type: snapshot.ballVisible ? .ballBecameVisible : .ballBecameUnavailable,
                startTimestamp: snapshot.timestamp,
                evidenceQuality: evidenceQuality(forConfidence: snapshot.ballConfidence)
            ))
        }
        lastBallVisible = snapshot.ballVisible
    }

    // MARK: - Evidence quality (design doc section 8)

    private func evidenceQuality(forConfidence confidence: Double?) -> EventEvidenceQuality {
        guard let confidence else { return .low }
        if confidence >= 0.7 { return .high }
        if confidence >= 0.5 { return .medium }
        return .low
    }
}
