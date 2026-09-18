//
//  PendingAssessmentStore.swift
//  KemetFCTracker
//
//  The durable local record of every assessment recording. Recording
//  must NEVER depend on backend connectivity — this store exists
//  precisely so a recording's existence and upload progress survive
//  network failure, backend restart, app restart/termination, and WiFi
//  loss, independent of whatever AssessmentSessionViewModel is doing in
//  memory at any given moment.
//
//  Persisted as plain JSON under an injected root directory (production
//  uses the app's real Documents directory; XCTest targets MUST inject
//  their own unique temporary directory — see PendingAssessmentStoreTests
//  — after a real physical-device run found test fixtures leaking into
//  the production queue file, since both shared the same hardcoded
//  Documents path before this fix).
//

import AVFoundation
import Foundation

public struct PendingAssessmentRecord: Codable, Identifiable, Equatable {
    public enum UploadState: String, Codable {
        /// Recording is actively in progress — not yet a queue entry in
        /// the practical sense (no stopTime), but persisted from the
        /// instant Start Assessment is tapped so even a crash mid-
        /// recording leaves a trace.
        case recording = "RECORDING"
        /// Stop Assessment finished: file existence, size>0, and
        /// AVAsset-readability have all been VERIFIED. The video is
        /// durably safe regardless of what happens next. Eligible for
        /// upload — see PendingAssessmentRecord.isRetryable.
        case localSaved = "LOCAL_SAVED"
        /// Verified and saved, queued for upload but not yet attempted
        /// (or waiting for connectivity) — spec's PENDING_UPLOAD.
        case pendingUpload = "PENDING_UPLOAD"
        case uploading = "UPLOADING"
        case uploaded = "UPLOADED"
        /// A transient failure (network/timeout/5xx) — safe and expected
        /// to retry, automatically or via the coach's Retry Upload.
        case failedRetryable = "FAILED_RETRYABLE"
        /// A failure retrying will never fix on its own (corrupt local
        /// file, a 4xx the server will always reject) — still never
        /// deleted, but not auto-retried forever. This does NOT imply
        /// the local file is absent — see PendingAssessmentStore
        /// .fileHealth(for:), which checks the real file independently
        /// of this state (spec: "FAILED_PERMANENT must not automatically
        /// imply that a valid local file is absent").
        case failedPermanent = "FAILED_PERMANENT"
    }

    /// Schema version this record was constructed under — bumped
    /// whenever a field's meaning changes in a way old records can't be
    /// blindly reinterpreted under. Currently always the latest
    /// (`PendingAssessmentRecord.currentSchemaVersion`); a decoded
    /// record from an older version is normalized by
    /// PendingAssessmentStore's migration pass before use.
    public static let currentSchemaVersion = 1

    /// The stable local recording ID — generated once when Start
    /// Assessment is tapped, before any network call. This IS
    /// clientRecordingId: the idempotency key for both tracking-session
    /// creation (client_recording_id) and video upload (video_id/
    /// record_id are derived from it) — see AssessmentSessionViewModel.
    public var id: String
    public var playerId: String
    public var playerName: String
    /// The backend's tracking_session_id, once created — this app has
    /// no separate "assessment" entity distinct from a tracking session
    /// (assessments are PUBLISHED from a session later, a different
    /// flow — see PublishTrackingAssessmentSchema); there is deliberately
    /// no separate assessmentSessionId field to avoid tracking a second
    /// ID that would always just duplicate this one.
    public var backendSessionId: String?
    public var serverVideoId: String?
    /// Relative to the store's root directory — never an absolute path,
    /// since the app's container path can change between launches/OS
    /// versions.
    public var localVideoRelativePath: String
    public var startTime: Date
    public var stopTime: Date?
    public var durationSeconds: Double?
    public var fileSizeBytes: Int64?
    public var resolution: String?
    public var frameRateFps: Double?
    public var trackingMode: String
    public var uploadState: UploadState
    public var lastError: String?
    public var uploadAttempts: Int
    public var lastAttemptAt: Date?

