//
//  FrameTelemetry.swift
//  KemetFCTracker
//
//  Builds ONE frame's telemetry from the trackers' state with honest
//  semantics. A field is nil when the system does not KNOW the value — never
//  a stale box, a predicted position, a constant placeholder id or someone
//  else's pose:
//
//    * player fields carry a measurement only when the tracker committed a
//      detection THIS frame (locked/reacquired with confidence > 0). While the
//      target is temporarily lost / searching / lost, the box, confidence and
//      track id are nil and `trackingStatus` says why.
//    * ball fields carry a measurement only when the ball was detected this
//      frame; the ball track id is the tracker's actual track identity.
//    * pose keypoints are attached only through PoseAssociator (the selected
//      player's own pose, or nothing).
//    * `identityAmbiguous` mirrors PlayerTracker.lastMatchWasAmbiguous, so the
//      backend quality gate can force coach review.
//

import Foundation

public struct FrameTelemetry: Equatable, Sendable {
    public var playerBox: NormalizedRect?
    public var playerConfidence: Double?
    public var playerTrackId: Int?
    public var ballBox: NormalizedRect?
    public var ballConfidence: Double?
    public var ballTrackId: Int?
    /// Empty = unknown / not associated (never "some pose from the frame").
    public var poseKeypoints: [PoseKeypoint]
    public var poseAssociation: PoseAssociationResult.Outcome
    public var trackingStatus: String
    public var trackingMode: String
    public var identityAmbiguous: Bool?
}

public enum FrameTelemetryAssembler {
    /// A tracker commit this frame: a confident detection was matched (not a miss, not a prediction).
    public static func isPlayerObserved(state: PlayerTrackState, confidence: Double) -> Bool {
        (state == .locked || state == .reacquired) && confidence > 0
    }

    public static func assemble(
        playerState: PlayerTrackState?,
        playerBox: NormalizedRect?,
        playerConfidence: Double,
        playerTargetTrackId: Int?,
        playerMatchWasAmbiguous: Bool,
        ballBox: NormalizedRect?,
        ballConfidence: Double,
        ballTrackId: Int?,
        poses: [[PoseKeypoint]],
        personBoxes: [NormalizedRect],
        mode: TrackingMode
    ) -> FrameTelemetry {
        let observed = playerState.map { isPlayerObserved(state: $0, confidence: playerConfidence) } ?? false

        let association = PoseAssociator.associate(
            poses: poses, target: playerBox, targetIsObserved: observed, otherPeople: personBoxes
        )

        let ballDetected = ballBox != nil && ballConfidence > 0
        return FrameTelemetry(
            playerBox: observed ? playerBox : nil,
            playerConfidence: observed ? playerConfidence : nil,
            playerTrackId: observed ? playerTargetTrackId : nil,
            ballBox: ballDetected ? ballBox : nil,
            ballConfidence: ballDetected ? ballConfidence : nil,
            ballTrackId: ballDetected ? ballTrackId : nil,
            poseKeypoints: association.keypoints ?? [],
            poseAssociation: association.outcome,
            trackingStatus: (playerState ?? .lost).rawValue,
            trackingMode: mode.rawValue,
            identityAmbiguous: playerState == nil ? nil : playerMatchWasAmbiguous
        )
    }
}
