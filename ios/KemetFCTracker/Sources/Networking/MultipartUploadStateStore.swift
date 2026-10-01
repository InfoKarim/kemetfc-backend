//
//  MultipartUploadStateStore.swift
//  KemetFCTracker
//
//  Durable, per-recording multipart-upload progress (2026-10-01, Phase 2).
//  Persisted as plain JSON, same atomic-write/backup pattern as
//  PendingAssessmentStore.swift — a crash/restart must never lose a
//  successfully-uploaded part. This file NEVER touches the original MOV;
//  it only tracks which byte ranges of it have already reached R2.
//

import Foundation

public struct MultipartUploadState: Codable, Equatable, Sendable {
    public var recordingId: String
    public var videoId: String
    public var recordId: String
    public var sessionId: String
    public var multipartUploadId: String
    public var objectKey: String
    public var partSizeBytes: Int64
    public var totalParts: Int
    /// partNumber -> ETag, for every part CONFIRMED uploaded (either by
    /// this client's own successful PUT, or reconciled from the
    /// backend's list-parts truth — see MultipartUploadReconciler).
    public var completedParts: [Int: String]
    public var attemptsPerPart: [Int: Int]
    public var createdAt: Date
    public var lastUpdatedAt: Date

    public init(
        recordingId: String, videoId: String, recordId: String, sessionId: String,
        multipartUploadId: String, objectKey: String, partSizeBytes: Int64, totalParts: Int,
        completedParts: [Int: String] = [:], attemptsPerPart: [Int: Int] = [:],
        createdAt: Date = Date(), lastUpdatedAt: Date = Date()
    ) {
        self.recordingId = recordingId
        self.videoId = videoId
        self.recordId = recordId
        self.sessionId = sessionId
        self.multipartUploadId = multipartUploadId
        self.objectKey = objectKey
        self.partSizeBytes = partSizeBytes
        self.totalParts = totalParts
        self.completedParts = completedParts
        self.attemptsPerPart = attemptsPerPart
        self.createdAt = createdAt
        self.lastUpdatedAt = lastUpdatedAt
    }

    public var isComplete: Bool { completedParts.count == totalParts && (1...totalParts).allSatisfy { completedParts[$0] != nil } }
    public func isPartComplete(_ partNumber: Int) -> Bool { completedParts[partNumber] != nil }
    public var firstIncompletePart: Int? { (1...totalParts).first { completedParts[$0] == nil } }
    public var incompletePartNumbers: [Int] { (1...totalParts).filter { completedParts[$0] == nil } }
}

/// Thread-confined to the main actor — same reasoning as
/// PendingAssessmentStore (a small JSON file read/write is cheap enough
/// not to need a background queue, and every caller already runs on
/// @MainActor).
@MainActor
public final class MultipartUploadStateStore {
    public static let defaultDocumentsDirectory: URL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    private static let indexFileName = "multipart_uploads.json"
    private static let backupFileName = "multipart_uploads.backup.json"

    public let rootDirectory: URL
    private var indexFileURL: URL { rootDirectory.appendingPathComponent(Self.indexFileName) }
    private var backupFileURL: URL { rootDirectory.appendingPathComponent(Self.backupFileName) }

    private var states: [String: MultipartUploadState] = [:]  // keyed by recordingId
    public private(set) var lastPersistenceError: String?

    public init(rootDirectory: URL = MultipartUploadStateStore.defaultDocumentsDirectory) {
        self.rootDirectory = rootDirectory
        try? FileManager.default.createDirectory(at: rootDirectory, withIntermediateDirectories: true)
        load()
    }

    public func state(for recordingId: String) -> MultipartUploadState? { states[recordingId] }

    public func upsert(_ state: MultipartUploadState) {
        var updated = state
        updated.lastUpdatedAt = Date()
        states[state.recordingId] = updated
        persist()
    }

    /// Called immediately after a single part's upload is confirmed —
    /// the whole point of persisting after EVERY part, not just at the
    /// end, so a crash mid-upload never loses already-confirmed work.
    public func recordCompletedPart(recordingId: String, partNumber: Int, etag: String) {
        guard var state = states[recordingId] else { return }
        state.completedParts[partNumber] = etag
        upsert(state)
    }

    public func recordAttempt(recordingId: String, partNumber: Int) {
        guard var state = states[recordingId] else { return }
        state.attemptsPerPart[partNumber, default: 0] += 1
        upsert(state)
    }

    public func removeState(for recordingId: String) {
        states.removeValue(forKey: recordingId)
        persist()
    }

    // MARK: - Persistence (same atomic-write/backup pattern as PendingAssessmentStore)

    private func load() {
        guard let data = try? Data(contentsOf: indexFileURL) else { return }
        guard let decoded = try? JSONDecoder.kemet.decode([String: MultipartUploadState].self, from: data) else {
            if let backupData = try? Data(contentsOf: backupFileURL),
               let backupDecoded = try? JSONDecoder.kemet.decode([String: MultipartUploadState].self, from: backupData) {
                states = backupDecoded
                lastPersistenceError = "The multipart upload index was corrupt; recovered from the last known-good backup."
            } else {
                lastPersistenceError = "The multipart upload index was unreadable and could not be recovered from a backup."
            }
            return
        }
        states = decoded
        lastPersistenceError = nil
    }

    private func persist() {
        do {
            let data = try JSONEncoder.kemet.encode(states)
            if FileManager.default.fileExists(atPath: indexFileURL.path) {
                try? FileManager.default.removeItem(at: backupFileURL)
                try? FileManager.default.copyItem(at: indexFileURL, to: backupFileURL)
            }
            try data.write(to: indexFileURL, options: .atomic)
            lastPersistenceError = nil
        } catch {
            lastPersistenceError = "Failed to save the multipart upload index: \(error.localizedDescription)."
        }
    }
}
