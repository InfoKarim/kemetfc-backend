//
//  MultipartUploadModels.swift
//  KemetFCTracker
//
//  Data contracts for the resumable direct-to-R2 multipart upload client
//  (2026-10-01, Phase 2 — iOS foreground multipart client). Mirrors the
//  backend's Phase 1 schemas exactly (app/api_schemas.py's
//  InitiateMultipartUploadSchema/CompleteMultipartUploadSchema/
//  AbortMultipartUploadSchema) — no identity logic is re-derived here;
//  this file only carries values the caller already has.
//

import Foundation

// MARK: - Part-size math (mirrors app/video_storage.py's calculate_part_plan exactly)

/// 32 MiB — same value, same reasoning, as the backend's
/// `MULTIPART_PART_SIZE_BYTES`. Kept as a SEPARATE constant (not shared
/// code with the backend, which is Python) — if these ever drift, the
/// backend's own plan recomputation on every call is authoritative; see
/// `MultipartUploadReconciler`'s own doc for why a client/server mismatch
/// here is self-healing rather than silently wrong.
public enum MultipartUploadPlan {
    public static let partSizeBytes: Int64 = 32 * 1024 * 1024

    /// Pure arithmetic — ceiling division, zero I/O. Throws rather than
    /// returning a nonsensical plan for a non-positive size.
    public static func calculate(fileSizeBytes: Int64, partSizeBytes: Int64 = MultipartUploadPlan.partSizeBytes) throws -> (partSizeBytes: Int64, totalParts: Int) {
        guard fileSizeBytes > 0 else {
            throw MultipartUploadError.invalidFileSize(fileSizeBytes)
        }
        let totalParts = Int((fileSizeBytes + partSizeBytes - 1) / partSizeBytes)
        return (partSizeBytes, totalParts)
    }

    /// The exact byte range [start, end) for a given 1-indexed part
    /// number, clamped so the FINAL part is exactly the remaining bytes
    /// — never reads past the end of the file, never pads.
    public static func byteRange(forPart partNumber: Int, fileSizeBytes: Int64, partSizeBytes: Int64 = MultipartUploadPlan.partSizeBytes) -> Range<Int64> {
        let start = Int64(partNumber - 1) * partSizeBytes
        let end = min(start + partSizeBytes, fileSizeBytes)
        return start..<end
    }
}

public enum MultipartUploadError: Error, Equatable, Sendable {
    case invalidFileSize(Int64)
    case partUploadFailed(partNumber: Int, statusCode: Int?)
    case missingETagInResponse(partNumber: Int)
    case fileReadFailed(partNumber: Int)
    case identityMismatch(expected: String, found: String)
    case finalizeIdentityMismatch
    case noActiveUploadState
}

// MARK: - Backend request/response DTOs (snake_case, matching app/api_schemas.py)

public struct MultipartInitiateRequest: Encodable, Sendable {
    public var videoId: String
    public var recordId: String
    public var playerId: String
    public var sessionId: String
    public var recordingId: String
    public var filename: String
    public var contentType: String
    public var fileSizeBytes: Int64
    public var videoType: String
    public var durationSeconds: Double
    public var locationId: String
    public var captureDevice: String
    public var resolution: String
    public var frameRateFps: Double
    public var schemaVersion: String
    public var createdBy: String

    enum CodingKeys: String, CodingKey {
        case videoId = "video_id", recordId = "record_id", playerId = "player_id", sessionId = "session_id"
        case recordingId = "recording_id", filename, contentType = "content_type", fileSizeBytes = "file_size_bytes"
        case videoType = "video_type", durationSeconds = "duration_seconds", locationId = "location_id"
        case captureDevice = "capture_device", resolution, frameRateFps = "frame_rate_fps"
        case schemaVersion = "schema_version", createdBy = "created_by"
    }

