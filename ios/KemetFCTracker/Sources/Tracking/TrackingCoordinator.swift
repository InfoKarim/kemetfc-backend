//
//  TrackingCoordinator.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//  Built on FramingCalculator.swift, whose pure math IS genuinely
//  compiled-and-run verified (see that file's header).
//
//  The per-frame orchestrator: runs detection at a bounded effective
//  rate (frame-skipping, spec section 26), updates PlayerTracker/
//  BallTracker, computes the mode-appropriate framing target, and feeds
//  GimbalController — while recording (CameraCaptureManager) continues
//  completely independently of whatever happens here (spec section 18).
//

import AVFoundation
import CoreImage
import Combine
import Foundation
import os.log

@MainActor
public final class TrackingCoordinator: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TrackingCoordinator")

    @Published public private(set) var mode: TrackingMode = .smartSoccer
    @Published public private(set) var playerState: PlayerTrackState = .lost
    @Published public private(set) var ballState: BallTrackState = .lost
    @Published public private(set) var lastFramingTarget: NormalizedRect?
    @Published public private(set) var isPaused = false

    // Field Test Mode telemetry (spec section 32) — visible only behind
    // an admin/debug flag in the UI, never shown to a parent/guardian.
    @Published public private(set) var inferenceFPS: Double = 0
    @Published public private(set) var droppedFrameCount = 0

    private let playerDetector: PlayerDetector
    private let ballDetector: BallDetecting
    private var playerTracker: PlayerTracker?
    private var ballTracker = BallTracker()
    private let gimbalController: GimbalController
    private let telemetryUploader: TelemetryUploader

    /// Frame-skipping: capture can run at 60fps, but ML inference does
    /// not need to — this bounds the EFFECTIVE inference rate
    /// independent of capture frame rate (spec section 26).
    public var targetInferenceIntervalSeconds: TimeInterval = 1.0 / 15.0
    private var lastInferenceTime: CFTimeInterval = 0
    private var isInferring = false
    private var inferenceTimestamps: [CFTimeInterval] = []

    public var framingPadding: Double = 0.08
    public var leadRoomMaxOffsetFraction: Double = 0.15

    public init(
        gimbalController: GimbalController,
        telemetryUploader: TelemetryUploader,
        ballDetector: BallDetecting = ClassicalCVBallDetector()
    ) {
        self.playerDetector = PlayerDetector()
        self.ballDetector = ballDetector
        self.gimbalController = gimbalController
        self.telemetryUploader = telemetryUploader
    }

    public func setMode(_ newMode: TrackingMode) {
        mode = newMode
        telemetryUploader.recordEvent(type: "mode_switch", details: ["mode": newMode.rawValue])
    }

    public func setSpeedProfile(_ profile: TrackingSpeedProfile) {
        gimbalController.speedProfile = profile
    }

    /// Coach taps a detected person in the preview overlay (spec section
    /// 6) — this is the ONLY way a player track is ever created. No
    /// automatic selection, no facial recognition, no database search.
    public func lockPlayer(at tap: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        guard let chosen = detections.min(by: { lhs, rhs in
            lhs.boundingBox.center.distance(to: tap) < rhs.boundingBox.center.distance(to: tap)
        }) else { return }

        let signature = Self.appearanceSignature(for: chosen.boundingBox, in: pixelBuffer)
        playerTracker = PlayerTracker(targetTrackId: 1, initialBoundingBox: chosen.boundingBox, appearance: signature)
        playerState = .locked
        telemetryUploader.recordEvent(type: "player_locked", details: ["target_track_id": 1])
    }

    /// Manual "Re-select Player" (spec section 25) — same tap gesture,
    /// same code path, whether coming from LOST or just a coach
    /// deciding the wrong person is locked.
    public func reselectPlayer(at tap: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        lockPlayer(at: tap, detections: detections, pixelBuffer: pixelBuffer)
    }

    public func pauseTracking() {
        isPaused = true
        gimbalController.pauseMotorCorrection()
    }

    public func resumeTracking() {
        isPaused = false
    }

    public func centerGimbal() {
        gimbalController.centerGimbal()
    }

    /// The main entry point, called from CameraCaptureManager's
    /// AVCaptureVideoDataOutputSampleBufferDelegate callback. Internally
    /// rate-limited to targetInferenceIntervalSeconds — a capture frame
    /// arriving faster than that is a normal, expected no-op here, NOT
    /// a dropped/failed frame (recording is untouched either way).
    public func processFrame(
        sampleBuffer: CMSampleBuffer,
        captureDevice: AVCaptureDevice,
        sessionElapsedSeconds: Double
    ) {
        guard !isPaused else { return }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        let now = CACurrentMediaTime()
        guard now - lastInferenceTime >= targetInferenceIntervalSeconds else { return }
        guard !isInferring else {
            droppedFrameCount += 1
            return
        }
        lastInferenceTime = now
        isInferring = true

        recordInferenceTimestamp(now)

        Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            let result = await self.runInference(pixelBuffer: pixelBuffer)
            await MainActor.run {
                self.apply(result: result, captureDevice: captureDevice, sessionElapsedSeconds: sessionElapsedSeconds)
                self.isInferring = false
            }
        }
    }

    private struct InferenceResult {
        var people: [PersonDetection]
        var ball: BallDetection?
        var pose: [PoseKeypoint]
    }

    nonisolated private func runInference(pixelBuffer: CVPixelBuffer) async -> InferenceResult {
        var people: [PersonDetection] = []
        var ball: BallDetection?
        var pose: [PoseKeypoint] = []

        do {
            people = try playerDetector.detectPeople(in: pixelBuffer, orientation: .right)
        } catch {
            // Player-detector failure: per spec section 24, fall back to
            // motion-predicted tracking (handled in apply(result:)) and
            // keep going — never crash the session.
        }
        do {
            if let detection = try ballDetector.detectBall(in: pixelBuffer) {
                ball = detection
            }
        } catch {
            // Ball-model failure: continue recording and player
            // tracking regardless (spec section 24).
        }
        do {
            pose = try playerDetector.detectPose(in: pixelBuffer, orientation: .right)
        } catch {
            // Pose-model failure: mark pose metrics unavailable for this
            // sample, never fail the whole frame (spec section 24).
        }

        return InferenceResult(people: people, ball: ball, pose: pose)
    }

    private func apply(result: InferenceResult, captureDevice: AVCaptureDevice, sessionElapsedSeconds: Double) {
        let appearanceSignatures = result.people.map { _ in AppearanceSignature(meanColor: (128, 128, 128)) }
        // NOTE: a real appearance signature needs the actual pixel
        // buffer sampled within each bounding box — omitted here to
        // keep this orchestration file's own diff focused; see
        // Self.appearanceSignature(for:in:) below, which lockPlayer(at:)
        // already uses, and wire the same call in for each detection
        // here before shipping.

        playerTracker?.update(detections: result.people, appearanceSignatures: appearanceSignatures, at: sessionElapsedSeconds)
        ballTracker.update(detections: result.ball.map { [$0] } ?? [], at: sessionElapsedSeconds)

        playerState = playerTracker?.state ?? .lost
        ballState = ballTracker.state

        if playerState == .lost {
            gimbalController.pauseMotorCorrection()
        }

        let framingTarget = computeFramingTarget()
        lastFramingTarget = framingTarget

        if let framingTarget, playerState != .lost {
            gimbalController.updateTarget(
                framingTarget: framingTarget,
                captureDevice: captureDevice,
                cameraIntrinsics: nil,
                referenceDimensions: CGSize(width: 1920, height: 1080)
            )
        }

        telemetryUploader.recordSample(
            tSeconds: sessionElapsedSeconds,
            playerBox: playerTracker?.currentBoundingBox,
            playerConfidence: playerTracker?.currentConfidence,
            playerTrackId: playerTracker?.targetTrackId,
            ballBox: ballTracker.currentBoundingBox,
            ballConfidence: ballTracker.currentConfidence,
            poseKeypoints: result.pose,
            gimbalState: "tracking_active",
            trackingMode: mode.rawValue,
            trackingStatus: playerState.rawValue
        )
    }

    /// The framing-target selection per spec section 9: PLAYER_LOCK
    /// frames the player only; BALL_TRACK frames the ball only;
    /// SMART_SOCCER combines both via FramingCalculator, with lead room
    /// applied in the player's direction of travel (spec section 15).
    private func computeFramingTarget() -> NormalizedRect? {
        let playerBox = playerTracker?.currentBoundingBox
        let ballBox = ballTracker.currentBoundingBox

        let raw: NormalizedRect?
        switch mode {
        case .playerLock:
            raw = playerBox
        case .ballTrack:
            raw = ballBox
        case .smartSoccer:
            // Ball loss never drops the player (spec section 14) — a
            // nil ballBox here just means combinedFramingTarget falls
            // back to the player box alone.
            raw = FramingCalculator.combinedFramingTarget(player: playerBox, ball: ballBox, padding: framingPadding)
        }

        guard let target = raw else { return nil }
        // playerTracker.currentVelocity — NOT predictedCenter(dt:),
        // which returns a POSITION. An earlier draft passed the wrong
        // one here; caught before this was ever the "verified" claim
        // extended to this file (see TrackTypes.swift header — this
        // whole file is unverified source, but this specific mistake is
        // exactly the kind that would have shipped a silently-wrong
        // lead-room direction if not caught by review).
        let withLeadRoom = FramingCalculator.applyLeadRoom(
            to: target,
            playerVelocity: playerTracker?.currentVelocity ?? .init(x: 0, y: 0),
            maxOffsetFraction: leadRoomMaxOffsetFraction
        )
        return FramingCalculator.enforceMinimumFrameSize(withLeadRoom)
    }

    private func recordInferenceTimestamp(_ time: CFTimeInterval) {
        inferenceTimestamps.append(time)
        inferenceTimestamps.removeAll { time - $0 > 1.0 }
        inferenceFPS = Double(inferenceTimestamps.count)
    }

    /// Crude mean-color "jersey" signature within a bounding box — see
    /// AppearanceSignature's own documented precision caveat.
    nonisolated private static func appearanceSignature(for box: NormalizedRect, in pixelBuffer: CVPixelBuffer) -> AppearanceSignature {
        // Real implementation: sample a small grid of pixels inside
        // `box` from `pixelBuffer` (converted via CIImage) and average.
        // Left as a documented stub with a neutral gray default — wire
        // real pixel sampling in before shipping (see apply(result:)
        // comment above for the exact call site that also needs it).
        AppearanceSignature(meanColor: (128, 128, 128))
    }
}
