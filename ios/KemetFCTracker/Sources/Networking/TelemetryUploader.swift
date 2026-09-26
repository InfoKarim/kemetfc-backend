//
//  TelemetryUploader.swift
//  KemetFCTracker
//
//  STATUS: unit-tested on macOS against a stub server (see Tests/
//  TelemetryUploaderTests.swift); not run on a device.
//
//  Local-first telemetry. recordSample/recordEvent hand each record to
//  TelemetryQueueStore, which persists it (and assigns its stable sequence
//  number / client_event_id) BEFORE anything is uploaded; upload is a
//  separate, retryable step:
//
//      recordSample -> durable queue -> flushPending -> POST -> explicit ack -> delete
//
//  * Single flight: however many callers ask, only one upload pass per
//    session is ever in flight (TelemetryQueueStore.runFlight); the others
//    join it. That also removes the old reordering when two flushes failed.
//  * Deletion rule: a sample leaves the queue only if the response's
//    `acknowledged_sequences` positively lists it AND this uploader sent it.
//    Any other outcome — 5xx, offline, 409 (identity mismatch: the server
//    committed nothing), a 2xx without that list, undecodable body — keeps
//    every sample. An event is deleted only when the response echoes its own
//    client_event_id (201 = stored, 200 = identical duplicate).
//  * Retries re-send the very same records with the very same identities.
//  * stop() is deterministic: it cancels the timer and marks the session
//    finished (its local copy is removed once everything is acknowledged).
//    Nothing is lost by stopping offline — the tail is already on disk and
//    is replayed by TelemetryReplayService. stopAndFlush() adds one final
//    upload pass.
//
//  Logging carries counts and the session id only — never coordinates,
//  poses or player identity.
//

import Foundation
import os.log

public final class TelemetryUploader: @unchecked Sendable {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TelemetryUploader")
    private let apiClient: KemetAPIClient
    private let sessionId: String
    private let store: TelemetryQueueStore
    private let lock = NSLock()

    /// Matches api_schemas.IngestTrackingSamplesSchema(max_length=500); well under it.
    private let maxBatchSize = 120
    private let flushInterval: TimeInterval = 2.0

    private var flushTask: Task<Void, Never>?
    private var recordedSinceLastKick = 0
    /// Upload passes in a row that ended without positive acknowledgment (offline, server error, 409, or a
    /// server that predates acknowledged_sequences). Drives the timer's back-off; reset by any acknowledgment.
    private var consecutiveUnacknowledgedPasses = 0
    private let maxBackoffDoublings = 6
    private var enqueueFailures = 0

    /// Seconds the periodic timer waits before its next pass: 2 s normally, doubling (up to ~2 min) while
    /// passes keep failing to get acknowledged. This stops a server that never acknowledges (an old backend
    /// without idempotent ingest) or a permanent conflict from being hammered every 2 s. Explicit
    /// flushPending() calls (foreground, network restored, stop) are never delayed by it.
    public var currentFlushIntervalSeconds: TimeInterval {
        lock.withLock { flushInterval * pow(2.0, Double(min(consecutiveUnacknowledgedPasses, maxBackoffDoublings))) }
    }

    /// Samples/events the durable queue could not accept (e.g. disk full).
    /// Never silent: each one is logged, and this counter lets the app say so.
    public var enqueueFailureCount: Int { lock.withLock { enqueueFailures } }

    public init(apiClient: KemetAPIClient, sessionId: String, store: TelemetryQueueStore = .shared) {
        self.apiClient = apiClient
        self.sessionId = sessionId
        self.store = store
        let restored = store.pendingSampleCount(sessionId: sessionId)
        if restored > 0 {
            log.notice("Restored \(restored) pending telemetry sample(s) for session \(sessionId, privacy: .public)")
        }
    }

    public func start() {
        lock.withLock { flushTask?.cancel() }
        let task = Task { [weak self] in
            while !Task.isCancelled {
                guard let interval = self?.currentFlushIntervalSeconds else { break }
                do { try await Task.sleep(for: .seconds(interval)) } catch { break }   // a cancelled sleep ends the loop
                guard let self else { break }
                await self.flushPending()
            }
        }
        lock.withLock { flushTask = task }
    }