    public init(
        id: String,
        playerId: String,
        playerName: String,
        localVideoRelativePath: String,
        startTime: Date,
        trackingMode: String
    ) {
        self.id = id
        self.playerId = playerId
        self.playerName = playerName
        self.backendSessionId = nil
        self.serverVideoId = nil
        self.localVideoRelativePath = localVideoRelativePath
        self.startTime = startTime
        self.stopTime = nil
        self.durationSeconds = nil
        self.fileSizeBytes = nil
        self.resolution = nil
        self.frameRateFps = nil
        self.trackingMode = trackingMode
        self.uploadState = .recording
        self.lastError = nil
        self.uploadAttempts = 0
        self.lastAttemptAt = nil
    }

    public var isFinished: Bool { uploadState == .uploaded }
    /// Eligible to have finalizeAndUpload(_:) attempted on it — either
    /// because a previous attempt is retryable, or because it's a
    /// verified-safe local recording that simply hasn't been attempted
    /// yet (spec correction: LOCAL_SAVED was previously excluded here,
    /// stranding it with no automatic OR manual way to ever upload —
    /// confirmed via a live example on the physical device,
    /// B1C50F7F-5D92-45ED-A9AD-D6FABF429659, stuck at LOCAL_SAVED with a
    /// verified 20s recording and no way to retry it).
    public var isRetryable: Bool {
        switch uploadState {
        case .localSaved, .pendingUpload, .failedRetryable:
            return true
        case .recording, .uploading, .uploaded, .failedPermanent:
            return false
        }
    }
    /// Eligible for the (opt-in, never automatic-by-default) local-file
    /// cleanup policy — spec Phase 8: only once the server has
    /// genuinely confirmed the video exists, never merely "we tried."
    public var isEligibleForCleanup: Bool {
        uploadState == .uploaded && serverVideoId != nil
    }
}

/// The real, on-disk truth about a record's video file — computed fresh
/// each time from FileManager/AVAsset, never inferred from uploadState
/// alone (spec: "Never claim a video is saved unless the file exists,
/// has size greater than zero, and is AVAsset-readable").
public enum LocalFileHealth {
    case verified(sizeBytes: Int64)
    case missing
    case empty
    case unreadable
}

/// Thread-confined to the main actor — every caller (AssessmentSessionViewModel,
/// the pending-uploads UI) already runs there, and a small JSON file
/// read/write is cheap enough not to need a background queue of its own.
@MainActor
public final class PendingAssessmentStore: ObservableObject {
    @Published public private(set) var records: [PendingAssessmentRecord] = []
    /// Set whenever persistence genuinely fails in a way that could lose
    /// data (encode failure, write failure, decode failure requiring
    /// quarantine) — spec: "Expose a diagnostic error without falsely
    /// reporting an empty queue as success." Field Test Mode / a future
    /// settings screen can surface this; it is never silently dropped.
    @Published public private(set) var lastPersistenceError: String?
    /// Records from the on-disk file that failed to decode even
    /// individually — quarantined (kept in the raw backup file, excluded
    /// from `records`) rather than silently discarded. Spec: "Preserve
    /// readable records and quarantine/report malformed records."
    @Published public private(set) var quarantinedRecordCount = 0

    /// Real Documents directory — production default. Tests MUST pass
    /// their own unique temporary `rootDirectory` to `init(rootDirectory:)`
    /// instead of relying on this, so test runs on a physical device can
    /// never read, write, or pollute the real app's data (spec: the
    /// exact contamination this fixes was found via a physical device
    /// pull showing XCTest fixtures — playerId "TEST_PLAYER" — mixed
    /// into the production queue).
    public static let defaultDocumentsDirectory: URL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    private static let recordingsDirectoryName = "Recordings"
    private static let indexFileName = "pending_assessments.json"
    private static let backupFileName = "pending_assessments.backup.json"
    private static let quarantineFileName = "pending_assessments.quarantine.json"

    /// This instance's storage root — `defaultDocumentsDirectory` in
    /// production, a unique per-test directory under XCTest.
    public let rootDirectory: URL
    private var indexFileURL: URL { rootDirectory.appendingPathComponent(Self.indexFileName) }
    private var backupFileURL: URL { rootDirectory.appendingPathComponent(Self.backupFileName) }
    private var quarantineFileURL: URL { rootDirectory.appendingPathComponent(Self.quarantineFileName) }

