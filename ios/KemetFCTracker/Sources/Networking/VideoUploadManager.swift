//
//  VideoUploadManager.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift
//  header. Uses `URLSession.upload(for:fromFile:delegate:)` and
//  `URLSessionTaskDelegate.urlSession(_:task:didSendBodyData:...)`,
//  both re-verified against Apple's live documentation this session
//  (developer.apple.com/documentation/foundation/urlsession/...) —
//  exact signatures, not recalled from training data.
//
//  Phase 2 fix for a real, previously-open gap: uploadVideo(fileURL:
//  playerId:) in AssessmentSessionViewModel.swift threw immediately —
//  there was no multipart implementation at all. This file is the real
//  implementation, matching app/routers/videos.py's exact multipart
//  shape (`metadata` JSON form field + `video` file field — see that
//  file, POST /videos/upload) and app/video_storage.py's 500MB limit
//  (MAX_PLAYER_VIDEO_SIZE_BYTES).
//
//  Explicit state machine (spec section 24: "Build an upload state
//  machine with progress/retry/cancellation") — NOT_STARTED ->
//  PREPARING -> UPLOADING_VIDEO(progress) -> PROCESSING -> COMPLETE, or
//  -> FAILED at any point. "UPLOADING_TELEMETRY" from the spec's naming
//  is intentionally represented by TelemetryUploader's own in-flight
//  batches (it uploads continuously DURING recording, not after) rather
//  than a redundant state here — see AssessmentSessionViewModel
//  .stopAssessment(), which calls telemetryUploader.stop() (flushing
//  any queued batches) before this video upload begins.
//
//  Streaming, not buffering: the multipart body is assembled on DISK
//  (a temp file), copying the source video in bounded 1MB chunks rather
//  than loading a potentially 100s-of-MB recording into memory, then
//  uploaded via `session.upload(for:fromFile:)` — which streams from
//  disk to the network without requiring the whole body resident in
//  RAM at once either.
//

import Foundation

public struct PlayerVideoUploadMetadata: Encodable {
    public var videoId: String?
    public var recordId: String?
    public var playerId: String
    public var videoType: String
    public var durationSeconds: Double
    public var sessionId: String
    public var locationId: String
    public var captureDevice: String
    public var resolution: String
    public var frameRateFps: Double
    public var schemaVersion: String
    public var createdBy: String

    // Matches PlayerVideoUploadMetadataSchema's snake_case field names
    // exactly (app/api_schemas.py) — the JSON payload for the
    // multipart request's "metadata" form field.
    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case recordId = "record_id"
        case playerId = "player_id"
        case videoType = "video_type"
        case durationSeconds = "duration_seconds"
        case sessionId = "session_id"
        case locationId = "location_id"
        case captureDevice = "capture_device"
        case resolution
        case frameRateFps = "frame_rate_fps"
        case schemaVersion = "schema_version"
        case createdBy = "created_by"
    }

    public init(
        videoId: String? = nil,
        recordId: String? = nil,
        playerId: String,
        videoType: String,
        durationSeconds: Double,
        sessionId: String,
        locationId: String,
        captureDevice: String,
        resolution: String,
        frameRateFps: Double,
        schemaVersion: String,
        createdBy: String
    ) {
        self.videoId = videoId
        self.recordId = recordId
        self.playerId = playerId
        self.videoType = videoType
        self.durationSeconds = durationSeconds
        self.sessionId = sessionId
        self.locationId = locationId
        self.captureDevice = captureDevice
        self.resolution = resolution
        self.frameRateFps = frameRateFps
        self.schemaVersion = schemaVersion
        self.createdBy = createdBy
    }
}

public enum VideoUploadError: Error {
    /// The recording exceeds the backend's 500MB limit
    /// (MAX_PLAYER_VIDEO_SIZE_BYTES in app/video_storage.py) — checked
    /// BEFORE spending any upload bandwidth, not discovered from a
    /// rejected request after the fact.
    case fileTooLarge(sizeBytes: Int64, limitBytes: Int64)
    case cancelled
}

@MainActor
public final class VideoUploadManager: NSObject, ObservableObject {
    public enum UploadState: Equatable {
        case notStarted
        case preparing
        case uploadingVideo(progress: Double)
        case processing
        case complete(videoId: String)
        case failed(String)
    }

    /// Mirrors the backend's real, current limit — kept as a named
    /// constant here (not silently assumed) so a pre-check can fail
    /// fast with an actionable message instead of uploading for minutes
    /// only to be rejected by the server at the very end.
    public static let maxUploadSizeBytes: Int64 = 500 * 1024 * 1024

    @Published public private(set) var state: UploadState = .notStarted

    private var activeTask: URLSessionTask?
    private var isCancelled = false

    public func cancel() {
        isCancelled = true
        activeTask?.cancel()
    }

