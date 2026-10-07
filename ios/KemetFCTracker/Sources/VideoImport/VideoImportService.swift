//
//  VideoImportService.swift
//  KemetFCTracker
//
//  Brings a video the coach already has (Photos or Files) into the SAME durable assessment storage a camera
//  recording uses, as the same kind of PendingAssessmentRecord, so the existing upload path and the future AI
//  pipeline treat it exactly like a recording.
//
//    1. the picker hands over a file; it is copied (never read into memory) into a staging folder
//    2. the copy is moved into <Documents>/Recordings, named by the new record id, KEEPING its container
//       extension (no re-encoding, no trimming, no recompression of the media)
//    3. size, SHA-256 (streamed in chunks), duration, picture size and frame rate are measured on that stored copy
//    4. the imported-video integrity check runs (not the camera "covers the assessment timer" rule)
//    5. the pending record is written with the SELECTED player's id and videoSource IMPORTED_VIDEO
//
//  The record is not uploaded until the coach confirms SAVE ASSESSMENT (`coachSavedAt`), and a cancel before
//  that removes only this fresh copy and its record. The player is always the one chosen before the video; it
//  is never read from a file name or from the video. This file never starts a recording, never touches the
//  camera, tracking, telemetry or DockKit, and never uploads: saving hands the record to the existing upload
//  code in AssessmentSessionViewModel.
//

import AVFoundation
import CoreTransferable
import CryptoKit
import Foundation
import UniformTypeIdentifiers

enum VideoImportError: LocalizedError {
    case copyFailed(String)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .copyFailed(let why): return "The video could not be copied onto this iPhone. \(why)"
        case .cancelled: return "Import cancelled."
        }
    }
}

/// What the Photos picker hands back: the video file, already copied to the staging folder.
struct PickedVideoFile: Transferable, Sendable {
    let stagedURL: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { received in
            // `received.file` is only valid inside this closure, so copy it out here (a file-level copy,
            // not a read into memory).
            let staged = try VideoImportService.stage(copyOf: received.file, coordinated: false)
            return PickedVideoFile(stagedURL: staged)
        }
    }
}

@MainActor
struct VideoImportService {
    let store: PendingAssessmentStore

    // MARK: staging (any thread)

