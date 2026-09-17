//
//  TrackTypes.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, written against DockKit/Vision APIs as
//  documented at developer.apple.com (verified live during this session,
//  September 2026). NOT compiled or run — this repository's environment
//  has no full Xcode install (Command Line Tools only) and no physical
//  Insta360 Flow 2 Pro. See ios/KemetFCTracker/README.md for the exact
//  verification status of every file in this module and the checklist
//  to run on real hardware before shipping.
//
//  Shared value types used across the tracking pipeline. Deliberately
//  plain structs (Codable, Sendable) with no Vision/DockKit/AVFoundation
//  types leaking through — this is the seam that lets TrackingCoordinator
//  and the unit tests in Tests/ be tested without a camera or a real dock.
//

import Foundation

/// Normalized (0...1) image-space rectangle — matches Vision's own
/// bounding-box convention so no per-frame remapping is needed when
/// converting a VNObservation into ours.
public struct NormalizedRect: Codable, Sendable, Equatable {
    public var x: Double
    public var y: Double
    public var width: Double
    public var height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }

    public var center: NormalizedPoint {
        NormalizedPoint(x: x + width / 2, y: y + height / 2)
    }

    /// Intersection-over-union — the primary signal for matching a new
    /// detection to an existing track across consecutive frames.
    public func iou(with other: NormalizedRect) -> Double {
        let x1 = max(x, other.x)
        let y1 = max(y, other.y)
        let x2 = min(x + width, other.x + other.width)
        let y2 = min(y + height, other.y + other.height)
        let intersection = max(0, x2 - x1) * max(0, y2 - y1)
        guard intersection > 0 else { return 0 }
        let union = (width * height) + (other.width * other.height) - intersection
        return union > 0 ? intersection / union : 0
    }
}

public struct NormalizedPoint: Codable, Sendable, Equatable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }

    public func distance(to other: NormalizedPoint) -> Double {
        let dx = x - other.x
        let dy = y - other.y
        return (dx * dx + dy * dy).squareRoot()
    }
}

/// A named 2D body-pose keypoint in normalized image space, matching
/// Vision's VNHumanBodyPoseObservation.JointName raw values so
/// PoseEstimator can serialize them directly.
public struct PoseKeypoint: Codable, Sendable, Equatable {
    public var name: String
    public var x: Double
    public var y: Double
    public var confidence: Double

    public init(name: String, x: Double, y: Double, confidence: Double) {
        self.name = name
        self.x = x
        self.y = y
        self.confidence = confidence
    }
}

/// One detected person in a frame, before track-ID assignment.
public struct PersonDetection: Sendable {
    public var boundingBox: NormalizedRect
    public var confidence: Double

    public init(boundingBox: NormalizedRect, confidence: Double) {
        self.boundingBox = boundingBox
        self.confidence = confidence
    }
}

/// One detected ball in a frame, before track continuity is applied.
public struct BallDetection: Sendable {
    public var boundingBox: NormalizedRect
    public var confidence: Double

    public init(boundingBox: NormalizedRect, confidence: Double) {
        self.boundingBox = boundingBox
        self.confidence = confidence
    }
}

/// Tracking lifecycle state for the coach-selected player — the state
/// machine SubjectReacquisition.swift drives and the UI displays
/// verbatim (LOCKED / TEMPORARILY LOST / SEARCHING / REACQUIRED / LOST).
public enum PlayerTrackState: String, Codable, Sendable {
    case locked
    case temporarilyLost = "temporarily_lost"
    case searching
    case reacquired
    case lost
}

public enum BallTrackState: String, Codable, Sendable {
    case tracked
    case lost
    case searching
}

public enum GimbalConnectionState: String, Codable, Sendable {
    case disconnected
    case connected
    case trackingActive = "tracking_active"
    case trackingLost = "tracking_lost"
    case reconnecting
}

public enum TrackingMode: String, Codable, Sendable, CaseIterable {
    case playerLock = "player_lock"
    case ballTrack = "ball_track"
    case smartSoccer = "smart_soccer"
}

public enum TrackingSpeedProfile: String, Codable, Sendable, CaseIterable {
    case slow
    case normal
    case sport
    case custom
}

/// One outgoing telemetry sample — the exact shape
/// IngestTrackingSamplesSchema (app/api_schemas.py) expects, so
/// TelemetryUploader can serialize this directly with no translation
/// layer, and a backend schema change is felt immediately at compile
/// time on this end too (once this target is ever actually built).
public struct TrackingSample: Codable, Sendable {
    public var tSeconds: Double
    public var playerBbox: [Double]?
    public var playerCenter: [Double]?
    public var playerConfidence: Double?
    public var playerTrackId: Int?
    public var ballBbox: [Double]?
    public var ballCenter: [Double]?
    public var ballConfidence: Double?
    public var ballTrackId: Int?
    public var poseKeypoints: [PoseKeypointWire]?
    public var gimbalState: String
    public var trackingMode: String
    public var trackingStatus: String

    enum CodingKeys: String, CodingKey {
        case tSeconds = "t_seconds"
        case playerBbox = "player_bbox"
        case playerCenter = "player_center"
        case playerConfidence = "player_confidence"
        case playerTrackId = "player_track_id"
        case ballBbox = "ball_bbox"
        case ballCenter = "ball_center"
        case ballConfidence = "ball_confidence"
        case ballTrackId = "ball_track_id"
        case poseKeypoints = "pose_keypoints"
        case gimbalState = "gimbal_state"
        case trackingMode = "tracking_mode"
        case trackingStatus = "tracking_status"
    }
}

/// Wire-format pose keypoint — a plain dict-shaped struct matching the
/// backend's `[{"name": ..., "x": ..., "y": ..., "confidence": ...}]`
/// convention exactly (see app/services/tracking_features.py).
public struct PoseKeypointWire: Codable, Sendable {
    public var name: String
    public var x: Double
    public var y: Double
    public var confidence: Double
}
