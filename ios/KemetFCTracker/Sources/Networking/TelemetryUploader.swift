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

public final class TelemetryUploader {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TelemetryUploader")
    private let apiClient: KemetAPIClient
    private let sessionId: String

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
        flushTask?.cancel()
        flushTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(self.flushInterval))
                await self.flush()
            }
        }
    }

    public func stop() {
        flushTask?.cancel()
        flushTask = nil
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
        pendingSamples.append(sample)
        if pendingSamples.count >= maxBatchSize {
            Task { await flush() }
        }
    }

    public func recordEvent(type: String, details: [String: Any]) {
        Task {
            do {
                struct EventBody: Encodable {
                    let event_type: String
                    let details: [String: AnyEncodable]
                }
                let body = EventBody(event_type: type, details: details.mapValues(AnyEncodable.init))
                try await apiClient.post(
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
        guard !pendingSamples.isEmpty else { return }
        let batch = Array(pendingSamples.prefix(maxBatchSize))
        pendingSamples.removeFirst(batch.count)

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
            pendingSamples.insert(contentsOf: batch, at: 0)
        }
    }
}

private struct EmptyResponse: Decodable {}

/// A minimal type-erased Encodable wrapper so recordEvent's [String: Any]
/// details dictionary can be sent as JSON without hand-rolling every
/// possible event's own Codable struct.
public struct AnyEncodable: Encodable {
    private let value: Any
    public init(_ value: Any) { self.value = value }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch value {
        case let v as String: try container.encode(v)
        case let v as Int: try container.encode(v)
        case let v as Double: try container.encode(v)
        case let v as Bool: try container.encode(v)
        default: try container.encodeNil()
        }
    }
}