    nonisolated static var stagingDirectory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("ImportStaging")
    }

    /// Copies a picked file into the staging folder and returns the copy. `coordinated` is for files that may
    /// live in a cloud provider (Files), where the system must be asked to make the file available first.
    nonisolated static func stage(copyOf source: URL, coordinated: Bool) throws -> URL {
        let directory = stagingDirectory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let ext = source.pathExtension.lowercased()
        let target = directory.appendingPathComponent("\(UUID().uuidString).\(ext.isEmpty ? "mov" : ext)")
        if coordinated {
            var coordinationError: NSError?
            var copyError: Error?
            NSFileCoordinator().coordinate(readingItemAt: source, options: [.withoutChanges], error: &coordinationError) { readable in
                do { try FileManager.default.copyItem(at: readable, to: target) } catch { copyError = error }
            }
            if let failure = coordinationError ?? copyError {
                try? FileManager.default.removeItem(at: target)
                throw VideoImportError.copyFailed(failure.localizedDescription)
            }
        } else {
            do { try FileManager.default.copyItem(at: source, to: target) } catch {
                throw VideoImportError.copyFailed(error.localizedDescription)
            }
        }
        return target
    }

    /// Removes staged copies that no import is using any more (left by a crash or a cancel).
    nonisolated static func cleanStaging(olderThan age: TimeInterval = 3_600) {
        let directory = stagingDirectory
        guard let items = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        for item in items {
            let modified = (try? item.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            if Date().timeIntervalSince(modified) > age { try? FileManager.default.removeItem(at: item) }
        }
    }

    // MARK: measuring (any thread)

    /// SHA-256 of the file, read in 4 MiB chunks so a large video never sits in memory.
    nonisolated static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk = try autoreleasepool { try handle.read(upToCount: 4 * 1_048_576) }
            guard let chunk, !chunk.isEmpty else { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    nonisolated static func measure(url: URL) async -> ImportedVideoMeasurement {
        let manager = FileManager.default
        let exists = manager.fileExists(atPath: url.path)
        let size = (try? manager.attributesOfItem(atPath: url.path)[.size] as? Int64) ?? 0
        var result = ImportedVideoMeasurement(
            fileExists: exists, sizeBytes: size, isReadable: false, isPlayable: false, hasProtectedContent: false,
            hasVideoTrack: false, durationSeconds: 0, width: 0, height: 0, frameRate: 0
        )
        guard exists, size > 0 else { return result }
        let asset = AVURLAsset(url: url)
        do {
            let (readable, playable, protected, duration) = try await asset.load(.isReadable, .isPlayable, .hasProtectedContent, .duration)
            result.isReadable = readable
            result.isPlayable = playable
            result.hasProtectedContent = protected
            result.durationSeconds = duration.seconds
            guard let track = try await asset.loadTracks(withMediaType: .video).first else { return result }
            result.hasVideoTrack = true
            let (natural, transform, nominal, minFrame) = try await track.load(.naturalSize, .preferredTransform, .nominalFrameRate, .minFrameDuration)
            let upright = CGRect(origin: .zero, size: natural).applying(transform)
            result.width = Int(abs(upright.width).rounded())
            result.height = Int(abs(upright.height).rounded())
            if nominal > 0 {
                result.frameRate = Double(nominal)
            } else if minFrame.isValid, minFrame.seconds > 0 {
                result.frameRate = 1.0 / minFrame.seconds
            }
        } catch {
            result.isReadable = false
        }
        return result
    }

    // MARK: importing

    /// Moves a staged video into durable storage, measures and checks it, and writes the pending record.
    /// The record is created even when the video has a problem (as a failed, never-uploadable record, like a
    /// recording that fails its check) so the review screen can say exactly why; cancelling removes it.
    func importStagedVideo(at stagedURL: URL, player: KemetPlayerSummary) async throws -> PendingAssessmentRecord {
        let recordId = UUID().uuidString
        let ext = stagedURL.pathExtension.lowercased().isEmpty ? "mov" : stagedURL.pathExtension.lowercased()
        let destination = store.newRecordingFileURL(recordingId: recordId, fileExtension: ext)
        do {
            try FileManager.default.moveItem(at: stagedURL, to: destination)
        } catch {
            try? FileManager.default.removeItem(at: stagedURL)
            throw VideoImportError.copyFailed(error.localizedDescription)
        }
        func discardCopy() { try? FileManager.default.removeItem(at: destination) }

        let hash: String
        do {
            hash = try await Task.detached(priority: .userInitiated) { try Self.sha256(of: destination) }.value
        } catch {
            discardCopy()
            throw VideoImportError.copyFailed("The video could not be read to check it.")
        }
        let measurement = await Self.measure(url: destination)
        if Task.isCancelled {
            discardCopy()
            throw VideoImportError.cancelled
        }

        let now = Date()
        var record = PendingAssessmentRecord(
            id: recordId,
            playerId: player.playerId,
            playerName: player.fullName,
            localVideoRelativePath: store.relativePath(for: destination),
            startTime: now,
            trackingMode: TrackingMode.smartSoccer.rawValue
        )
        record.stopTime = now
        record.durationSeconds = measurement.durationSeconds.isFinite ? max(measurement.durationSeconds, 0) : 0
        record.fileSizeBytes = measurement.sizeBytes
        record.resolution = measurement.width > 0 && measurement.height > 0 ? "\(measurement.width)x\(measurement.height)" : nil
        record.frameRateFps = measurement.frameRate > 0 ? measurement.frameRate : nil
        record.videoSource = AssessmentVideoSource.importedVideo.rawValue
        record.contentSha256 = hash

        switch ImportedVideoIntegrity.evaluate(measurement) {
        case .valid:
            record.uploadState = .localSaved
            if store.records.contains(where: { $0.contentSha256 == hash && $0.playerId == player.playerId && $0.id != recordId }) {
                record.integrityWarning = "This exact video was already imported for this player."
            }
        case .invalid(let reason):
            record.uploadState = .failedPermanent
            record.integrityFailure = reason
            record.lastError = reason
        }
        store.upsert(record)
        return record
    }

    /// The coach confirmed SAVE ASSESSMENT. Only a healthy imported video can be saved; from here the record
    /// is an ordinary pending record that the existing upload code picks up.
    @discardableResult
    func markSaved(recordId: String) -> Bool {
        guard var record = store.record(id: recordId), record.isImportedVideo,
              record.integrityFailure == nil, store.fileExistsSync(for: record) else { return false }
        if record.coachSavedAt == nil {
            record.coachSavedAt = Date()
            store.upsert(record)
        }
        return true
    }

    /// Cancel before SAVE: removes only this freshly made copy and its record. A saved record, a camera
    /// recording and any older record can never be removed through this.
    @discardableResult
    func discardUnsaved(recordId: String) -> Bool {
        guard let record = store.record(id: recordId), record.isImportedVideo, record.coachSavedAt == nil else { return false }
        return store.deleteRecord(id: recordId)
    }
}