    public init(rootDirectory: URL = PendingAssessmentStore.defaultDocumentsDirectory) {
        self.rootDirectory = rootDirectory
        try? FileManager.default.createDirectory(at: rootDirectory, withIntermediateDirectories: true)
        load()
    }

    /// A fresh, durable file URL for a new recording — inside
    /// `<root>/Recordings`, which (in production, under Documents) iOS
    /// backs up and does not purge under storage pressure the way it
    /// may purge tmp/. Creates the directory on first use.
    public func newRecordingFileURL(recordingId: String) -> URL {
        let directory = rootDirectory.appendingPathComponent(Self.recordingsDirectoryName)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("\(recordingId).mov")
    }

    public func relativePath(for fileURL: URL) -> String {
        let rootPath = rootDirectory.path
        var filePath = fileURL.path
        if filePath.hasPrefix(rootPath) {
            filePath.removeFirst(rootPath.count)
        }
        return filePath.hasPrefix("/") ? String(filePath.dropFirst()) : filePath
    }

    /// The absolute URL for a record's video, resolved against THIS
    /// store's own root — replaces the old
    /// PendingAssessmentRecord.localVideoURL computed property, which
    /// resolved against a single hardcoded static path and was the
    /// mechanism that let test and production data collide.
    public func localVideoURL(for record: PendingAssessmentRecord) -> URL {
        rootDirectory.appendingPathComponent(record.localVideoRelativePath)
    }

