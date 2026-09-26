//
//  TelemetryQueueStore.swift
//  KemetFCTracker
//
//  The durable, local-first queue for tracking telemetry (samples + events).
//
//      sample/event  ->  appended to disk  ->  uploaded  ->  backend proves it
//      durable (explicit acknowledgment)  ->  only then deleted locally
//
//  Nothing held in RAM counts as queued: appendSample/appendEvent return only
//  after the record has been handed to the operating system (write(2)), so it
//  survives the process being killed. An fsync of the open segment runs at
//  most once a second on a background queue, and on flushToDisk() — sudden
//  power loss can therefore lose at most ~1 s of the newest samples.
//
//  ON-DISK FORMAT (one directory per tracking session, named by session id
//  only — no player names or other PII in any path):
//
//      <root>/<sessionId>/samples-000000001.jsonl   append-only, one wire-format
//                                                    TrackingSample per line; the file
//                                                    name is the first sequence in it;
//                                                    rotated every 1000 samples
//      <root>/<sessionId>/events.jsonl              one QueuedTelemetryEvent per line
//      <root>/<sessionId>/state.json                {"ackCursor": n, "finished": bool}
//
//  * sequence_number is assigned once, written with the sample, and continues
//    from max(highest sequence on disk, ack cursor) after any restart — so it
//    never repeats even after everything was acknowledged and deleted.
//  * A torn final line (process died mid-write) is truncated on first access
//    and can never be confused with a record; that record was never reported
//    as queued.
//  * The ack cursor advances only over the CONTIGUOUS run of sequences the
//    backend explicitly acknowledged. Whole segments below the cursor are
//    deleted; the newest segment is never deleted while the session is open.
//  * Unacknowledged telemetry is NEVER deleted for age or size. Crossing
//    warningThresholdBytes only logs, calls onStorageWarning and flips
//    isOverWarningThreshold — data keeps being preserved while storage lasts.
//  * A finished session's directory is removed only once every sample and
//    event is acknowledged.
//
//  Single writer per directory: production uses `TelemetryQueueStore.shared`;
//  all operations are serialized by an internal lock. Files live in
//  Application Support, are excluded from iCloud backup, and (iOS) use
//  file protection that still permits background upload after first unlock.
//

import Foundation
import os.log

public struct QueuedTelemetryEvent: Codable, Equatable, Sendable {
    /// UUID string generated ONCE at enqueue; the backend's idempotency key for events.
    public var clientEventId: String
    public var eventType: String
    public var details: [String: TelemetryEventValue]
    /// Device time the event happened. The backend keeps its own receipt time separately.
    public var clientOccurredAt: Date
}

public final class TelemetryQueueStore: @unchecked Sendable {
    public static let defaultWarningThresholdBytes: Int64 = 150 * 1024 * 1024

