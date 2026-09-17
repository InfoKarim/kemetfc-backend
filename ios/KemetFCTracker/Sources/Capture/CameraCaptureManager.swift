//
//  CameraCaptureManager.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  AVFoundation capture: rear wide-angle camera, negotiated (never
//  hard-coded) resolution/frame-rate against what the actual device
//  supports, simultaneous video-data-output (for Vision inference) and
//  movie-file-output (for the untouched assessment recording) so
//  inference latency/failure NEVER affects the recorded video (spec
//  section 18: "Recording must remain separate from inference").
//

import AVFoundation
import Combine
import Foundation
import os.log

public enum CameraCaptureError: Error {
    case permissionDenied
    case noSuitableDevice
    case configurationFailed(String)
}

@MainActor
public final class CameraCaptureManager: NSObject, ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "CameraCaptureManager")

    public let session = AVCaptureSession()
    private let videoDataOutput = AVCaptureVideoDataOutput()
    private let movieFileOutput = AVCaptureMovieFileOutput()
    private let sessionQueue = DispatchQueue(label: "com.kemetfc.tracker.session")
    private let videoDataQueue = DispatchQueue(label: "com.kemetfc.tracker.videodata")

    @Published public private(set) var isRecording = false
    @Published public private(set) var isConfigured = false
    @Published public private(set) var activeFormatDescription: String = ""
    @Published public private(set) var effectiveFrameRate: Double = 0

    public private(set) var device: AVCaptureDevice?

    /// Zoom is deliberately separate from gimbal rotation (spec section
    /// 16) — this is the iPhone's own optical/digital zoom via
    /// videoZoomFactor, never a claim about the Flow 2 Pro having a
    /// physical lens zoom (it does not; it only pans/tilts).
    public let minZoomFactor: CGFloat = 1.0
    public let maxZoomFactor: CGFloat = 3.0 // conservative default — see spec section 16

    public weak var frameDelegate: AVCaptureVideoDataOutputSampleBufferDelegate?

    public override init() {
        super.init()
    }

    public func requestPermissionAndConfigure() async throws {
        let status = AVCaptureDevice.authorizationStatus(for: .video)
        switch status {
        case .authorized:
            break
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .video)
            guard granted else { throw CameraCaptureError.permissionDenied }
        default:
            throw CameraCaptureError.permissionDenied
        }
        try await configure()
    }

    private func configure() async throws {
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw CameraCaptureError.noSuitableDevice
        }
        self.device = device

        try selectBestFormat(for: device)

        session.beginConfiguration()
        session.sessionPreset = .inputPriority

        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            session.commitConfiguration()
            throw CameraCaptureError.configurationFailed("Cannot add camera input")
        }
        session.addInput(input)

        videoDataOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        videoDataOutput.alwaysDiscardsLateVideoFrames = true
        videoDataOutput.setSampleBufferDelegate(self, queue: videoDataQueue)
        guard session.canAddOutput(videoDataOutput) else {
            session.commitConfiguration()
            throw CameraCaptureError.configurationFailed("Cannot add video data output")
        }
        session.addOutput(videoDataOutput)

        guard session.canAddOutput(movieFileOutput) else {
            session.commitConfiguration()
            throw CameraCaptureError.configurationFailed("Cannot add movie file output")
        }
        session.addOutput(movieFileOutput)

        if let connection = videoDataOutput.connection(with: .video) {
            connection.preferredVideoStabilizationMode = .cinematic
        }

        session.commitConfiguration()

        sessionQueue.async { [session] in
            session.startRunning()
        }

        isConfigured = true
    }

    /// Never hard-codes a resolution/frame rate — negotiates the
    /// highest frame rate available at a resolution suitable for
    /// outdoor soccer (>=1920x1080), per spec section 4. Falls back to
    /// whatever the device actually offers rather than failing if a
    /// 60fps format isn't available on a given hardware revision.
    private func selectBestFormat(for device: AVCaptureDevice) throws {
        let candidates = device.formats.filter { format in
            let dimensions = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
            return dimensions.width >= 1920 && dimensions.height >= 1080
        }
        guard !candidates.isEmpty else {
            throw CameraCaptureError.configurationFailed("No 1080p+ format available on this device")
        }

        // Prefer the format supporting the highest frame rate; among
        // ties, prefer the smaller (less thermally/CPU expensive)
        // resolution — 4K60 is rarely necessary for pose/ball inference
        // and taxes the Neural Engine unnecessarily (spec section 26/27).
        let best = candidates.max { lhs, rhs in
            let lhsMaxFPS = lhs.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0
            let rhsMaxFPS = rhs.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0
            if lhsMaxFPS != rhsMaxFPS { return lhsMaxFPS < rhsMaxFPS }
            let lhsDims = CMVideoFormatDescriptionGetDimensions(lhs.formatDescription)
            let rhsDims = CMVideoFormatDescriptionGetDimensions(rhs.formatDescription)
            return (lhsDims.width * lhsDims.height) > (rhsDims.width * rhsDims.height)
        }
        guard let selected = best else {
            throw CameraCaptureError.configurationFailed("Could not select a capture format")
        }

        let targetFPS = min(60, selected.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 30)

        try device.lockForConfiguration()
        device.activeFormat = selected
        device.activeVideoMinFrameDuration = CMTime(value: 1, timescale: Int32(targetFPS))
        device.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: Int32(targetFPS))
        if device.isSmoothAutoFocusSupported {
            device.isSmoothAutoFocusEnabled = true
        }
        device.focusMode = .continuousAutoFocus
        device.exposureMode = .continuousAutoExposure
        if device.isLowLightBoostSupported {
            device.automaticallyEnablesLowLightBoostWhenAvailable = true
        }
        device.unlockForConfiguration()

        let dims = CMVideoFormatDescriptionGetDimensions(selected.formatDescription)
        activeFormatDescription = "\(dims.width)x\(dims.height)"
        effectiveFrameRate = targetFPS
        log.info("Selected capture format \(dims.width)x\(dims.height) @ \(targetFPS)fps")
    }

    public func setZoomFactor(_ factor: CGFloat) {
        guard let device else { return }
        let clamped = max(minZoomFactor, min(maxZoomFactor, factor))
        do {
            try device.lockForConfiguration()
            device.videoZoomFactor = clamped
            device.unlockForConfiguration()
        } catch {
            log.error("setZoomFactor failed: \(error.localizedDescription)")
        }
    }

    public func startRecording(to url: URL) {
        guard !isRecording else { return }
        movieFileOutput.startRecording(to: url, recordingDelegate: self)
    }

    public func stopRecording() {
        guard isRecording else { return }
        movieFileOutput.stopRecording()
    }
}

extension CameraCaptureManager: AVCaptureVideoDataOutputSampleBufferDelegate {
    public nonisolated func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        // Forwarded, never processed here — CameraCaptureManager's only
        // job is capture; TrackingCoordinator owns frame-skipping and
        // inference scheduling (spec section 26) so this delegate call
        // itself stays cheap and never blocks the capture pipeline.
        frameDelegate?.captureOutput?(output, didOutput: sampleBuffer, from: connection)
    }
}

extension CameraCaptureManager: AVCaptureFileOutputRecordingDelegate {
    public nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didStartRecordingTo fileURL: URL,
        from connections: [AVCaptureConnection]
    ) {
        Task { @MainActor in self.isRecording = true }
    }

    public nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        Task { @MainActor in
            self.isRecording = false
            if let error {
                self.log.error("Recording finished with error: \(error.localizedDescription)")
            }
        }
    }
}