    /// The real, current, verified state of a record's local file — spec:
    /// "Never claim a video is saved unless the file exists, has size
    /// greater than zero, and is AVAsset-readable." Async because
    /// AVAsset duration loading is; callers needing a fast/sync check
    /// (e.g. list-row rendering) should use `fileExistsSync(for:)`
    /// instead and reserve this for verification-critical paths.
    public func verifyFileHealth(for record: PendingAssessmentRecord) async -> LocalFileHealth {
        let url = localVideoURL(for: record)
        guard FileManager.default.fileExists(atPath: url.path) else { return .missing }
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int64) ?? 0
        guard size > 0 else { return .empty }
        let duration = (try? await AVURLAsset(url: url).load(.duration).seconds) ?? 0
        guard duration.isFinite, duration > 0 else { return .unreadable }
        return .verified(sizeBytes: size)
    }

    /// Fast, synchronous existence check for UI rendering — never the
    /// sole basis for a "saved"/"synced" claim, only for immediate
    /// truthful UI wording (spec section 6: never say "Saved locally"
    /// when the file is absent).
    public func fileExistsSync(for record: PendingAssessmentRecord) -> Bool {
        FileManager.default.fileExists(atPath: localVideoURL(for: record).path)
    }

    public func upsert(_ record: PendingAssessmentRecord) {
        if let index = records.firstIndex(where: { $0.id == record.id }) {
            records[index] = record
        } else {
            records.append(record)
        }
        persist()
    }

    public func record(id: String) -> PendingAssessmentRecord? {
        records.first { $0.id == id }
    }

    /// Every recording that hasn't successfully reached the backend yet
    /// AND is still actionable (excludes FAILED_PERMANENT, which will
    /// never resolve on its own) — what auto-retry on launch/foreground/
    /// network-restored iterates.
    public var pendingUploads: [PendingAssessmentRecord] {
        records.filter { $0.isRetryable }
    }

    /// What a "Pending Upload(s)" banner should count — spec: "must
    /// count only genuinely pending/retryable/recovery-required items,
    /// not synced test fixtures [or permanently-failed entries]." A
    /// FAILED_PERMANENT entry is still visible in the full Pending
    /// Assessments list (for transparency) but contributes nothing more
    /// will automatically happen to it, so it's excluded from this count.
    public var actionablePendingCount: Int {
        records.filter { $0.uploadState == .recording || $0.isRetryable || $0.uploadState == .uploading }.count
    }

    /// Spec Phase 8: local files safe to delete — uploaded AND
    /// server-confirmed AND past the safety window. Callers decide
    /// whether/when to actually act on this list; this store never
    /// deletes anything on its own.
    public func cleanupEligibleRecordIds(safetyWindow: TimeInterval) -> [String] {
        let cutoff = Date().addingTimeInterval(-safetyWindow)
        return records
            .filter { $0.isEligibleForCleanup && ($0.stopTime ?? $0.startTime) < cutoff }
            .map(\.id)
    }

    /// Deletes ONLY the local video file for a record already verified
    /// eligible (see cleanupEligibleRecordIds) — the queue entry itself
    /// is kept (audit trail), just its local file is freed.
    public func deleteLocalFile(recordId: String) {
        guard let record = self.record(id: recordId), record.isEligibleForCleanup else { return }
        try? FileManager.default.removeItem(at: localVideoURL(for: record))
    }

    // MARK: - Persistence

    /// Decodes each array element INDEPENDENTLY (via JSONSerialization
    /// first, then per-element JSONDecoder) rather than decoding the
    /// whole array in one shot — spec: "Do not silently discard the
    /// entire queue when one record is malformed. Preserve readable
    /// records and quarantine/report malformed records." One bad
    /// element no longer costs every other record in the file.
    private func load() {
        guard let data = try? Data(contentsOf: indexFileURL) else {
            // No file yet is normal (first launch) — not an error.
            return
        }

        guard let rawArray = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            // The file exists but isn't even valid JSON / not an array
            // of objects — try the backup before giving up.
            lastPersistenceError = "Could not parse the local recordings index — attempting backup recovery."
            loadFromBackupOrQuarantine(originalData: data)
            return
        }

        var recovered: [PendingAssessmentRecord] = []
        var quarantined: [[String: Any]] = []
        for element in rawArray {
            guard let elementData = try? JSONSerialization.data(withJSONObject: element),
                  let record = try? JSONDecoder.kemet.decode(PendingAssessmentRecord.self, from: elementData)
            else {
                quarantined.append(element)
                continue
            }
            recovered.append(record)
        }

        records = recovered
        quarantinedRecordCount = quarantined.count
        if !quarantined.isEmpty {
            lastPersistenceError = "\(quarantined.count) recording record(s) could not be read and were quarantined — see the quarantine file."
            if let quarantineData = try? JSONSerialization.data(withJSONObject: quarantined, options: [.prettyPrinted]) {
                try? quarantineData.write(to: quarantineFileURL, options: .atomic)
            }
        } else {
            lastPersistenceError = nil
        }
    }

    private func loadFromBackupOrQuarantine(originalData: Data) {
        if let backupData = try? Data(contentsOf: backupFileURL),
           let backupRecords = try? JSONDecoder.kemet.decode([PendingAssessmentRecord].self, from: backupData) {
            records = backupRecords
            lastPersistenceError = "The local recordings index was corrupt; recovered from the last known-good backup."
            return
        }
        // Neither the main file nor the backup could be read — quarantine
        // the unreadable original rather than deleting it, and start
        // with an empty in-memory queue (never crash, never silently
        // claim nothing was lost).
        try? originalData.write(to: quarantineFileURL, options: .atomic)
        records = []
        lastPersistenceError = "The local recordings index was unreadable and could not be recovered from a backup. The raw file was preserved for inspection, not deleted."
    }

    /// Atomic write, with the PREVIOUS valid index preserved as a backup
    /// BEFORE being replaced (spec: "Keep a recoverable backup of the
    /// previous valid index before replacement") — and every
    /// encode/write failure is captured into `lastPersistenceError`
    /// rather than silently swallowed via `try?`.
    private func persist() {
        do {
            let data = try JSONEncoder.kemet.encode(records)
            if FileManager.default.fileExists(atPath: indexFileURL.path) {
                // Best-effort backup of the outgoing version — a backup
                // write failure must never block the real, more
                // important write below.
                try? FileManager.default.removeItem(at: backupFileURL)
                try? FileManager.default.copyItem(at: indexFileURL, to: backupFileURL)
            }
            try data.write(to: indexFileURL, options: .atomic)
            lastPersistenceError = nil
        } catch {
            lastPersistenceError = "Failed to save the local recordings index: \(error.localizedDescription). In-memory state may not survive a restart until this is resolved."
        }
    }
}
