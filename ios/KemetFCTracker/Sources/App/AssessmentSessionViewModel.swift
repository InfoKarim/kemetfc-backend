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
import os.log

@MainActor
public final class AssessmentSessionViewModel: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "AssessmentSessionViewModel")

    @Published public private(set) var confirmedPlayer: KemetPlayerSummary?
    @Published public private(set) var backendSessionId: String?
    @Published public private(set) var isAssessmentActive = false
    @Published public private(set) var lastError: String?

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

    public init(apiClient: KemetAPIClient) {
        self.apiClient = apiClient
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

        do {
            struct CreateSessionBody: Encodable {
                let player_id: String
                let tracking_mode: String
                let gimbal_model: String?
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
                    gimbal_model: dockKitManager.connectedAccessory != nil ? "Insta360 Flow 2 Pro" : nil
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

        let coordinator = TrackingCoordinator(gimbalController: controller, telemetryUploader: uploader)
        coordinator.setMode(mode)
        trackingCoordinator = coordinator
        cameraCaptureManager.frameDelegate = FrameForwarder(coordinator: coordinator, captureManagerRef: cameraCaptureManager)

        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(sessionId).mov")
        recordingURL = url
        cameraCaptureManager.startRecording(to: url)

        isAssessmentActive = true
    }

    /// Coach taps a detected person to lock the target (spec section 6).
    public func handlePlayerTap(_ point: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        trackingCoordinator?.lockPlayer(at: point, detections: detections, pixelBuffer: pixelBuffer)
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

    /// Multipart upload to the EXISTING /videos/upload endpoint — full
    /// multipart construction omitted here (standard URLSession
    /// multipart/form-data boilerplate, not tracking-specific logic);
    /// see app/routers/videos.py:70 for the exact multipart shape
    /// (`metadata` JSON part + `video` file part) this must produce.
    private func uploadVideo(fileURL: URL, playerId: String) async throws -> String {
        throw KemetAPIError.transport(NSError(
            domain: "KemetFCTracker", code: -1,
            userInfo: [NSLocalizedDescriptionKey: "uploadVideo(fileURL:playerId:) multipart body not yet implemented — see method doc comment"]
        ))
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
