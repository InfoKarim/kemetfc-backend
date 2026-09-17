//
//  TelemetryUploader.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Batches TrackingSample rows and uploads them to
//  POST /tracking/sessions/{id}/samples in bounded batches — never one
//  HTTP request per frame (spec section 19), and the request body shape
//  here matches IngestTrackingSamplesSchema (app/api_schemas.py) exactly,
//  including its `max_length=500` samples-per-request cap.
//

import Foundation
import os.log

/// `@unchecked Sendable`: a real Xcode build (Swift 6 strict
/// concurrency — never checkable via `swiftc -typecheck` alone, since
/// this class is called across actor boundaries by TrackingCoordinator)
/// flagged `start()`/`recordSample(...)`'s `Task { ... }` closures as
/// unsafe. That flag was pointing at something REAL: `pendingSamples`
/// had NO synchronization at all before this fix, despite being mutated
/// from `recordSample`/`recordEvent` (called synchronously from
/// TrackingCoordinator, @MainActor) AND from `flush()`'s own
/// periodic-timer Task — a genuine, pre-existing potential data race,
/// not a false positive. Fixed with an explicit `NSLock` guarding every
/// access to `pendingSamples`/`flushTask`, never held across an `await`
/// (holding a lock across a suspension point is its own hazard) — the
/// `@unchecked Sendable` conformance is honest because of this lock,
/// not in spite of it.
public final class TelemetryUploader: @unchecked Sendable {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TelemetryUploader")
    private let apiClient: KemetAPIClient
    private let sessionId: String
    private let lock = NSLock()

    /// Matches api_schemas.IngestTrackingSamplesSchema(samples: ...,
    /// max_length=500) — flushing well under that cap keeps each
    /// request small regardless of how long a batch interval runs.
    private let maxBatchSize = 120
    private let flushInterval: TimeInterval = 2.0

    private var pendingSamples: [TrackingSample] = []
    private var flushTask: Task<Void, Never>?

    public init(apiClient: KemetAPIClient, sessionId: String) {
        self.apiClient = apiClient
        self.sessionId = sessionId
    }

    public func start() {
        lock.withLock { flushTask?.cancel() }
        let task = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(self.flushInterval))
                await self.flush()
            }
        }
        lock.withLock { flushTask = task }
    }

    public func stop() {
        lock.withLock {
            flushTask?.cancel()
            flushTask = nil
        }
    }

    public func recordSample(
        tSeconds: Double,
        playerBox: NormalizedRect?,
        playerConfidence: Double?,
        playerTrackId: Int?,
        ballBox: NormalizedRect?,
        ballConfidence: Double?,
        poseKeypoints: [PoseKeypoint],
        gimbalState: String,
        trackingMode: String,
        trackingStatus: String
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
            ballTrackId: ballBox != nil ? 1 : nil,
            poseKeypoints: poseKeypoints.isEmpty ? nil : poseKeypoints.map {
                PoseKeypointWire(name: $0.name, x: $0.x, y: $0.y, confidence: $0.confidence)
            },
            gimbalState: gimbalState,
            trackingMode: trackingMode,
            trackingStatus: trackingStatus
        )
        let shouldFlush = lock.withLock {
            pendingSamples.append(sample)
            return pendingSamples.count >= maxBatchSize
        }
        if shouldFlush {
            Task { await flush() }
        }
    }

    public func recordEvent(type: String, details: [String: TelemetryEventValue]) {
        Task {
            do {
                struct EventBody: Encodable {
                    let event_type: String
                    let details: [String: TelemetryEventValue]
                }
                let body = EventBody(event_type: type, details: details)
                _ = try await apiClient.post(
                    path: "/tracking/sessions/\(sessionId)/events",
                    body: body,
                    as: EmptyResponse.self
                )
            } catch {
                log.error("recordEvent(\(type, privacy: .public)) failed: \(error.localizedDescription)")
            }
        }
    }

    private func flush() async {
        // A real Xcode build rejected manual lock()/unlock() pairs
        // INSIDE this async function outright ("unavailable from
        // asynchronous contexts") — Swift 6's NSLock now requires the
        // closure-based `withLock` for exactly the scoped, never-
        // held-across-`await` usage this already was; each `withLock`
        // call below still fully returns before the next `await`.
        let batch: [TrackingSample] = lock.withLock {
            guard !pendingSamples.isEmpty else { return [] }
            let batch = Array(pendingSamples.prefix(maxBatchSize))
            pendingSamples.removeFirst(batch.count)
            return batch
        }
        guard !batch.isEmpty else { return }

        struct Body: Encodable {
            let samples: [TrackingSample]
        }
        struct IngestResponse: Decodable {
            let ingested: Int
        }

        do {
            _ = try await apiClient.post(
                path: "/tracking/sessions/\(sessionId)/samples",
                body: Body(samples: batch),
                as: IngestResponse.self
            )
        } catch {
            // Never drop the assessment over a network blip — re-queue
            // for the next flush (spec section 28: "Recording
            // interruption" must never corrupt the assessment record;
            // the same principle applies to telemetry upload).
            log.error("Sample flush failed, re-queuing \(batch.count) samples: \(error.localizedDescription)")
            lock.withLock {
                pendingSamples.insert(contentsOf: batch, at: 0)
            }
        }
    }
}

private struct EmptyResponse: Decodable {}

/// Phase 3 fix: `recordEvent`'s `details` parameter used to be
/// `[String: Any]`, encoded via a type-erased `AnyEncodable(Any)`
/// wrapper — `Any` is not `Sendable`, and a real Xcode build flagged
/// `recordEvent`'s `Task { ... }` closure (capturing `details`) as a
/// data-race risk because of it. Since every actual call site only ever
/// passes a String or an Int (see TrackingCoordinator.setMode/
/// lockPlayer), this closed, genuinely-Sendable enum replaces the
/// open-ended `Any` — a real fix, not a suppressed warning, since it
/// also stops this API from silently accepting a value it couldn't
/// actually encode (the old `AnyEncodable` defaulted unknown types to
/// `null` rather than failing).
public enum TelemetryEventValue: Sendable, Encodable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

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
