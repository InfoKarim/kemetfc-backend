//
//  MultipartVideoUploader.swift
//  KemetFCTracker
//
//  Resumable, FOREGROUND-only direct-to-R2 multipart video uploader
//  (2026-10-01, Phase 2). Deliberately NOT wired into
//  AssessmentSessionViewModel.retryAllPending()'s automatic path in this
//  phase — see this phase's own report for why that's the safety
//  boundary. Callers invoke `upload(fileURL:metadata:)` explicitly.
//
//  Never reads the whole source MOV into memory — only one part's worth
//  of bytes (<= MultipartUploadPlan.partSizeBytes) is ever resident at
//  once. Never modifies or deletes the source MOV; only its OWN small
//  temporary per-part files are ever removed, and only after a
//  confirmed-successful PUT.
//

import Foundation

@MainActor
public final class MultipartVideoUploader {
    private let apiClient: KemetAPIClient
    private let stateStore: MultipartUploadStateStore
    /// A SEPARATE, minimal session for R2 part uploads — deliberately NOT
    /// `apiClient.underlyingSession` (which carries the Kemet cookie
    /// jar/CSRF header machinery). Presigned R2 URLs are a different
    /// origin entirely and must never receive Kemet auth material.
    private let partUploadSession: URLSession
    private let maxAttemptsPerPart: Int

    public init(
        apiClient: KemetAPIClient,
        stateStore: MultipartUploadStateStore,
        partUploadSession: URLSession = URLSession(configuration: .ephemeral),
        maxAttemptsPerPart: Int = 5
    ) {
        self.apiClient = apiClient
        self.stateStore = stateStore
        self.partUploadSession = partUploadSession
        self.maxAttemptsPerPart = maxAttemptsPerPart
    }

    /// Drives one recording's upload to completion: resolves/resumes
    /// state, uploads every incomplete part (retrying only THAT part on
    /// failure), completes the multipart upload, then finalizes. Safe to
    /// call again after any failure — it always resumes from persisted
    /// state rather than restarting from part 1.
    public func upload(fileURL: URL, metadata: MultipartInitiateRequest) async throws -> MultipartFinalizeResponse {
        let state = try await resolveState(fileURL: fileURL, metadata: metadata)
        let reconciled = try await reconcile(state: state, metadata: metadata)
        try await uploadIncompleteParts(state: reconciled, fileURL: fileURL, metadata: metadata)

        guard let finalState = stateStore.state(for: metadata.recordingId), finalState.isComplete else {
            throw MultipartUploadError.noActiveUploadState
        }
        try await completeMultipartUpload(state: finalState, metadata: metadata)
        return try await finalizeUpload(state: finalState, metadata: metadata)
    }

    // MARK: - State resolution (fresh initiate, or resume existing)

    private func resolveState(fileURL: URL, metadata: MultipartInitiateRequest) async throws -> MultipartUploadState {
        if let existing = stateStore.state(for: metadata.recordingId) {
            return existing
        }
        let response = try await apiClient.initiateMultipartUpload(metadata)
        let state = MultipartUploadState(
            recordingId: metadata.recordingId, videoId: metadata.videoId, recordId: metadata.recordId,
            sessionId: metadata.sessionId, multipartUploadId: response.uploadId, objectKey: response.objectKey,
            partSizeBytes: response.partSizeBytes, totalParts: response.totalParts
        )
        stateStore.upsert(state)
        pendingPartURLs[metadata.recordingId] = Dictionary(uniqueKeysWithValues: response.parts.map { ($0.partNumber, $0.url) })
        return state
    }

    /// Part URLs returned by `initiate` are cached in memory only for
    /// THIS run — if the app restarts, a fresh `initiate` call (which is
    /// itself idempotent server-side, see Phase 1) regenerates them.
    private var pendingPartURLs: [String: [Int: String]] = [:]

    // MARK: - Reconciliation (Part 6: server/R2 truth always wins)

    /// Reconciles local persisted state against the backend's own
    /// list-parts truth BEFORE uploading anything. A part the server
    /// confirms is accepted (its ETag overwrites whatever local state
    /// claimed); a part local state claims complete but the server does
    /// NOT confirm is treated as incomplete and will be re-uploaded —
    /// this is what makes "upload succeeded -> response/local
    /// persistence lost -> app restarted" recoverable without
    /// duplicating or corrupting the upload.
    private func reconcile(state: MultipartUploadState, metadata: MultipartInitiateRequest) async throws -> MultipartUploadState {
        let serverParts: MultipartListPartsResponse
        do {
            serverParts = try await apiClient.listMultipartUploadParts(
                uploadId: state.multipartUploadId, videoId: state.videoId, filename: metadata.filename, playerId: metadata.playerId
            )
        } catch {
            // If the upload was already completed+finalized by an
            // earlier run whose response was lost, list-parts may 404
            // (R2 multipart state no longer exists) — that's fine; the
            // caller's subsequent complete/finalize calls are themselves
            // idempotent (Phase 1) and will recover correctly.
            return state
        }

        var reconciled = state
        let serverPartNumbers = Set(serverParts.uploadedParts.map(\.partNumber))
        for partNumber in reconciled.completedParts.keys where !serverPartNumbers.contains(partNumber) {
            reconciled.completedParts.removeValue(forKey: partNumber)
        }
        for part in serverParts.uploadedParts {
            reconciled.completedParts[part.partNumber] = part.etag
        }
        stateStore.upsert(reconciled)
        return reconciled
    }

    // MARK: - Part upload (bounded memory, retried individually)