    /// Cancels the timer and marks the session finished. Does no network work.
    public func stop() {
        lock.withLock {
            flushTask?.cancel()
            flushTask = nil
        }
        do { try store.finishSession(sessionId) } catch {
            log.error("finishSession failed for \(self.sessionId, privacy: .public): \(error.localizedDescription)")
        }
    }

    /// stop() plus one final best-effort upload pass.
    public func stopAndFlush() async {
        stop()
        await flushPending()
    }

    // MARK: - Enqueue (durable before returning)

    public func recordSample(
        tSeconds: Double,
        playerBox: NormalizedRect?,
        playerConfidence: Double?,
        playerTrackId: Int?,
        ballBox: NormalizedRect?,
        ballConfidence: Double?,
        ballTrackId: Int? = nil,
        poseKeypoints: [PoseKeypoint],
        gimbalState: String,
        trackingMode: String,
        trackingStatus: String,
        identityAmbiguous: Bool? = nil
    ) {
        let sample = TrackingSample(
            tSeconds: tSeconds,
            playerBbox: playerBox.map { [$0.x, $0.y, $0.width, $0.height] },
            playerCenter: playerBox.map { [$0.center.x, $0.center.y] },
            playerConfidence: playerConfidence,
            playerTrackId: playerTrackId,
            ballBbox: ballBox.map { [$0.x, $0.y, $0.width, $0.height] },
            ballCenter: ballBox.map { [$0.center.x, $0.center.y] },
            ballConfidence: ballConfidence,
            ballTrackId: ballTrackId,   // the tracker's real identity; nil = unknown (never a placeholder)
            poseKeypoints: poseKeypoints.isEmpty ? nil : poseKeypoints.map {
                PoseKeypointWire(name: $0.name, x: $0.x, y: $0.y, confidence: $0.confidence)
            },
            gimbalState: gimbalState,
            trackingMode: trackingMode,
            trackingStatus: trackingStatus,
            identityAmbiguous: identityAmbiguous
        )
        do {
            try store.appendSample(sample, sessionId: sessionId)
        } catch {
            noteEnqueueFailure("sample", error)
            return
        }
        let shouldKick = lock.withLock { () -> Bool in
            recordedSinceLastKick += 1
            guard recordedSinceLastKick >= maxBatchSize else { return false }
            recordedSinceLastKick = 0
            return true
        }
        if shouldKick { Task { await flushPending() } }
    }

    public func recordEvent(type: String, details: [String: TelemetryEventValue]) {
        do {
            try store.appendEvent(type: type, details: details, occurredAt: Date(), sessionId: sessionId)
        } catch {
            noteEnqueueFailure("event", error)
        }
    }

    /// Returns once every record* call made before it is on disk. Writes are
    /// synchronous today, so there is nothing to wait for; the seam exists so a
    /// future serialized background writer can keep the same guarantee.
    public func waitForPendingWrites() async {}

    private func noteEnqueueFailure(_ what: String, _ error: Error) {
        lock.withLock { enqueueFailures += 1 }
        log.error("Could not durably queue a telemetry \(what, privacy: .public) for \(self.sessionId, privacy: .public): \(error.localizedDescription)")
    }

    // MARK: - Upload (single flight)

    /// One upload pass over everything pending. Concurrent callers join the flight in progress.
    public func flushPending() async {
        await store.runFlight(sessionId: sessionId) { [self] in await self.uploadPass() }
    }

    private struct SamplesBody: Encodable { let samples: [TrackingSample] }

    private struct SamplesResponse: Decodable {
        let acknowledgedSequences: [Int]?
        let duplicates: Int?
        enum CodingKeys: String, CodingKey {
            case acknowledgedSequences = "acknowledged_sequences"
            case duplicates
        }
    }

    private struct EventBody: Encodable {
        let event_type: String
        let details: [String: TelemetryEventValue]
        let client_event_id: String
        let client_occurred_at: Date
    }

    private struct EventResponse: Decodable {
        let clientEventId: String?
        enum CodingKeys: String, CodingKey { case clientEventId = "client_event_id" }
    }