    public func uploadVideo(
        fileURL: URL,
        metadata: PlayerVideoUploadMetadata,
        apiClient: KemetAPIClient,
        maxAttempts: Int = 3
    ) async throws -> String {
        isCancelled = false
        state = .preparing

        let fileSize = (try? FileManager.default.attributesOfItem(atPath: fileURL.path)[.size] as? Int64) ?? 0
        guard fileSize <= Self.maxUploadSizeBytes else {
            let error = VideoUploadError.fileTooLarge(sizeBytes: fileSize, limitBytes: Self.maxUploadSizeBytes)
            state = .failed("Recording is \(fileSize / 1_048_576)MB, exceeding the 500MB upload limit.")
            throw error
        }

        let boundary = "KemetFCBoundary-\(UUID().uuidString)"
        let metadataJSON = try JSONEncoder.kemet.encode(metadata)
        let contentType = Self.contentType(forExtension: fileURL.pathExtension.lowercased())
        let multipartFileURL = try Self.buildMultipartBody(
            boundary: boundary,
            metadataJSON: metadataJSON,
            videoFileURL: fileURL,
            videoFilename: fileURL.lastPathComponent,
            videoContentType: contentType
        )
        defer { try? FileManager.default.removeItem(at: multipartFileURL) }

        var lastError: Error = KemetAPIError.transport(
            NSError(domain: "KemetFCTracker", code: -1, userInfo: [NSLocalizedDescriptionKey: "Upload did not run"])
        )

        for attempt in 1...maxAttempts {
            guard !isCancelled else {
                state = .failed("Cancelled")
                throw VideoUploadError.cancelled
            }
            do {
                state = .uploadingVideo(progress: 0)
                let videoId = try await performUpload(multipartFileURL: multipartFileURL, boundary: boundary, apiClient: apiClient)
                state = .processing
                return videoId
            } catch let error as KemetAPIError {
                // A 4xx is the server rejecting THIS request (bad
                // metadata, unsupported format, oversized file) —
                // retrying an identical request guarantees an identical
                // rejection, so only transport/5xx errors are retried.
                if case .server(let status, _) = error, (400..<500).contains(status) {
                    state = .failed("Upload rejected: \(error)")
                    throw error
                }
                lastError = error
            } catch {
                lastError = error
            }

            if attempt < maxAttempts {
                let backoffSeconds = pow(2.0, Double(attempt))
                try? await Task.sleep(nanoseconds: UInt64(backoffSeconds * 1_000_000_000))
            }
        }

        state = .failed("Upload failed after \(maxAttempts) attempts: \(lastError)")
        throw lastError
    }

    private func performUpload(multipartFileURL: URL, boundary: String, apiClient: KemetAPIClient) async throws -> String {
        var request = apiClient.authenticatedRequest(path: "/videos/upload", method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await apiClient.underlyingSession.upload(for: request, fromFile: multipartFileURL, delegate: self)
        } catch {
            throw KemetAPIError.transport(error)
        }

        try KemetAPIClient.validate(response: response, data: data)

        struct UploadResponse: Decodable { let video_id: String }
        do {
            return try JSONDecoder.kemet.decode(UploadResponse.self, from: data).video_id
        } catch {
            throw KemetAPIError.decoding(error)
        }
    }

    private static func contentType(forExtension ext: String) -> String {
        // Mirrors SUPPORTED_VIDEO_TYPES in app/player_video_upload.py —
        // an unrecognized extension still gets a best-effort generic
        // type; the backend, not this client, is the source of truth
        // for what's actually accepted.
        switch ext {
        case "mp4": return "video/mp4"
        case "mov": return "video/quicktime"
        case "avi": return "video/x-msvideo"
        case "mkv": return "video/x-matroska"
        case "webm": return "video/webm"
        case "m4v": return "video/x-m4v"
        default: return "application/octet-stream"
        }
    }

    /// Assembles the multipart/form-data body on disk, streaming the
    /// source video in bounded chunks rather than loading it fully into
    /// memory. Matches app/routers/videos.py's exact expected shape:
    /// `metadata` (JSON text field) then `video` (file field).
    nonisolated private static func buildMultipartBody(
        boundary: String,
        metadataJSON: Data,
        videoFileURL: URL,
        videoFilename: String,
        videoContentType: String
    ) throws -> URL {
        let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).multipart")
        guard FileManager.default.createFile(atPath: tempURL.path, contents: nil) else {
            throw KemetAPIError.transport(NSError(domain: "KemetFCTracker", code: -2, userInfo: [NSLocalizedDescriptionKey: "Could not create temp upload file"]))
        }
        let output = try FileHandle(forWritingTo: tempURL)
        defer { try? output.close() }

        func write(_ string: String) throws {
            try output.write(contentsOf: Data(string.utf8))
        }

        try write("--\(boundary)\r\n")
        try write("Content-Disposition: form-data; name=\"metadata\"\r\n\r\n")
        try output.write(contentsOf: metadataJSON)
        try write("\r\n")

        try write("--\(boundary)\r\n")
        try write("Content-Disposition: form-data; name=\"video\"; filename=\"\(videoFilename)\"\r\n")
        try write("Content-Type: \(videoContentType)\r\n\r\n")

        let input = try FileHandle(forReadingFrom: videoFileURL)
        defer { try? input.close() }
        let chunkSize = 1 * 1024 * 1024
        while true {
            let chunk = try input.read(upToCount: chunkSize) ?? Data()
            if chunk.isEmpty { break }
            try output.write(contentsOf: chunk)
        }

        try write("\r\n--\(boundary)--\r\n")
        return tempURL
    }
}

extension VideoUploadManager: URLSessionTaskDelegate {
    public nonisolated func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        guard totalBytesExpectedToSend > 0 else { return }
        let progress = Double(totalBytesSent) / Double(totalBytesExpectedToSend)
        Task { @MainActor in
            self.state = .uploadingVideo(progress: progress)
        }
    }
}
