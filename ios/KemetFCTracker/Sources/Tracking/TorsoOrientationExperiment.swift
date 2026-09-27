//
//  TorsoOrientationExperiment.swift
//  KemetFCTracker
//
//  EXPERIMENTAL — DEBUG ONLY. NOT wired into PlayerAnalysisEngine, the live
//  snapshot, the assessment summary, or any UI. Per
//  PLAYER_ANALYSIS_CAPABILITY_AUDIT.md section 4: this measures ONLY torso
//  rotation from the shoulder/hip landmarks the pose pipeline already tracks.
//  It is explicitly NOT head orientation (no head/neck/eye landmarks are
//  requested anywhere in this codebase — see PlayerDetector.trackedJointNames)
//  and must never be called, labeled, or surfaced as:
//    "Scanning" / "Vision" / "Awareness" / "Shoulder Check"
//  It is called exactly what it measures: Torso Orientation Change. Whether
//  this signal is useful at all is what this experiment exists to find out,
//  against real footage, before any promotion to production analysis.
//

#if DEBUG
import Foundation

/// A single torso-orientation reading, in radians, measured as the angle of
/// the shoulder-to-shoulder line in image space. Not a real-world/3D angle —
/// purely a 2D image-space proxy (capability audit section 4, Level 1).
public struct TorsoOrientationReading: Equatable, Sendable {
    public var timestamp: TimeInterval
    public var angleRadians: Double
    /// The lower of the two shoulder joints' Vision confidences — the reading
    /// is only as trustworthy as its weakest required landmark.
    public var confidence: Double

    public init(timestamp: TimeInterval, angleRadians: Double, confidence: Double) {
        self.timestamp = timestamp
        self.angleRadians = angleRadians
        self.confidence = confidence
    }
}

public enum TorsoOrientationExperiment {
    /// Below this per-joint confidence, a shoulder landmark is not trusted
    /// enough to compute an orientation from — documented, not validated
    /// against real footage yet, same as this whole experiment.
    public static let minimumJointConfidence = 0.5
    /// Minimum angle change between two readings to call it a "change" rather
    /// than pose-estimation jitter — an initial guess, explicitly unvalidated.
    public static let minimumChangeRadians = 0.35 // ~20 degrees

    /// Reads left/right shoulder from the given pose keypoints and returns the
    /// image-space angle of the shoulder line, or nil if either shoulder is
    /// missing or below `minimumJointConfidence`. Never falls back to hip-only
    /// geometry silently — a torso reading needs both shoulders by definition
    /// here; a future hip-line variant would be a separate, explicitly named
    /// measurement, not a silent substitute.
    public static func torsoOrientation(from keypoints: [PoseKeypoint], at timestamp: TimeInterval) -> TorsoOrientationReading? {
        guard
            let left = keypoints.first(where: { $0.name == "left_shoulder" }),
            let right = keypoints.first(where: { $0.name == "right_shoulder" }),
            left.confidence >= minimumJointConfidence,
            right.confidence >= minimumJointConfidence
        else { return nil }

        let dx = right.x - left.x
        let dy = right.y - left.y
        guard dx != 0 || dy != 0 else { return nil }
        let angle = atan2(dy, dx)
        return TorsoOrientationReading(
            timestamp: timestamp,
            angleRadians: angle,
            confidence: min(left.confidence, right.confidence)
        )
    }

    /// True when two readings differ by at least `minimumChangeRadians`,
    /// correctly handling angle wraparound (e.g. comparing +179° and -179°,
    /// which are 2° apart, not 358°). Returns false (never true) if either
    /// reading's confidence is below `minimumJointConfidence` — a change
    /// computed from an untrusted reading is not reported as a change.
    public static func isOrientationChange(from previous: TorsoOrientationReading, to current: TorsoOrientationReading) -> Bool {
        guard previous.confidence >= minimumJointConfidence, current.confidence >= minimumJointConfidence else { return false }
        var delta = current.angleRadians - previous.angleRadians
        while delta > .pi { delta -= 2 * .pi }
        while delta < -.pi { delta += 2 * .pi }
        return abs(delta) >= minimumChangeRadians
    }
}
#endif
