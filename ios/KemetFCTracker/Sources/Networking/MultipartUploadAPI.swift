//
//  MultipartUploadAPI.swift
//  KemetFCTracker
//
//  Typed KemetAPIClient methods for the 5 backend Phase 1 endpoints
//  (2026-10-01, Phase 2). Each method is a thin, direct mapping onto the
//  generic post/get/delete primitives already on KemetAPIClient — no
//  identity logic is re-derived here; every value is supplied by the
//  caller, matching the backend's own "never trust a bare object key"
//  design (Phase 1 report).
//

import Foundation

extension KemetAPIClient {
    public func initiateMultipartUpload(_ request: MultipartInitiateRequest) async throws -> MultipartInitiateResponse {
        try await post(path: "/videos/uploads/initiate", body: request, as: MultipartInitiateResponse.self)
    }

    public func listMultipartUploadParts(uploadId: String, videoId: String, filename: String, playerId: String) async throws -> MultipartListPartsResponse {
        func encode(_ value: String) -> String {
            value.addingPercentEncoding(withAllowedCharacters: .urlQueryValueAllowed) ?? value
        }
        let query = "video_id=\(encode(videoId))&filename=\(encode(filename))&player_id=\(encode(playerId))"
        return try await get(path: "/videos/uploads/\(uploadId)/parts?\(query)", as: MultipartListPartsResponse.self)
    }

    public func completeMultipartUpload(uploadId: String, request: MultipartCompleteRequest) async throws -> MultipartCompleteResponse {
        try await post(path: "/videos/uploads/\(uploadId)/parts/complete", body: request, as: MultipartCompleteResponse.self)
    }

    public func finalizeMultipartUpload(uploadId: String, request: MultipartFinalizeRequest) async throws -> MultipartFinalizeResponse {
        try await post(path: "/videos/uploads/\(uploadId)/finalize", body: request, as: MultipartFinalizeResponse.self)
    }

    public func abortMultipartUpload(uploadId: String, request: MultipartAbortRequest) async throws {
        try await delete(path: "/videos/uploads/\(uploadId)", body: request)
    }
}

private extension CharacterSet {
    /// RFC 3986 query-value-safe set (stricter than `.urlQueryAllowed`,
    /// which still permits `&`/`=` — unsafe inside a value we're
    /// concatenating into a hand-built query string).
    static let urlQueryValueAllowed: CharacterSet = {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return allowed
    }()
}
