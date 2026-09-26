//
//  PlayerDetector.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Multi-person detection using Apple's built-in Vision framework — NOT a
//  custom-trained model. Verified live against developer.apple.com this
//  session:
//
//    VNDetectHumanRectanglesRequest    (iOS 13.0) — person bounding boxes
//    VNDetectHumanBodyPoseRequest      (iOS 14.0) — 2D body-pose keypoints
//    VNHumanBodyPoseObservation.JointName cases: leftShoulder,
//      rightShoulder, leftElbow, rightElbow, leftWrist, rightWrist,
//      leftHip, rightHip, leftKnee, rightKnee, leftAnkle, rightAnkle,
//      neck, nose, leftEye, rightEye, leftEar, rightEar, root
//      (no dedicated "feet" joint — ankle is used as the feet proxy,
//      documented explicitly rather than silently invented)
//
//  Both run entirely on-device via the Neural Engine when available.
//  Deliberately NOT facial recognition/identification — these requests
//  answer "where are the people/joints in this frame," never "who is
//  this," matching spec section 7 ("Do not use database-wide facial
//  recognition") and section 29 (privacy).
//

import Foundation
import Vision

public struct PoseObservationResult {
    public var personIndex: Int
    public var boundingBox: NormalizedRect
    public var keypoints: [PoseKeypoint]
}

public final class PlayerDetector {
    /// Reported as `player_detector_version`/`pose_model_version` at
    /// tracking-session-create time (spec section 14: model-version
    /// traceability) — identifies these as Apple's built-in Vision
    /// requests running on the OS's bundled model, NOT a KEMET-trained
    /// one, so telemetry never implies a custom player/pose model exists.
    public static let detectorVersion = "apple-vision-VNDetectHumanRectanglesRequest-ios13"
    public static let poseModelVersion = "apple-vision-VNDetectHumanBodyPoseRequest-ios14"

    /// VNDetectHumanBodyPoseRequest joint names KEMET actually uses for
    /// interpretable movement metrics (spec section 3) — a deliberate
    /// subset, not every joint Vision can report, to keep
    /// TrackingSample.pose_keypoints compact (spec section 19: "Do not
    /// unnecessarily store every raw ML tensor").
    public static let trackedJointNames: [VNHumanBodyPoseObservation.JointName] = [
        .leftShoulder, .rightShoulder,
        .leftElbow, .rightElbow,
        .leftWrist, .rightWrist,
        .leftHip, .rightHip,
        .leftKnee, .rightKnee,
        .leftAnkle, .rightAnkle, // ankle == feet proxy, no dedicated foot joint in Vision
    ]

    private let humanRectanglesRequest = VNDetectHumanRectanglesRequest()
    private let bodyPoseRequest = VNDetectHumanBodyPoseRequest()

    public init() {
        humanRectanglesRequest.upperBodyOnly = false
    }

    /// Every person Vision finds in this frame — the caller (
    /// TrackingCoordinator) is responsible for matching these against
    /// existing tracks and for the coach's initial tap-to-select; this
    /// method never itself decides "this is player 2."
    public func detectPeople(in pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation) throws -> [PersonDetection] {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        try handler.perform([humanRectanglesRequest])

        guard let results = humanRectanglesRequest.results else { return [] }
        return results.map { observation in
            PersonDetection(
                boundingBox: NormalizedRect(
                    x: observation.boundingBox.origin.x,
                    y: 1 - observation.boundingBox.origin.y - observation.boundingBox.height, // Vision is bottom-left origin
                    width: observation.boundingBox.width,
                    height: observation.boundingBox.height
                ),
                confidence: Double(observation.confidence)
            )
        }
    }

    /// EVERY pose Vision finds in the frame, in Vision's own (arbitrary) order,
    /// as top-left-origin normalized keypoints. Which of them — if any — belongs
    /// to the coach-selected player is decided by PoseAssociator; the order here
    /// carries no meaning. (This replaces detectPose(in:), which returned only
    /// `results.first` and let the wrong child's pose reach the target's telemetry.)
    public func detectPoses(in pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation) throws -> [[PoseKeypoint]] {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        try handler.perform([bodyPoseRequest])

        return try (bodyPoseRequest.results ?? []).map { observation in
            let points = try observation.recognizedPoints(.all)
            return Self.trackedJointNames.compactMap { jointName in
                guard let point = points[jointName], point.confidence > 0.1 else { return nil }
                return PoseKeypoint(
                    name: jointName.rawValue.rawValue,
                    x: point.location.x,
                    y: 1 - point.location.y, // Vision is bottom-left origin
                    confidence: Double(point.confidence)
                )
            }
        }
    }
}