    private func uploadPass() async {
        let samplesOk = await uploadSamples()
        await uploadEvents()
        lock.withLock { consecutiveUnacknowledgedPasses = samplesOk ? 0 : consecutiveUnacknowledgedPasses + 1 }
        let depth = store.pendingSampleCount(sessionId: sessionId)
        if depth > 0 { log.info("Telemetry queue depth for \(self.sessionId, privacy: .public): \(depth) sample(s) pending") }
    }

    /// - Returns: false if a batch could not be acknowledged (the pass failed).
    private func uploadSamples() async -> Bool {
        while true {
            let batch: [TrackingSample]
            do { batch = try store.pendingSamples(sessionId: sessionId, limit: maxBatchSize) } catch {
                log.error("Could not read queued telemetry for \(self.sessionId, privacy: .public): \(error.localizedDescription)")
                return false
            }
            guard !batch.isEmpty else { return true }

            do {
                let response = try await apiClient.post(
                    path: "/tracking/sessions/\(sessionId)/samples",
                    body: SamplesBody(samples: batch),
                    as: SamplesResponse.self
                )
                // Only what this uploader sent AND the server lists can be deleted.
                let sent = Set(batch.compactMap(\.sequenceNumber))
                let acknowledged = (response.acknowledgedSequences ?? []).filter { sent.contains($0) }
                guard !acknowledged.isEmpty else {
                    log.error("Upload of \(batch.count) sample(s) for \(self.sessionId, privacy: .public) returned no usable acknowledgment (is the backend up to date?); keeping them")
                    return false
                }
                let before = store.pendingSampleCount(sessionId: sessionId)
                try store.acknowledgeSamples(sessionId: sessionId, sequences: acknowledged)
                let removed = before - store.pendingSampleCount(sessionId: sessionId)
                log.info("Acknowledged \(removed) telemetry sample(s) for \(self.sessionId, privacy: .public) (\(response.duplicates ?? 0) duplicate(s) already stored)")
                if removed <= 0 { return false }
            } catch {
                log.error("Telemetry upload failed for \(self.sessionId, privacy: .public), \(batch.count) sample(s) stay queued: \(error.localizedDescription)")
                return false
            }
        }
    }

    private func uploadEvents() async {
        let events: [QueuedTelemetryEvent]
        do { events = try store.pendingEvents(sessionId: sessionId) } catch { return }
        for event in events {
            do {
                let response = try await apiClient.post(
                    path: "/tracking/sessions/\(sessionId)/events",
                    body: EventBody(
                        event_type: event.eventType, details: event.details,
                        client_event_id: event.clientEventId, client_occurred_at: event.clientOccurredAt
                    ),
                    as: EventResponse.self
                )
                guard response.clientEventId == event.clientEventId else {
                    log.error("Event upload for \(self.sessionId, privacy: .public) was not acknowledged with its client_event_id; keeping it")
                    continue
                }
                try store.acknowledgeEvent(sessionId: sessionId, clientEventId: event.clientEventId)
            } catch let error as KemetAPIError {
                if case .transport = error { return }   // offline: try again next pass
                log.error("Event upload rejected for \(self.sessionId, privacy: .public): \(String(describing: error), privacy: .public); keeping it")
            } catch {
                log.error("Event upload failed for \(self.sessionId, privacy: .public): \(error.localizedDescription)")
            }
        }
    }
}

/// A closed, genuinely-Sendable value type for event details. Codable so the
/// durable queue can persist events and re-send them unchanged on retry.
public enum TelemetryEventValue: Sendable, Codable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) { self = .bool(value); return }
        if let value = try? container.decode(Int.self) { self = .int(value); return }
        if let value = try? container.decode(Double.self) { self = .double(value); return }
        if let value = try? container.decode(String.self) { self = .string(value); return }
        throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported telemetry event value")
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        }
    }
}

extension TelemetryEventValue: ExpressibleByStringLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
}

extension TelemetryEventValue: ExpressibleByIntegerLiteral {
    public init(integerLiteral value: Int) { self = .int(value) }
}
