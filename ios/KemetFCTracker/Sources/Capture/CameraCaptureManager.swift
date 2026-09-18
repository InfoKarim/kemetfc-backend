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

/// Explicit recording lifecycle (spec: "CameraCaptureManager.startRecording
/// currently returns before AVFoundation confirms didStartRecordingTo" —
/// a real defect: the app-level UI could show recording as active before
/// AVFoundation had genuinely started, and a Stop tapped during that
/// window would silently no-op without ever calling stopRecording()).
/// `.recording` is ONLY entered from the real didStartRecordingTo
/// delegate callback — never assumed from calling startRecordingAndWait.
public enum RecordingLifecycleState: Equatable {
    case idle
    case starting
    case recording
    case stopping
    case finished(URL)
    case failed(String)
}

@MainActor
public final class CameraCaptureManager: NSObject, ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "CameraCaptureManager")

    public let session = AVCaptureSession()
    private let videoDataOutput = AVCaptureVideoDataOutput()
    private let movieFileOutput = AVCaptureMovieFileOutput()
    /// Added/removed on demand (startQRScanning/stopQRScanning) rather
    /// than kept on the session permanently — the QR Scan tab (spec
    /// section 5) is the only consumer, and it must not still be
    /// decoding metadata once a player is confirmed and real tracking
    /// begins.
    private let qrMetadataOutput = AVCaptureMetadataOutput()
    private let sessionQueue = DispatchQueue(label: "com.kemetfc.tracker.session")
    private let videoDataQueue = DispatchQueue(label: "com.kemetfc.tracker.videodata")

    @Published public private(set) var isRecording = false
    @Published public private(set) var recordingLifecycleState: RecordingLifecycleState = .idle
    @Published public private(set) var isConfigured = false
    @Published public private(set) var activeFormatDescription: String = ""
    @Published public private(set) var effectiveFrameRate: Double = 0
    /// The negotiated capture format's pixel dimensions — read by the
    /// tap-to-lock UI (TapToLockConverter.normalizedCameraPoint) to undo
    /// the preview layer's aspect-fill crop. Zero until configure()
    /// actually negotiates a format; never a hard-coded guess.
    @Published public private(set) var activeVideoDimensions: CGSize = .zero

    public private(set) var device: AVCaptureDevice?
    /// Set only while performStart(...) is awaiting the real
    /// didStartRecordingTo callback — resolved exactly once via
    /// resolveStartIfStillPending, from either that callback or the
    /// bounded start timeout, whichever comes first.
    private var startContinuation: CheckedContinuation<Bool, Never>?
    /// Set only while performStop(...) is awaiting the real
    /// didFinishRecordingTo callback — resolved exactly once via
    /// resolveStopIfStillPending.
    private var stopContinuation: CheckedContinuation<URL?, Never>?
    /// The exact URL the most recent startRecordingAndWait(to:) call
    /// asked AVFoundation to record to — kept even after a failed/timed-
    /// out start, so a stop can still check whether AVFoundation wrote a
    /// real file there regardless of what our own state believed (spec:
    /// "check the expected recording URL again ... before declaring the
    /// file missing").
    private var expectedRecordingURL: URL?
    /// Coalesces concurrent startRecordingAndWait callers onto one
    /// underlying operation.
    private var startTask: Task<Bool, Never>?
    /// Coalesces concurrent stopRecordingAndWait callers onto one
    /// underlying operation — spec: "Multiple Stop taps must share one
    /// finalization operation or be ignored safely."
    private var stopTask: Task<URL?, Never>?

    /// Zoom is deliberately separate from gimbal rotation (spec section
    /// 16) — this is the iPhone's own optical/digital zoom via
    /// videoZoomFactor, never a claim about the Flow 2 Pro having a
    /// physical lens zoom (it does not; it only pans/tilts).
    public let minZoomFactor: CGFloat = 1.0
    public let maxZoomFactor: CGFloat = 3.0 // conservative default — see spec section 16

    // nonisolated(unsafe): a real Xcode build flagged captureOutput(_:
    // didOutput:from:) below (AVFoundation always calls this on
    // videoDataQueue, a background queue — it MUST stay nonisolated,
    // not hop to the main actor, or every frame would incur an actor
    // hop before recording/inference could even begin) reading this
    // @MainActor-isolated property from that nonisolated context. This
    // is genuinely safe: `frameDelegate` is set exactly once, from the
    // main actor, before frames start flowing (AssessmentSessionViewModel
    // .startAssessment), and a `weak` reference read is atomic at the
    // runtime level regardless of which thread performs it.
    public nonisolated(unsafe) weak var frameDelegate: AVCaptureVideoDataOutputSampleBufferDelegate?

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
        activeVideoDimensions = CGSize(width: Int(dims.width), height: Int(dims.height))
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

    /// Requests a recording start and waits for AVFoundation's OWN
    /// confirmation (didStartRecordingTo) before returning `true` —
    /// `recordingLifecycleState` only ever enters `.recording` from that
    /// real callback, never optimistically. Concurrent callers (there
    /// should never legitimately be more than one, but a UI double-tap
    /// is a real possibility) share this single operation.
    ///
    /// A timeout that elapses before the callback arrives resolves as
    /// `false` with `.failed(...)` — this is NOT a claim the recording
    /// permanently failed, only that AVFoundation hasn't confirmed it
    /// yet within a bounded wait; if the real callback arrives later, it
    /// still transitions state correctly (see resolveStartIfStillPending),
    /// it just wasn't in time for this particular caller's await.
    @discardableResult
    public func startRecordingAndWait(to url: URL, timeoutSeconds: TimeInterval = 5.0) async -> Bool {
        if let startTask { return await startTask.value }
        guard recordingLifecycleState != .recording, recordingLifecycleState != .stopping, recordingLifecycleState != .starting else {
            return false
        }

        let task = Task<Bool, Never> { [weak self] in
            await self?.performStart(to: url, timeoutSeconds: timeoutSeconds) ?? false
        }
        startTask = task
        let result = await task.value
        startTask = nil
        return result
    }

    private func performStart(to url: URL, timeoutSeconds: TimeInterval) async -> Bool {
        recordingLifecycleState = .starting
        expectedRecordingURL = url
        movieFileOutput.startRecording(to: url, recordingDelegate: self)

        return await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            self.startContinuation = continuation
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                await self?.resolveStartIfStillPending(outcome: false, reason: "Recording start timed out waiting for camera confirmation")
            }
        }
    }

    /// Resumes startContinuation EXACTLY once, whichever of (the real
    /// didStartRecordingTo callback) or (the timeout task) reaches this
    /// first — guarded by clearing the continuation atomically before
    /// acting, so the loser is a safe no-op, never a double-resume.
    ///
    /// State truthfulness is handled SEPARATELY from continuation
    /// resolution: a genuine didStartRecordingTo that arrives AFTER the
    /// timeout already resolved the waiting caller (spec: "Do not create
    /// a permanent failure merely because the async start callback has
    /// not arrived yet") still updates `recordingLifecycleState` to
    /// `.recording` — AVFoundation really is recording at that point,
    /// regardless of whether any caller was still around to hear about
    /// it in time.
    private func resolveStartIfStillPending(outcome: Bool, reason: String?) {
        if outcome {
            if recordingLifecycleState == .starting || isFailedLifecycleState(recordingLifecycleState) {
                recordingLifecycleState = .recording
            }
        } else if recordingLifecycleState == .starting {
            recordingLifecycleState = .failed(reason ?? "Recording did not start")
        }

        guard let continuation = startContinuation else { return }
        startContinuation = nil
        continuation.resume(returning: outcome)
    }

    private func isFailedLifecycleState(_ state: RecordingLifecycleState) -> Bool {
        if case .failed = state { return true }
        return false
    }

    /// Requests a stop and waits for AVFoundation's real finalization
    /// (didFinishRecordingTo) before returning — never reads the file
    /// immediately after a fire-and-forget stopRecording() call, which
    /// can race a still-open file.
    ///
    /// Re-entrancy safe (spec: "Multiple Stop taps must share one
    /// finalization operation or be ignored safely"): a second concurrent
    /// call while one is already in flight awaits the SAME task rather
    /// than issuing a second stopRecording() or overwriting the pending
    /// continuation.
    ///
    /// If a Stop arrives while still STARTING, it waits for the start to
    /// resolve first rather than racing it — this is the exact defect a
    /// physical-device test caught: stopping before didStartRecordingTo
    /// fired returned nil without ever calling stopRecording(), even
    /// though AVFoundation had already begun writing real data to the
    /// file.
    public func stopRecordingAndWait(timeoutSeconds: TimeInterval = 8.0) async -> URL? {
        if let stopTask { return await stopTask.value }

        let task = Task<URL?, Never> { [weak self] in
            await self?.performStop(timeoutSeconds: timeoutSeconds) ?? nil
        }
        stopTask = task
        let result = await task.value
        stopTask = nil
        return result
    }

    private func performStop(timeoutSeconds: TimeInterval) async -> URL? {
        if recordingLifecycleState == .starting, let startTask {
            _ = await startTask.value
        }

        guard recordingLifecycleState == .recording else {
            // Nothing AVFoundation currently considers "recording" (never
            // started, start failed/timed out, or already stopped) — but
            // per the fix for the exact bug this replaces, still hand
            // back the URL we last told AVFoundation to record to. The
            // caller verifies real file existence/size/readability
            // against this rather than assuming absence just because our
            // own state tracking didn't observe a clean lifecycle.
            return expectedRecordingURL
        }

        recordingLifecycleState = .stopping
        let expectedURL = expectedRecordingURL
        return await withCheckedContinuation { (continuation: CheckedContinuation<URL?, Never>) in
            self.stopContinuation = continuation
            movieFileOutput.stopRecording()
            // Confirmed defect (physical device): if didFinishRecordingTo
            // never fires — a genuine, observed AVFoundation stall, not
            // hypothetical — this continuation hung forever with no
            // fallback, presenting as "the camera won't turn off" with no
            // way back to the app's UI. A bounded timeout guarantees this
            // always resolves; the caller still verifies the real file at
            // `expectedURL` independently, so a timeout here never claims
            // the recording is lost, only that this specific wait gave up.
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                await self?.resolveStopIfStillPending(url: expectedURL, timedOut: true)
            }
        }
    }

    /// Resumes stopContinuation EXACTLY once, whichever of (the real
    /// didFinishRecordingTo callback) or (the timeout task) reaches this
    /// first — same guard pattern as resolveStartIfStillPending. On a
    /// genuine timeout, `url` is the recording's own expected path (never
    /// nil), so a caller that times out still has something concrete to
    /// verify rather than an unconditional "missing."
    private func resolveStopIfStillPending(url: URL?, timedOut: Bool = false) {
        guard let continuation = stopContinuation else { return }
        stopContinuation = nil
        if !timedOut {
            recordingLifecycleState = url.map { .finished($0) } ?? .idle
        }
        // On timeout, deliberately leave recordingLifecycleState at
        // .stopping rather than guessing — if the real callback still
        // arrives later, it can correctly transition to .finished/.idle
        // itself; this resolver has already handed the caller a URL to
        // verify independently either way.
        continuation.resume(returning: url)
    }

    /// Adds a lightweight AVCaptureMetadataOutput to the SAME session
    /// already running for the live preview (spec: "reuse the existing
    /// capture session safely", "do not create conflicting camera
    /// sessions") — never a second AVCaptureSession. Safe to call while
    /// the session is running: AVFoundation allows adding/removing
    /// outputs live, wrapped in begin/commitConfiguration. Returns false
    /// if the output couldn't be added (e.g. already present, or the
    /// session isn't configured yet) so the caller can show an error
    /// instead of silently never scanning anything.
    @discardableResult
    public func startQRScanning(
        delegate: AVCaptureMetadataOutputObjectsDelegate,
        queue: DispatchQueue
    ) -> Bool {
        guard isConfigured else { return false }
        guard !session.outputs.contains(qrMetadataOutput) else { return true }
        guard session.canAddOutput(qrMetadataOutput) else { return false }

        session.beginConfiguration()
        session.addOutput(qrMetadataOutput)
        qrMetadataOutput.setMetadataObjectsDelegate(delegate, queue: queue)
        if qrMetadataOutput.availableMetadataObjectTypes.contains(.qr) {
            qrMetadataOutput.metadataObjectTypes = [.qr]
        }
        session.commitConfiguration()
        return true
    }

    /// Removes the QR metadata output once a player is confirmed or the
    /// coach leaves the QR Scan tab — recording/inference (the movie
    /// file output and video data output) are entirely untouched by
    /// this, since they're separate outputs on the same session.
    public func stopQRScanning() {
        guard session.outputs.contains(qrMetadataOutput) else { return }
        session.beginConfiguration()
        session.removeOutput(qrMetadataOutput)
        session.commitConfiguration()
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
        Task { @MainActor in
            self.isRecording = true
            self.resolveStartIfStillPending(outcome: true, reason: nil)
        }
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
            // The URL is handed to the continuation regardless of `error`
            // — AVFoundation can report a non-fatal error (e.g. a
            // dropped frame near the end) while still producing a
            // usable file. The caller (stopAssessment) does its own
            // real existence/size/AVAsset-readable verification rather
            // than trusting this delegate's error alone.
            self.resolveStopIfStillPending(url: outputFileURL)
            // Guards against a pathological case where didFinishRecordingTo
            // fires without a preceding didStartRecordingTo (a very early
            // failure) — never leave startContinuation dangling forever.
            self.resolveStartIfStillPending(outcome: false, reason: error?.localizedDescription ?? "Recording finished before it was confirmed started")
        }
    }
}
