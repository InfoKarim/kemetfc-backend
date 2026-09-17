//
//  AssessmentSessionViewModel.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  The full workflow (spec section 24), owning every piece and wiring
//  them together in the right order:
//
//    Select/Confirm Player -> Connect Gimbal -> Select Smart Soccer ->
//    Tap Player -> PLAYER LOCKED -> Start Assessment -> [tracking +
//    recording run concurrently] -> Stop Assessment -> Upload video ->
//    Complete tracking session -> Publish assessment -> Player Profile
//

import AVFoundation
import Combine
import Foundation
import UIKit
import os.log

@MainActor
public final class AssessmentSessionViewModel: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "AssessmentSessionViewModel")

    @Published public private(set) var confirmedPlayer: KemetPlayerSummary?
    @Published public private(set) var backendSessionId: String?
    @Published public private(set) var isAssessmentActive = false
    @Published public private(set) var lastError: String?
    /// Bindable by AssessmentTrackingView (or a future upload-progress
    /// sheet) to show PREPARING/UPLOADING_VIDEO(progress)/PROCESSING/
    /// COMPLETE/FAILED without polling — see VideoUploadManager.swift.
    @Published public private(set) var videoUploadState: VideoUploadManager.UploadState = .notStarted

    /// Stopgap coach identity for `created_by` on the video-upload
    /// metadata (PlayerVideoUploadMetadataSchema.created_by is
    /// required). There is no native login view yet (see
    /// VERIFICATION_CHECKLIST.md and this session's report — building
    /// one is a separate, explicitly tracked gap), so this is set by
    /// whatever authenticated a WKWebView-hosted /login instead; NEVER
    /// silently defaulted to a fabricated-looking real name.
    public var coachIdentifier: String = "ios-app-unidentified-coach"

    public let dockKitManager = DockKitManager()
    public let cameraCaptureManager = CameraCaptureManager()
    private let apiClient: KemetAPIClient
    private var gimbalController: GimbalController?
    /// Published (not private) — AssessmentTrackingView binds to THIS
    /// exact instance, not one it constructs itself, so the overlay/HUD
    /// reflects the same coordinator actually receiving camera frames.
    @Published public private(set) var trackingCoordinator: TrackingCoordinator?
    private var telemetryUploader: TelemetryUploader?
    private var recordingURL: URL?
    private let videoUploadManager = VideoUploadManager()
    private var cancellables = Set<AnyCancellable>()

    public init(apiClient: KemetAPIClient) {
        self.apiClient = apiClient
        videoUploadManager.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in self?.videoUploadState = state }
            .store(in: &cancellables)
    }

    public func start() async {
        dockKitManager.startMonitoring()
        do {
            try await cameraCaptureManager.requestPermissionAndConfigure()
        } catch {
            lastError = "Camera permission denied — grant camera access in Settings to record an assessment."
        }
    }

    /// Step 1: coach confirms the player (spec section 5). No tracking
    /// starts until this has happened.
    public func confirmPlayer(_ player: KemetPlayerSummary) {
        confirmedPlayer = player
    }

    /// Step 2 (implicitly already true if dockKitManager.isConnected):
    /// the gimbal is physically docked. Step 3: create the backend
    /// tracking session and wire the tracking pipeline.
    public func startAssessment(mode: TrackingMode, speedProfile: TrackingSpeedProfile) async {
        guard let player = confirmedPlayer else {
            lastError = "Select and confirm a player before starting an assessment."
            return
        }

        if let preflightFailure = Self.preflightCheck() {
            lastError = preflightFailure
            return
        }

        // Constructed BEFORE the session POST (not after, like
        // gimbalController/coordinator below) specifically so the exact
        // detector instance whose version we report is the same one that
        // will actually run — never a version string hand-copied
        // separately from the real object (spec section 14: model-version
        // traceability must be honest, not guessed).
        let ballDetector: BallDetecting = ClassicalCVBallDetector()

        do {
            struct CreateSessionBody: Encodable {
                let player_id: String
                let tracking_mode: String
                let gimbal_model: String?
                let player_detector_version: String
                let ball_detector_version: String
                let ball_model_status: String
                let pose_model_version: String
                let tracker_algorithm_version: String
                let framing_algorithm_version: String
                let ios_app_version: String?
            }
            struct SessionResponse: Decodable {
                let session_id: String
            }
            apiClient.refreshCSRFToken()
            let response = try await apiClient.post(
                path: "/tracking/sessions",
                body: CreateSessionBody(
                    player_id: player.playerId,
                    tracking_mode: mode.rawValue,
                    gimbal_model: dockKitManager.connectedAccessory != nil ? "Insta360 Flow 2 Pro" : nil,
                    player_detector_version: PlayerDetector.detectorVersion,
                    ball_detector_version: ballDetector.modelIdentifier.version,
                    ball_model_status: ballDetector.ballModelStatus,
                    pose_model_version: PlayerDetector.poseModelVersion,
                    tracker_algorithm_version: PlayerTracker.algorithmVersion,
                    framing_algorithm_version: FramingCalculator.algorithmVersion,
                    ios_app_version: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
                ),
                as: SessionResponse.self
            )
            backendSessionId = response.session_id
        } catch {
            lastError = "Could not start tracking session: \(error)"
            return
        }

        guard let sessionId = backendSessionId else { return }

        let uploader = TelemetryUploader(apiClient: apiClient, sessionId: sessionId)
        uploader.start()
        telemetryUploader = uploader

        let controller = GimbalController()
        controller.speedProfile = speedProfile
        if let accessory = dockKitManager.connectedAccessory {
            controller.attach(accessory: accessory)
        }
        gimbalController = controller

        let coordinator = TrackingCoordinator(gimbalController: controller, telemetryUploader: uploader, ballDetector: ballDetector)
        coordinator.setMode(mode)
        trackingCoordinator = coordinator
        cameraCaptureManager.frameDelegate = FrameForwarder(coordinator: coordinator, captureManagerRef: cameraCaptureManager)

        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(sessionId).mov")
        recordingURL = url
        cameraCaptureManager.startRecording(to: url)

        isAssessmentActive = true
    }

    /// Battery/storage pre-checks before starting an assessment (spec
    /// section: "add battery/storage pre-checks") — real measured
    /// values (UIDevice.batteryLevel, FileManager volume capacity), not
    /// guesses, and deliberately conservative/approximate thresholds
    /// rather than a fabricated exact recording-duration prediction
    /// (spec: "Do not fabricate exact recording duration predictions
    /// unless based on measured bitrate/file size" — this app has not
    /// measured its own actual bitrate, so it checks a safety margin
    /// instead of promising "N minutes remaining"). Returns nil when OK,
    /// or a coach-facing message describing what to fix.
    private static func preflightCheck() -> String? {
        UIDevice.current.isBatteryMonitoringEnabled = true
        let batteryState = UIDevice.current.batteryState
        let batteryLevel = UIDevice.current.batteryLevel
        // batteryLevel is -1 (unknown) in the Simulator or briefly at
        // startup — never treated as "low," only a genuinely measured
        // low value is.
        if batteryState != .charging, batteryState != .full, batteryLevel >= 0, batteryLevel < 0.15 {
            return "Battery is below 15% and not charging — plug in before starting a full assessment."
        }

        // Conservative floor, not a duration promise: 1080p H.264 at a
        // typical ~15 Mbps runs well under 150MB/minute in practice, so
        // 1GB free comfortably covers a multi-minute assessment without
        // this app ever claiming to know the coach's exact device
        // codec/bitrate in advance.
        let minimumFreeBytes: Int64 = 1_000_000_000
        if let values = try? FileManager.default.temporaryDirectory
            .resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
            let available = values.volumeAvailableCapacityForImportantUsage,
            available < minimumFreeBytes {
            let availableMB = available / 1_000_000
            return "Only \(availableMB)MB of storage available — free up space before starting an assessment."
        }

        return nil
    }

    /// Coach taps a detected person to lock the target (spec section 6).
    public func handlePlayerTap(_ point: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        trackingCoordinator?.lockPlayer(at: point, detections: detections, pixelBuffer: pixelBuffer)
    }

    /// Coach review workflow (spec section 16): "Correct Player Tracked:
    /// YES/NO/UNSURE" — posts to the EXISTING, tested backend endpoint
    /// (POST /tracking/sessions/{id}/confirm-player, app/routers/
    /// tracking.py). A failure here is deliberately non-fatal to the
    /// caller (the assessment itself already completed) — surfaced via
    /// lastError for visibility, not thrown.
    public func confirmPlayerTracked(_ answer: String, notes: String? = nil) async {
        guard let sessionId = backendSessionId else { return }
        struct ConfirmBody: Encodable { let answer: String; let notes: String? }
        struct LabelResponse: Decodable { let label_id: String }
        do {
            apiClient.refreshCSRFToken()
            _ = try await apiClient.post(
                path: "/tracking/sessions/\(sessionId)/confirm-player",
                body: ConfirmBody(answer: answer, notes: notes),
                as: LabelResponse.self
            )
        } catch {
            log.error("confirmPlayerTracked failed: \(error.localizedDescription)")
            lastError = "Could not record player-tracking confirmation — the assessment itself was saved regardless."
        }
    }

    /// Stop Assessment -> Upload video -> Complete tracking session
    /// (spec section 24). The recorded video is uploaded through the
    /// EXISTING /videos/upload endpoint (app/routers/videos.py) — this
    /// app never reimplements video storage.
    public func stopAssessment() async {
        cameraCaptureManager.stopRecording()
        telemetryUploader?.stop()
        isAssessmentActive = false

        guard let sessionId = backendSessionId, let player = confirmedPlayer, let url = recordingURL else { return }

        do {
            let videoId = try await uploadVideo(fileURL: url, playerId: player.playerId)

            struct CompleteBody: Encodable { let video_id: String? }
            struct SessionResponse: Decodable { let status: String }
            _ = try await apiClient.post(
                path: "/tracking/sessions/\(sessionId)/complete",
                body: CompleteBody(video_id: videoId),
                as: SessionResponse.self
            )
        } catch {
            // The video file on disk and the tracking samples already
            // uploaded are BOTH still intact even if this final linking
            // step fails — nothing here can corrupt or lose the
            // assessment (spec section 28).
            log.error("stopAssessment finalize failed: \(error.localizedDescription)")
            lastError = "Upload finalization failed — the recording is saved locally and can be retried."
        }
    }

    /// Real multipart upload to the EXISTING /videos/upload endpoint —
    /// see VideoUploadManager.swift for the streaming multipart
    /// implementation, retry/backoff, and state machine.
    private func uploadVideo(fileURL: URL, playerId: String) async throws -> String {
        let duration = try await AVURLAsset(url: fileURL).load(.duration).seconds
        let metadata = PlayerVideoUploadMetadata(
            playerId: playerId,
            videoType: "assessment_smart_tracking",
            durationSeconds: duration.isFinite ? duration : 0,
            sessionId: backendSessionId ?? "",
            locationId: "kemetfc_ios_field_capture",
            captureDevice: "iPhone (KemetFCTracker)",
            resolution: cameraCaptureManager.activeFormatDescription,
            frameRateFps: cameraCaptureManager.effectiveFrameRate,
            schemaVersion: "1.0",
            createdBy: coachIdentifier
        )
        return try await videoUploadManager.uploadVideo(fileURL: fileURL, metadata: metadata, apiClient: apiClient)
    }
}

/// Bridges CameraCaptureManager's AVCaptureVideoDataOutputSampleBufferDelegate
/// callback to TrackingCoordinator.processFrame, computing session-elapsed
/// time from the sample buffer's own presentation timestamp rather than
/// wall-clock time (so a brief AVFoundation hiccup never desyncs
/// telemetry from the actual recorded video timeline).
private final class FrameForwarder: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let coordinator: TrackingCoordinator
    private weak var captureManagerRef: CameraCaptureManager?
    private var sessionStartTime: CMTime?

    init(coordinator: TrackingCoordinator, captureManagerRef: CameraCaptureManager) {
        self.coordinator = coordinator
        self.captureManagerRef = captureManagerRef
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        if sessionStartTime == nil { sessionStartTime = presentationTime }
        let elapsed = CMTimeGetSeconds(CMTimeSubtract(presentationTime, sessionStartTime ?? presentationTime))

        guard let device = captureManagerRef?.device else { return }
        Task { @MainActor in
            coordinator.processFrame(sampleBuffer: sampleBuffer, captureDevice: device, sessionElapsedSeconds: elapsed)
        }
    }
}
