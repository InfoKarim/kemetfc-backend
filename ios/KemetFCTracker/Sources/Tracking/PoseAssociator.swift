//
//  PoseAssociator.swift
//  KemetFCTracker
//
//  Decides which detected body pose (if any) belongs to the coach-selected
//  player. Pure geometry over the frame's poses and person boxes — no
//  facial recognition, no biometric identity, no appearance model.
//
//  THE DEFECT THIS REPLACES: the old code took `bodyPoseRequest.results?.first`
//  — the first pose Vision returned for the WHOLE frame — and attached it to
//  the selected player's telemetry. With two children in view that was often
//  the wrong child's body.
//
//  RULE: "no pose is better than the wrong pose."
//    * The selected player must have been OBSERVED this frame (a confident
//      tracker commit) — never a stale or predicted box.
//    * A pose belongs to the player only when enough of its joints fall
//      inside the player's (slightly padded) box AND clearly more than in
//      any other person's box (a margin), so overlapping players yield NO
//      pose instead of a coin flip.
//    * Several poses claiming the same player without a clear winner also
//      yield no pose.
//
//  Poses use the same top-left-origin normalized coordinates as the boxes
//  (PlayerDetector flips Vision's bottom-left origin for both).
//

import Foundation

public struct PoseAssociationResult: Equatable, Sendable {
    public enum Outcome: String, Sendable {
        /// Exactly one pose clearly belongs to the selected player.
        case matched
        /// The selected player was not observed this frame: nothing to attach a pose to.
        case noObservedTarget = "no_observed_target"
        /// Vision found no usable pose in the frame.
        case noPoses = "no_poses"
        /// Poses exist, but none of them is the selected player's.
        case foreign
        /// More than one pose (or another person) plausibly explains the same box:
        /// association is uncertain, so no pose is reported.
        case ambiguous
    }

    public var outcome: Outcome
    /// Non-nil only for `.matched`.
    public var keypoints: [PoseKeypoint]?
}

public enum PoseAssociator {
    /// Poses with fewer usable joints than this cannot be located reliably.
    public static let minimumJoints = 4
    /// Fraction of a pose's joints that must lie inside the target box.
    public static let minimumInsideFraction = 0.6
    /// How much more of the pose must lie in the target box than in any other person's box.
    public static let minimumMargin = 0.25
    /// Between two poses that both claim the target, the winner must lead by this much.
    public static let minimumPoseLead = 0.15
    /// The box is widened by this fraction of its size on each side (joints of an
    /// extended arm/leg can leave a tight person box).
    public static let boxPadding = 0.10
    /// Another detection this overlapped with the target box IS the target's own detection.
    static let selfOverlapIoU = 0.6

    /// - Parameters:
    ///   - poses: every pose Vision found in the frame (any order).
    ///   - target: the selected player's box, only meaningful when `targetIsObserved`.
    ///   - targetIsObserved: the tracker committed a detection for the target THIS frame.
    ///   - otherPeople: every person box detected this frame (the target's own may be included).
    public static func associate(
        poses: [[PoseKeypoint]],
        target: NormalizedRect?,
        targetIsObserved: Bool,
        otherPeople: [NormalizedRect]
    ) -> PoseAssociationResult {
        guard targetIsObserved, let target else {
            return PoseAssociationResult(outcome: .noObservedTarget, keypoints: nil)
        }
        let usable = poses.filter { $0.count >= minimumJoints }
        guard !usable.isEmpty else {
            return PoseAssociationResult(outcome: .noPoses, keypoints: nil)
        }

        let rivals = otherPeople.filter { $0.iou(with: target) < selfOverlapIoU }
        var owned: [(pose: [PoseKeypoint], score: Double)] = []
        var contested = false

        for pose in usable {
            let targetScore = insideFraction(of: pose, in: target)
            let rivalScore = rivals.map { insideFraction(of: pose, in: $0) }.max() ?? 0
            guard targetScore >= minimumInsideFraction else { continue }
            if targetScore - rivalScore >= minimumMargin {
                owned.append((pose, targetScore))
            } else {
                contested = true   // it sits in the target's box but another person's box explains it too
            }
        }

        switch owned.count {
        case 0:
            return PoseAssociationResult(outcome: contested ? .ambiguous : .foreign, keypoints: nil)
        case 1:
            return PoseAssociationResult(outcome: .matched, keypoints: owned[0].pose)
        default:
            let ranked = owned.sorted { $0.score > $1.score }
            if ranked[0].score - ranked[1].score >= minimumPoseLead {
                return PoseAssociationResult(outcome: .matched, keypoints: ranked[0].pose)
            }
            return PoseAssociationResult(outcome: .ambiguous, keypoints: nil)
        }
    }

    private static func insideFraction(of pose: [PoseKeypoint], in box: NormalizedRect) -> Double {
        guard !pose.isEmpty else { return 0 }
        let padX = box.width * boxPadding
        let padY = box.height * boxPadding
        let minX = box.x - padX, maxX = box.x + box.width + padX
        let minY = box.y - padY, maxY = box.y + box.height + padY
        let inside = pose.filter { $0.x >= minX && $0.x <= maxX && $0.y >= minY && $0.y <= maxY }.count
        return Double(inside) / Double(pose.count)
    }
}
