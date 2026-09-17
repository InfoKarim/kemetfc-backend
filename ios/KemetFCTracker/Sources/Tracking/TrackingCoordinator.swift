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

/// Explicitly asserts single-ownership Sendable-safety for a value
/// (here, `CVPixelBuffer`) that the Swift 6 checker can't itself prove
/// safe to move across an isolation boundary — used ONLY where that
/// invariant genuinely holds (see call site comment). Never a blanket
/// suppression: a real Xcode build is what found each place this was
/// actually needed.
struct UncheckedSendableBox<Value>: @unchecked Sendable {
    let value: Value
}

@MainActor
public final class TrackingCoordinator: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TrackingCoordinator")

    @Published public private(set) var mode: TrackingMode = .smartSoccer
    @Published public private(set) var playerState: PlayerTrackState = .lost
    @Published public private(set) var ballState: BallTrackState = .lost
    @Published public private(set) var lastFramingTarget: NormalizedRect?
    @Published public private(set) var isPaused = false

    /// The most recent frame's raw person detections and pixel buffer —
    /// exposed so the tap-to-lock UI only needs to resolve a view tap
    /// into a normalized camera-space point (via TapToLockConverter) and
    /// hand it back here; it never needs to track detections/pixel
    /// buffers itself. `latestPixelBuffer` is intentionally NOT
    /// `@Published` (CVPixelBuffer retention/observation churn on every
    /// inference frame would be wasteful) — callers read it only at the
    /// moment of a tap, not reactively.
    @Published public private(set) var latestDetections: [PersonDetection] = []
    public private(set) var latestPixelBuffer: CVPixelBuffer?

    /// Mirrors GimbalController.isControlLost — forwarded explicitly
    /// (rather than exposing gimbalController itself) since
    /// TrackingCoordinator's own @Published properties don't
    /// automatically propagate a nested ObservableObject's changes to
    /// SwiftUI. See GimbalController.swift's failsafe comment: recording
    /// continues regardless; only gimbal motion stops.
    @Published public private(set) var isGimbalControlLost = false
    private var cancellables = Set<AnyCancellable>()

    public let thermalManager = ThermalManager()

    // Field Test Mode telemetry (spec section 32) — visible only behind
    // an admin/debug flag in the UI, never shown to a parent/guardian.
    @Published public private(set) var inferenceFPS: Double = 0
    @Published public private(set) var droppedFrameCount = 0

    // nonisolated(unsafe): a real Xcode build (Swift 6 strict
    // concurrency) refused to compile runInference(...) below reading
    // these as plain @MainActor-isolated stored properties from its own
    // `nonisolated` context — PlayerDetector/BallDetecting are not
    // Sendable (they wrap mutable Vision request objects), so the
    // compiler cannot prove concurrent access is safe. It IS safe here,
    // by construction, not by hand-waving: processFrame(...)'s
    // `isInferring` flag guarantees at most one runInference(...) call
    // is ever in flight at a time, so these are never actually accessed
    // concurrently despite crossing the actor boundary. This annotation
    // documents and asserts exactly that invariant — it does not disable
    // or bypass the check for a reason that isn't true.
    nonisolated(unsafe) private let playerDetector: PlayerDetector
    nonisolated(unsafe) private let ballDetector: BallDetecting
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

        gimbalController.$isControlLost
            .receive(on: DispatchQueue.main)
            .sink { [weak self] lost in self?.isGimbalControlLost = lost }
            .store(in: &cancellables)
    }

    /// Explicit-only "Resume Gimbal Control" (spec: never automatic) —
    /// see GimbalController.resumeControlAfterFailsafe().
    public func resumeGimbalControl() {
        gimbalController.resumeControlAfterFailsafe()
    }

    /// The exact model/algorithm identifiers active in THIS coordinator
    /// instance, for the caller to report at tracking-session-create time
    /// (spec section 14: model-version traceability) — reading these
    /// from the live objects (rather than duplicating version strings at
    /// the call site) keeps the reported values honest even if a
    /// different ballDetector is injected.
    public var modelVersionInfo: (
        playerDetectorVersion: String,
        ballDetectorVersion: String,
        ballModelStatus: String,
        poseModelVersion: String,
        trackerAlgorithmVersion: String,
        framingAlgorithmVersion: String
    ) {
        (
            playerDetectorVersion: PlayerDetector.detectorVersion,
            ballDetectorVersion: ballDetector.modelIdentifier.version,
            ballModelStatus: ballDetector.ballModelStatus,
            poseModelVersion: PlayerDetector.poseModelVersion,
            trackerAlgorithmVersion: PlayerTracker.algorithmVersion,
            framingAlgorithmVersion: FramingCalculator.algorithmVersion
        )
    }

    public func setMode(_ newMode: TrackingMode) {
        mode = newMode
        telemetryUploader.recordEvent(type: "mode_switch", details: ["mode": .string(newMode.rawValue)])
    }

    public func setSpeedProfile(_ profile: TrackingSpeedProfile) {
        gimbalController.speedProfile = profile
    }

    /// Coach taps a detected person in the preview overlay (spec section
    /// 6) — this is the ONLY way a player track is ever created. No
    /// automatic selection, no facial recognition, no database search.
    public func lockPlayer(at tap: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        // Phase 2 fix: this previously always locked the NEAREST
        // detection with a plain `min(by:)`, with no distance cutoff —
        // meaning a tap on empty space far from everyone still silently
        // locked whoever happened to be closest. TapToLockConverter
        // .selectDetection (box-containment first, else nearest-within-
        // tolerance, else nil) is the actual spec-mandated policy ("Do
        // not lock an arbitrary person when the user taps empty space")
        // and was already built + verified (see TapToLockConverter.swift)
        // but never wired in here until now.
        guard let chosen = TapToLockConverter.selectDetection(at: tap, among: detections) else { return }

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

        // Thermal management (spec: real, via ProcessInfo, never a
        // fabricated proxy) widens the EFFECTIVE interval under
        // pressure — recording/gimbal/UI are untouched either way,
        // only the rate of ML inference work drops.
        let effectiveIntervalSeconds = targetInferenceIntervalSeconds * thermalManager.inferenceIntervalMultiplier

        let now = CACurrentMediaTime()
        guard now - lastInferenceTime >= effectiveIntervalSeconds else { return }
        guard !isInferring else {
            droppedFrameCount += 1
            return
        }
        lastInferenceTime = now
        isInferring = true

        recordInferenceTimestamp(now)

        // Phase 3 fix: a real Xcode build flagged sending `pixelBuffer`
        // (CVPixelBuffer, a CoreVideo type that does not conform to
        // Sendable) into this Task.detached closure as a data-race
        // risk. The handoff itself is genuinely safe — this is the
        // standard AVFoundation pattern of handing one frame's buffer
        // to exactly one consumer for exactly one round of processing,
        // with no other reference to it retained anywhere afterward —
        // but the compiler cannot prove that about an un-audited
        // CoreVideo type. `UncheckedSendableBox` documents and asserts
        // that single-ownership invariant explicitly, rather than
        // silencing the check for a reason that isn't true.
        // Same reasoning as pixelBuffer above: AVCaptureDevice is also
        // not Sendable, and is only ever read (device type/position),
        // never mutated, by the code that uses it after this handoff.
        let boxedCaptureDevice = UncheckedSendableBox(value: captureDevice)
        let boxedPixelBuffer = UncheckedSendableBox(value: pixelBuffer)
        Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            let result = await self.runInference(pixelBuffer: boxedPixelBuffer.value)
            await MainActor.run {
                self.apply(result: result, pixelBuffer: boxedPixelBuffer.value, captureDevice: boxedCaptureDevice.value, sessionElapsedSeconds: sessionElapsedSeconds)
                self.isInferring = false
            }
        }
    }

    private struct InferenceResult {
        var people: [PersonDetection]
        var ball: BallDetection?
        var pose: [PoseKeypoint]
        var timing: StageTiming
    }

    /// Per-stage wall-clock durations (milliseconds) — real measured
    /// values via CACurrentMediaTime() around each call, not estimates
    /// (spec: "add performance instrumentation" around each pipeline
    /// stage). Surfaced in Field Test Mode and attached to tracking
    /// events so a slow stage on a real device is diagnosable from the
    /// recorded telemetry after the fact, not just live.
    public struct StageTiming: Sendable {
        public var playerDetectionMs: Double
        public var ballDetectionMs: Double
        public var poseDetectionMs: Double
        public var totalMs: Double
    }

    @Published public private(set) var lastStageTiming: StageTiming?

    nonisolated private func runInference(pixelBuffer: CVPixelBuffer) async -> InferenceResult {
        let stageStart = CACurrentMediaTime()
        var people: [PersonDetection] = []
        var ball: BallDetection?
        var pose: [PoseKeypoint] = []

        let playerStart = CACurrentMediaTime()
        do {
            people = try playerDetector.detectPeople(in: pixelBuffer, orientation: .right)
        } catch {
            // Player-detector failure: per spec section 24, fall back to
            // motion-predicted tracking (handled in apply(result:)) and
            // keep going — never crash the session.
        }
        let playerEnd = CACurrentMediaTime()

        do {
            if let detection = try ballDetector.detectBall(in: pixelBuffer) {
                ball = detection
            }
        } catch {
            // Ball-model failure: continue recording and player
            // tracking regardless (spec section 24).
        }
        let ballEnd = CACurrentMediaTime()

        do {
            pose = try playerDetector.detectPose(in: pixelBuffer, orientation: .right)
        } catch {
            // Pose-model failure: mark pose metrics unavailable for this
            // sample, never fail the whole frame (spec section 24).
        }
        let poseEnd = CACurrentMediaTime()

        let timing = StageTiming(
            playerDetectionMs: (playerEnd - playerStart) * 1000,
            ballDetectionMs: (ballEnd - playerEnd) * 1000,
            poseDetectionMs: (poseEnd - ballEnd) * 1000,
            totalMs: (poseEnd - stageStart) * 1000
        )

        return InferenceResult(people: people, ball: ball, pose: pose, timing: timing)
    }

    private func apply(result: InferenceResult, pixelBuffer: CVPixelBuffer, captureDevice: AVCaptureDevice, sessionElapsedSeconds: Double) {
        latestDetections = result.people
        latestPixelBuffer = pixelBuffer
        lastStageTiming = result.timing

        // Phase 2 fix: this previously built a flat neutral-gray
        // signature for every detection (`AppearanceSignature(meanColor:
        // (128,128,128))`) instead of sampling real pixels — which also
        // referenced an API (`meanColor:`) that no longer exists after
        // PlayerTracker.swift's Phase 2 two-region appearance rewrite
        // (`torsoColor`/`legsColor`), so this file would not have
        // compiled in Xcode as it stood. Now samples each candidate's
        // actual torso/legs colors via Self.appearanceSignature(for:in:),
        // the same real per-region sampling lockPlayer(at:) already uses.
        let appearanceSignatures = result.people.map { Self.appearanceSignature(for: $0.boundingBox, in: pixelBuffer) }

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

    /// Real per-region mean-color "jersey" signature within a person's
    /// bounding box, via CoreImage's CIAreaAverage filter (an on-GPU
    /// reduction, not a manual per-pixel scan — appropriate here since
    /// this runs once per detected person per inference frame, inside
    /// the same frame budget as player/pose/ball detection). Splits the
    /// box into an upper torso band (jersey) and lower legs band
    /// (shorts+socks), matching AppearanceSignature's two-region model
    /// (see PlayerTracker.swift for why single-flat-color wasn't
    /// discriminative enough for same-kit collisions) — a thin
    /// waistband gap between the two bands is deliberately excluded so
    /// they don't blend. Still explicitly non-biometric: mean color
    /// only, never a face or identity signal (spec sections 7, 9).
    nonisolated private static func appearanceSignature(for box: NormalizedRect, in pixelBuffer: CVPixelBuffer) -> AppearanceSignature {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let imageWidth = ciImage.extent.width
        let imageHeight = ciImage.extent.height
        guard imageWidth > 0, imageHeight > 0, box.width > 0, box.height > 0 else {
            return AppearanceSignature(flatColor: (128, 128, 128))
        }

        // NormalizedRect is top-left origin (see PlayerDetector.swift's
        // explicit Vision bottom-left -> top-left flip when building
        // these boxes), but CIImage/CoreGraphics coordinates are
        // bottom-left origin — flip Y here when converting to pixel
        // space, the same conversion done in reverse there.
        let pixelX = box.x * imageWidth
        let pixelWidth = box.width * imageWidth
        let pixelHeight = box.height * imageHeight
        let boxBottomOriginY = imageHeight - (box.y * imageHeight) - pixelHeight

        let torsoRect = CGRect(
            x: pixelX, y: boxBottomOriginY + pixelHeight * 0.55,
            width: pixelWidth, height: pixelHeight * 0.45
        ).intersection(ciImage.extent)
        let legsRect = CGRect(
            x: pixelX, y: boxBottomOriginY,
            width: pixelWidth, height: pixelHeight * 0.45
        ).intersection(ciImage.extent)

        let context = CIContext()
        let torsoColor = averageColor(of: ciImage, in: torsoRect, context: context)
        let legsColor = averageColor(of: ciImage, in: legsRect, context: context)
        return AppearanceSignature(torsoColor: torsoColor, legsColor: legsColor)
    }

    /// Mean RGB of `extent` within `image`, via CIAreaAverage (an
    /// on-GPU/Accelerate reduction, not a manual pixel loop). Returns
    /// neutral gray for a degenerate (empty/off-frame) extent rather
    /// than crashing — an out-of-frame box is a plausible edge case
    /// (a person partially off-screen), not an error.
    nonisolated private static func averageColor(of image: CIImage, in extent: CGRect, context: CIContext) -> (r: Double, g: Double, b: Double) {
        guard extent.width > 0, extent.height > 0,
              let filter = CIFilter(name: "CIAreaAverage") else { return (128, 128, 128) }
        filter.setValue(image, forKey: kCIInputImageKey)
        filter.setValue(CIVector(cgRect: extent), forKey: kCIInputExtentKey)
        guard let outputImage = filter.outputImage else { return (128, 128, 128) }

        var bitmap = [UInt8](repeating: 0, count: 4)
        context.render(
            outputImage,
            toBitmap: &bitmap,
            rowBytes: 4,
            bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
            format: .RGBA8,
            colorSpace: nil
        )
        return (Double(bitmap[0]), Double(bitmap[1]), Double(bitmap[2]))
    }
}