    public static var defaultRootDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("KemetFC", isDirectory: true)
            .appendingPathComponent("telemetry", isDirectory: true)
    }

    public static let shared = TelemetryQueueStore(rootDirectory: TelemetryQueueStore.defaultRootDirectory)

    /// Called (outside the store's lock) when a session's queue first crosses
    /// warningThresholdBytes, and again after each further ~10 MB of growth.
    /// Observability only — it never causes data to be dropped.
    public var onStorageWarning: ((_ sessionId: String, _ bytes: Int64) -> Void)? {
        get { lock.withLock { warningHandler } }
        set { lock.withLock { warningHandler = newValue } }
    }

    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TelemetryQueueStore")
    private let rootDirectory: URL
    private let warningThresholdBytes: Int64
    private let lock = NSLock()
    private var warningHandler: ((String, Int64) -> Void)?
    private var sessions: [String: SessionState] = [:]

    private let flightLock = NSLock()
    private var flights: [String: Task<Void, Never>] = [:]

    private let fsyncQueue = DispatchQueue(label: "com.kemetfc.telemetry.fsync", qos: .utility)
    private let fsyncInterval: TimeInterval = 1.0
    private let maxSamplesPerSegment = 1000
    private let warningRepeatBytes: Int64 = 10 * 1024 * 1024

    private final class SessionState {
        var nextSequence = 1
        var ackCursor = 0
        var finished = false
        var handle: FileHandle?
        var currentSegmentSamples = 0
        var bytes: Int64 = 0
        var lastWarnedBytes: Int64 = 0
        var lastSync = Date.distantPast
    }

    private struct PersistedState: Codable {
        var ackCursor: Int
        var finished: Bool
    }

    public init(rootDirectory: URL, warningThresholdBytes: Int64 = TelemetryQueueStore.defaultWarningThresholdBytes) {
        self.rootDirectory = rootDirectory
        self.warningThresholdBytes = warningThresholdBytes
    }

    // MARK: - Enqueue

    /// Persists the sample, then returns it with its sequence number. The
    /// sequence is assigned here, exactly once.
    @discardableResult
    public func appendSample(_ sample: TrackingSample, sessionId: String) throws -> TrackingSample {
        var warning: (String, Int64)?
        var handler: ((String, Int64) -> Void)?
        let stored: TrackingSample = try lock.withLock {
            let state = try loadState(sessionId)
            var record = sample
            record.sequenceNumber = state.nextSequence
            var line = try JSONEncoder().encode(record)
            line.append(0x0A)

            let handle = try segmentHandle(for: state, sessionId: sessionId)
            try handle.write(contentsOf: line)

            state.nextSequence += 1
            state.currentSegmentSamples += 1
            state.bytes += Int64(line.count)
            scheduleFsync(state, handle: handle)
            warning = warningIfNeeded(state, sessionId: sessionId)
            handler = warningHandler
            return record
        }
        if let warning { handler?(warning.0, warning.1) }
        return stored
    }

    /// Persists an event with a stable client_event_id (generated here, once).
    @discardableResult
    public func appendEvent(
        type: String, details: [String: TelemetryEventValue], occurredAt: Date, sessionId: String
    ) throws -> QueuedTelemetryEvent {
        try lock.withLock {
            let state = try loadState(sessionId)
            let event = QueuedTelemetryEvent(
                clientEventId: UUID().uuidString, eventType: type, details: details, clientOccurredAt: occurredAt
            )
            var line = try JSONEncoder.kemet.encode(event)
            line.append(0x0A)
            let url = eventsFileURL(sessionId)
            if !FileManager.default.fileExists(atPath: url.path) { createFile(at: url) }
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: line)
            try? handle.synchronize()   // events are rare and small: make each one durable immediately
            state.bytes += Int64(line.count)
            return event
        }
    }

    // MARK: - Read

    /// Unacknowledged samples, ascending by sequence number.
    public func pendingSamples(sessionId: String, limit: Int) throws -> [TrackingSample] {
        try lock.withLock {
            guard directoryExists(sessionId) else { return [] }
            let state = try loadState(sessionId)
            let segments = sampleSegmentURLsUnlocked(sessionId)
            var result: [TrackingSample] = []
            for (index, url) in segments.enumerated() {
                if index + 1 < segments.count, firstSequence(of: segments[index + 1]) <= state.ackCursor + 1 { continue }
                for sample in decodeSamples(in: url) where (sample.sequenceNumber ?? 0) > state.ackCursor {
                    result.append(sample)
                    if result.count >= limit { return result }
                }
            }
            return result
        }
    }

    public func pendingEvents(sessionId: String) throws -> [QueuedTelemetryEvent] {
        try lock.withLock {
            guard directoryExists(sessionId) else { return [] }
            _ = try loadState(sessionId)
            return readEvents(sessionId)
        }
    }

    /// Number of samples not yet acknowledged — O(1).
    public func pendingSampleCount(sessionId: String) -> Int {
        lock.withLock {
            guard directoryExists(sessionId), let state = try? loadState(sessionId) else { return 0 }
            return max(state.nextSequence - 1 - state.ackCursor, 0)
        }
    }

    // MARK: - Acknowledge (the only way data leaves the queue)

    /// Deletes ONLY what the backend positively proved durable: the cursor
    /// moves over the contiguous run of acknowledged sequences that starts at
    /// cursor + 1. An acknowledged sequence beyond a gap stays pending (it is
    /// re-sent later, which the backend treats as a harmless duplicate).
    /// Sequences that were never assigned are ignored. Never moves backwards.
    public func acknowledgeSamples(sessionId: String, sequences: [Int]) throws {
        try lock.withLock {
            guard directoryExists(sessionId) else { return }
            let state = try loadState(sessionId)
            let acknowledged = Set(sequences)
            var cursor = state.ackCursor
            while cursor + 1 < state.nextSequence, acknowledged.contains(cursor + 1) { cursor += 1 }
            guard cursor > state.ackCursor else { return }

            state.ackCursor = cursor
            try persist(state, sessionId: sessionId)
            deleteFullyAcknowledgedSegments(state, sessionId: sessionId)
            try removeSessionIfFinishedAndDrained(sessionId, state: state)
        }
    }

    public func acknowledgeEvent(sessionId: String, clientEventId: String) throws {
        try lock.withLock {
            guard directoryExists(sessionId) else { return }
            let state = try loadState(sessionId)
            let remaining = readEvents(sessionId).filter { $0.clientEventId != clientEventId }
            var data = Data()
            for event in remaining {
                data.append(try JSONEncoder.kemet.encode(event))
                data.append(0x0A)
            }
            try data.write(to: eventsFileURL(sessionId), options: .atomic)
            try removeSessionIfFinishedAndDrained(sessionId, state: state)
        }
    }

    // MARK: - Session lifecycle

    /// The session ended. Its directory is removed once everything is acknowledged —
    /// immediately if that is already true, otherwise as soon as it becomes true.
    public func finishSession(_ sessionId: String) throws {
        try lock.withLock {
            guard directoryExists(sessionId) else { return }
            let state = try loadState(sessionId)
            state.finished = true
            try persist(state, sessionId: sessionId)
            try removeSessionIfFinishedAndDrained(sessionId, state: state)
        }
    }

    /// Sessions that still hold unacknowledged samples or events.
    public func sessionsWithPendingTelemetry() -> [String] {
        lock.withLock {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: rootDirectory.path)) ?? []
            return names.sorted().filter { name in
                guard let state = try? loadState(name) else { return false }
                return state.ackCursor < state.nextSequence - 1 || !readEvents(name).isEmpty
            }
        }
    }

    /// Pending sessions whose files have not been touched for `interval`.
    /// For reporting only — such data is kept, never deleted.
    public func abandonedSessions(olderThan interval: TimeInterval, now: Date = Date()) -> [String] {
        sessionsWithPendingTelemetry().filter { sessionId in
            let urls = (try? FileManager.default.contentsOfDirectory(
                at: sessionDirectory(for: sessionId), includingPropertiesForKeys: [.contentModificationDateKey]
            )) ?? []
            let newest = urls.compactMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate }.max()
            return newest.map { now.timeIntervalSince($0) >= interval } ?? false
        }
    }

    public func sessionDirectory(for sessionId: String) -> URL {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let safeName = sessionId.addingPercentEncoding(withAllowedCharacters: allowed) ?? "invalid-session"
        return rootDirectory.appendingPathComponent(safeName, isDirectory: true)
    }

    public func sampleSegmentURLs(sessionId: String) -> [URL] {
        lock.withLock { sampleSegmentURLsUnlocked(sessionId) }
    }

    public func isOverWarningThreshold(sessionId: String) -> Bool {
        lock.withLock {
            guard directoryExists(sessionId), let state = try? loadState(sessionId) else { return false }
            return state.bytes > warningThresholdBytes
        }
    }

    /// fsync every open segment now (call when the app is about to be suspended).
    public func flushToDisk() {
        lock.withLock {
            for state in sessions.values { try? state.handle?.synchronize() }
        }
    }

    // MARK: - Single flight (shared by every uploader of a session)

    /// Runs `body` as THE flight for this session: a caller arriving while
    /// one is running awaits it instead of starting a second one.
    public func runFlight(sessionId: String, _ body: @escaping @Sendable () async -> Void) async {
        let task: Task<Void, Never> = flightLock.withLock {
            if let running = flights[sessionId] { return running }
            // Created while holding flightLock: the cleanup below cannot run
            // (it needs the lock) before the task has been registered.
            let created = Task { [weak self] in
                await body()
                self?.flightLock.withLock { self?.flights[sessionId] = nil }
            }
            flights[sessionId] = created
            return created
        }
        await task.value
    }

    // MARK: - Internals (lock held)

    private func directoryExists(_ sessionId: String) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: sessionDirectory(for: sessionId).path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    private func stateFileURL(_ sessionId: String) -> URL { sessionDirectory(for: sessionId).appendingPathComponent("state.json") }
    private func eventsFileURL(_ sessionId: String) -> URL { sessionDirectory(for: sessionId).appendingPathComponent("events.jsonl") }

    private func segmentURL(_ sessionId: String, firstSequence: Int) -> URL {
        sessionDirectory(for: sessionId).appendingPathComponent(String(format: "samples-%09d.jsonl", firstSequence))
    }

    private func firstSequence(of url: URL) -> Int {
        Int(url.deletingPathExtension().lastPathComponent.dropFirst("samples-".count)) ?? Int.max
    }

    private func sampleSegmentURLsUnlocked(_ sessionId: String) -> [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(at: sessionDirectory(for: sessionId), includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.lastPathComponent.hasPrefix("samples-") && $0.pathExtension == "jsonl" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Loads (once per store instance) the session's state from disk, creating the
    /// directory on first use and repairing a torn final line.
    private func loadState(_ sessionId: String) throws -> SessionState {
        if let existing = sessions[sessionId] { return existing }

        let directory = sessionDirectory(for: sessionId)
        try createDirectory(directory)

        let state = SessionState()
        if let data = try? Data(contentsOf: stateFileURL(sessionId)),
           let persisted = try? JSONDecoder().decode(PersistedState.self, from: data) {
            state.ackCursor = persisted.ackCursor
            state.finished = persisted.finished
        }

        var highestOnDisk = state.ackCursor
        let segments = sampleSegmentURLsUnlocked(sessionId)
        if let last = segments.last {
            try repairTornTail(of: last)
            let samples = decodeSamples(in: last)
            state.currentSegmentSamples = samples.count
            highestOnDisk = max(highestOnDisk, samples.compactMap(\.sequenceNumber).max() ?? 0, firstSequence(of: last) - 1)
        }
        if FileManager.default.fileExists(atPath: eventsFileURL(sessionId).path) {
            try repairTornTail(of: eventsFileURL(sessionId))
        }
        state.nextSequence = highestOnDisk + 1

        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        state.bytes = files.reduce(0) { $0 + Int64((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }

        sessions[sessionId] = state
        return state
    }

    /// A line without its terminating newline was never reported as queued
    /// (the process died mid-write): cut it off so it cannot merge with the next append.
    private func repairTornTail(of url: URL) throws {
        let data = try Data(contentsOf: url)
        guard let last = data.last, last != 0x0A else { return }
        let keep = (data.lastIndex(of: 0x0A) ?? -1) + 1
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.truncate(atOffset: UInt64(keep))
        log.notice("Discarded a torn telemetry line (\(data.count - keep) bytes) in \(url.lastPathComponent, privacy: .public)")
    }

    private func decodeSamples(in url: URL) -> [TrackingSample] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        var lines = data.split(separator: 0x0A, omittingEmptySubsequences: true)
        if data.last != 0x0A, !lines.isEmpty { lines.removeLast() }   // unterminated = torn
        let decoder = JSONDecoder()
        var corrupt = 0
        let samples = lines.compactMap { line -> TrackingSample? in
            if let sample = try? decoder.decode(TrackingSample.self, from: Data(line)) { return sample }
            corrupt += 1
            return nil
        }
        if corrupt > 0 { log.error("Skipped \(corrupt) unreadable telemetry line(s) in \(url.lastPathComponent, privacy: .public)") }
        return samples
    }

    private func readEvents(_ sessionId: String) -> [QueuedTelemetryEvent] {
        guard let data = try? Data(contentsOf: eventsFileURL(sessionId)) else { return [] }
        var lines = data.split(separator: 0x0A, omittingEmptySubsequences: true)
        if data.last != 0x0A, !lines.isEmpty { lines.removeLast() }
        return lines.compactMap { try? JSONDecoder.kemet.decode(QueuedTelemetryEvent.self, from: Data($0)) }
    }

    /// The open handle for the newest segment, rotating to a new segment file when it is full.
    private func segmentHandle(for state: SessionState, sessionId: String) throws -> FileHandle {
        if let handle = state.handle, state.currentSegmentSamples < maxSamplesPerSegment { return handle }

        if let full = state.handle {
            try? full.synchronize()
            try? full.close()
            state.handle = nil
        }
        let segments = sampleSegmentURLsUnlocked(sessionId)
        let url: URL
        if let last = segments.last, state.currentSegmentSamples < maxSamplesPerSegment {
            url = last
        } else {
            url = segmentURL(sessionId, firstSequence: state.nextSequence)
            createFile(at: url)
            state.currentSegmentSamples = 0
        }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        state.handle = handle
        return handle
    }

    private func scheduleFsync(_ state: SessionState, handle: FileHandle) {
        let now = Date()
        guard now.timeIntervalSince(state.lastSync) >= fsyncInterval else { return }
        state.lastSync = now
        fsyncQueue.async { try? handle.synchronize() }
    }

    private func warningIfNeeded(_ state: SessionState, sessionId: String) -> (String, Int64)? {
        guard state.bytes > warningThresholdBytes else { return nil }
        guard state.lastWarnedBytes == 0 || state.bytes - state.lastWarnedBytes >= warningRepeatBytes else { return nil }
        state.lastWarnedBytes = state.bytes
        log.error("Telemetry queue for \(sessionId, privacy: .public) holds \(state.bytes) bytes of unacknowledged data (warning threshold \(self.warningThresholdBytes)); data is being preserved")
        return (sessionId, state.bytes)
    }

    private func persist(_ state: SessionState, sessionId: String) throws {
        let data = try JSONEncoder().encode(PersistedState(ackCursor: state.ackCursor, finished: state.finished))
        try data.write(to: stateFileURL(sessionId), options: .atomic)
        try? state.handle?.synchronize()
    }

    private func deleteFullyAcknowledgedSegments(_ state: SessionState, sessionId: String) {
        let segments = sampleSegmentURLsUnlocked(sessionId)
        for (index, url) in segments.enumerated() where index + 1 < segments.count {   // never the newest
            guard firstSequence(of: segments[index + 1]) <= state.ackCursor + 1 else { continue }
            let size = Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
            if (try? FileManager.default.removeItem(at: url)) != nil { state.bytes -= size }
        }
    }

    private func removeSessionIfFinishedAndDrained(_ sessionId: String, state: SessionState) throws {
        guard state.finished, state.ackCursor >= state.nextSequence - 1, readEvents(sessionId).isEmpty else { return }
        try? state.handle?.close()
        state.handle = nil
        sessions[sessionId] = nil
        try FileManager.default.removeItem(at: sessionDirectory(for: sessionId))
        log.info("Telemetry for finished session \(sessionId, privacy: .public) fully acknowledged; local copy removed")
    }

    private func createDirectory(_ directory: URL) throws {
        let fileManager = FileManager.default
        if !fileManager.fileExists(atPath: rootDirectory.path) {
            try fileManager.createDirectory(at: rootDirectory, withIntermediateDirectories: true)
            excludeFromBackup(rootDirectory)
        }
        if !fileManager.fileExists(atPath: directory.path) {
            #if os(iOS)
            try fileManager.createDirectory(
                at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
            )
            #else
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
            #endif
            excludeFromBackup(directory)
        }
    }

    private func createFile(at url: URL) {
        #if os(iOS)
        FileManager.default.createFile(
            atPath: url.path, contents: nil,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
        )
        #else
        FileManager.default.createFile(atPath: url.path, contents: nil)
        #endif
    }

    /// Child tracking data must never be swept into an iCloud backup.
    private func excludeFromBackup(_ url: URL) {
        var target = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? target.setResourceValues(values)
    }
}