    public init(
        videoId: String, recordId: String, playerId: String, sessionId: String, recordingId: String,
        filename: String, contentType: String, fileSizeBytes: Int64, videoType: String, durationSeconds: Double,
        locationId: String, captureDevice: String, resolution: String, frameRateFps: Double,
        schemaVersion: String, createdBy: String
    ) {
        self.videoId = videoId; self.recordId = recordId; self.playerId = playerId; self.sessionId = sessionId
        self.recordingId = recordingId; self.filename = filename; self.contentType = contentType
        self.fileSizeBytes = fileSizeBytes; self.videoType = videoType; self.durationSeconds = durationSeconds
        self.locationId = locationId; self.captureDevice = captureDevice; self.resolution = resolution
        self.frameRateFps = frameRateFps; self.schemaVersion = schemaVersion; self.createdBy = createdBy
    }
}

/// Also the finalize request body — the backend's `/finalize` route
/// accepts the identical `InitiateMultipartUploadSchema` shape (the
/// client resends the same metadata rather than the server persisting it
/// in a new table — see the Phase 1 report's "no DB migration" design).
public typealias MultipartFinalizeRequest = MultipartInitiateRequest

public struct MultipartInitiatePartURL: Decodable, Sendable {
    public var partNumber: Int
    public var url: String
    enum CodingKeys: String, CodingKey { case partNumber = "part_number", url }
}

public struct MultipartInitiateResponse: Decodable, Sendable {
    public var uploadId: String
    public var objectKey: String
    public var partSizeBytes: Int64
    public var totalParts: Int
    public var parts: [MultipartInitiatePartURL]
    enum CodingKeys: String, CodingKey {
        case uploadId = "upload_id", objectKey = "object_key", partSizeBytes = "part_size_bytes"
        case totalParts = "total_parts", parts
    }
}

public struct MultipartUploadedPart: Decodable, Equatable, Sendable {
    public var partNumber: Int
    public var etag: String
    public var sizeBytes: Int64
    enum CodingKeys: String, CodingKey { case partNumber = "part_number", etag, sizeBytes = "size_bytes" }
}

public struct MultipartListPartsResponse: Decodable, Sendable {
    public var uploadId: String
    public var objectKey: String
    public var uploadedParts: [MultipartUploadedPart]
    enum CodingKeys: String, CodingKey { case uploadId = "upload_id", objectKey = "object_key", uploadedParts = "uploaded_parts" }
}

public struct MultipartCompletePartInput: Encodable, Sendable {
    public var partNumber: Int
    public var etag: String
    enum CodingKeys: String, CodingKey { case partNumber = "part_number", etag }
    public init(partNumber: Int, etag: String) { self.partNumber = partNumber; self.etag = etag }
}

public struct MultipartCompleteRequest: Encodable, Sendable {
    public var videoId: String
    public var playerId: String
    public var filename: String
    public var parts: [MultipartCompletePartInput]
    enum CodingKeys: String, CodingKey { case videoId = "video_id", playerId = "player_id", filename, parts }
    public init(videoId: String, playerId: String, filename: String, parts: [MultipartCompletePartInput]) {
        self.videoId = videoId; self.playerId = playerId; self.filename = filename; self.parts = parts
    }
}

public struct MultipartCompleteResponse: Decodable, Sendable {
    public var objectKey: String
    public var sizeBytes: Int64
    public var checksum: String
    enum CodingKeys: String, CodingKey { case objectKey = "object_key", sizeBytes = "size_bytes", checksum }
}

public struct MultipartFinalizeResponse: Decodable, Sendable {
    public var videoId: String
    public var recordId: String
    public var sessionId: String
    enum CodingKeys: String, CodingKey { case videoId = "video_id", recordId = "record_id", sessionId = "session_id" }
}

public struct MultipartAbortRequest: Encodable, Sendable {
    public var videoId: String
    public var playerId: String
    public var filename: String
    enum CodingKeys: String, CodingKey { case videoId = "video_id", playerId = "player_id", filename }
    public init(videoId: String, playerId: String, filename: String) {
        self.videoId = videoId; self.playerId = playerId; self.filename = filename
    }
}