    private func uploadIncompleteParts(state: MultipartUploadState, fileURL: URL, metadata: MultipartInitiateRequest) async throws {
        var partURLs = pendingPartURLs[metadata.recordingId]
        if partURLs == nil {
            // Resuming after a restart — re-initiate (idempotent) to get
            // fresh presigned URLs; the returned upload_id must match
            // what we already persisted, since it's the SAME object key.
            let response = try await apiClient.initiateMultipartUpload(metadata)
            guard response.uploadId == state.multipartUploadId else {
                throw MultipartUploadError.identityMismatch(expected: state.multipartUploadId, found: response.uploadId)
            }
            partURLs = Dictionary(uniqueKeysWithValues: response.parts.map { ($0.partNumber, $0.url) })
            pendingPartURLs[metadata.recordingId] = partURLs
        }

        for partNumber in state.incompletePartNumbers {
            guard let url = partURLs?[partNumber] else {
                throw MultipartUploadError.partUploadFailed(partNumber: partNumber, statusCode: nil)
            }
            try await uploadSinglePartWithRetry(
                recordingId: state.recordingId, partNumber: partNumber, url: url,
                fileURL: fileURL, fileSizeBytes: metadata.fileSizeBytes, partSizeBytes: state.partSizeBytes
            )
        }
    }

    /// Retries ONLY this one part — a failed part never restarts the
    /// whole upload, and every other already-confirmed part is left
    /// untouched (Part 9).
    private func uploadSinglePartWithRetry(
        recordingId: String, partNumber: Int, url: String, fileURL: URL, fileSizeBytes: Int64, partSizeBytes: Int64
    ) async throws {
        var lastError: Error = MultipartUploadError.partUploadFailed(partNumber: partNumber, statusCode: nil)
        for attempt in 1...maxAttemptsPerPart {
            stateStore.recordAttempt(recordingId: recordingId, partNumber: partNumber)
            do {
                let etag = try await uploadPart(partNumber: partNumber, url: url, fileURL: fileURL, fileSizeBytes: fileSizeBytes, partSizeBytes: partSizeBytes)
                stateStore.recordCompletedPart(recordingId: recordingId, partNumber: partNumber, etag: etag)
                return
            } catch {
                lastError = error
                if attempt < maxAttemptsPerPart {
                    let backoffSeconds = pow(2.0, Double(attempt))
                    try? await Task.sleep(nanoseconds: UInt64(backoffSeconds * 1_000_000_000))
                }
            }
        }
        throw lastError
    }

    /// Reads ONLY this part's byte range (never the whole file), writes
    /// it to a small temp file, PUTs it directly to the presigned R2 URL
    /// with NO Kemet cookies/CSRF headers, then removes ONLY that temp
    /// file — the source MOV is opened read-only and never modified.
    private func uploadPart(partNumber: Int, url: String, fileURL: URL, fileSizeBytes: Int64, partSizeBytes: Int64) async throws -> String {
        let range = MultipartUploadPlan.byteRange(forPart: partNumber, fileSizeBytes: fileSizeBytes, partSizeBytes: partSizeBytes)
        let handle = try FileHandle(forReadingFrom: fileURL)
        let data: Data
        do {
            try handle.seek(toOffset: UInt64(range.lowerBound))
            guard let read = try handle.read(upToCount: Int(range.upperBound - range.lowerBound)) else {
                try handle.close()
                throw MultipartUploadError.fileReadFailed(partNumber: partNumber)
            }
            data = read
            try handle.close()
        } catch {
            try? handle.close()
            throw error
        }

        let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).part")
        try data.write(to: tempURL, options: .atomic)
        defer { try? FileManager.default.removeItem(at: tempURL) }

        guard let requestURL = URL(string: url) else {
            throw MultipartUploadError.partUploadFailed(partNumber: partNumber, statusCode: nil)
        }
        var request = URLRequest(url: requestURL)
        request.httpMethod = "PUT"
        request.httpShouldHandleCookies = false  // never send Kemet (or any) cookies to R2

        let response: URLResponse
        do {
            (_, response) = try await partUploadSession.upload(for: request, fromFile: tempURL)
        } catch {
            throw MultipartUploadError.partUploadFailed(partNumber: partNumber, statusCode: nil)
        }

        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw MultipartUploadError.partUploadFailed(partNumber: partNumber, statusCode: (response as? HTTPURLResponse)?.statusCode)
        }
        guard let etagHeader = http.value(forHTTPHeaderField: "ETag") ?? http.value(forHTTPHeaderField: "Etag") else {
            throw MultipartUploadError.missingETagInResponse(partNumber: partNumber)
        }
        return etagHeader.trimmingCharacters(in: CharacterSet(charactersIn: "\""))
    }

    // MARK: - Complete + finalize

    private func completeMultipartUpload(state: MultipartUploadState, metadata: MultipartInitiateRequest) async throws {
        let parts = state.completedParts
            .sorted { $0.key < $1.key }
            .map { MultipartCompletePartInput(partNumber: $0.key, etag: $0.value) }
        _ = try await apiClient.completeMultipartUpload(
            uploadId: state.multipartUploadId,
            request: MultipartCompleteRequest(videoId: state.videoId, playerId: metadata.playerId, filename: metadata.filename, parts: parts)
        )
    }

    private func finalizeUpload(state: MultipartUploadState, metadata: MultipartInitiateRequest) async throws -> MultipartFinalizeResponse {
        let response = try await apiClient.finalizeMultipartUpload(uploadId: state.multipartUploadId, request: metadata)
        guard response.videoId == metadata.videoId, response.recordId == metadata.recordId, response.sessionId == metadata.sessionId else {
            throw MultipartUploadError.finalizeIdentityMismatch
        }
        // Only now, with the backend's own confirmed identity in hand,
        // is this recording's multipart state no longer needed locally.
        stateStore.removeState(for: metadata.recordingId)
        return response
    }
}
