//
//  TelemetryTestSupport.swift
//  KemetFCTrackerTests
//
//  Shared test support for the durable-telemetry tests. Uses ONLY types
//  that exist today (KemetAPIClient, TrackingSample), so this file compiles
//  against the current production code.
//
//  * StubServer is a URLProtocol: the real KemetAPIClient talks to it
//    through its injectable URLSession, so the tests exercise the real
//    request encoding/decoding with no protocol refactor.
//  * Every test gets its own temporary queue directory (never the app's
//    real Application Support — the same isolation rule as
//    PendingAssessmentStoreTests).
//

import XCTest
@testable import KemetFCTracker

/// One request the stub server received.
struct RecordedRequest {
    let path: String
    let method: String
    let body: Data

    var json: [String: Any] {
        (try? JSONSerialization.jsonObject(with: body) as? [String: Any]) ?? [:]
    }

    /// `sequence_number` of every sample in a POST .../samples body, in order.
    var sampleSequenceNumbers: [Int] {
        let samples = json["samples"] as? [[String: Any]] ?? []
        return samples.compactMap { $0["sequence_number"] as? Int }
    }

    var isSamplesUpload: Bool { path.hasSuffix("/samples") }
    var isEventUpload: Bool { path.hasSuffix("/events") }
}

/// What the stub answers with.
struct StubReply {
    var status: Int
    var body: Data
    /// Seconds to hold the response — lets tests overlap concurrent requests.
    var delay: TimeInterval = 0
    /// Simulates no connectivity: the request fails before any response.
    var transportFailure = false
    /// Additional response headers — e.g. a presigned-upload stub
    /// simulating R2's own `ETag` response header (2026-10-01, multipart
    /// upload tests). Merged with the default Content-Type below.
    var headers: [String: String] = [:]

    static func json(_ status: Int, _ object: [String: Any], delay: TimeInterval = 0) -> StubReply {
        StubReply(status: status, body: (try? JSONSerialization.data(withJSONObject: object)) ?? Data(), delay: delay)
    }

    static let offline = StubReply(status: 0, body: Data(), transportFailure: true)
    static let serverError = StubReply.json(500, ["detail": "boom"])

    /// The explicit acknowledgment the durable queue requires before it may
    /// delete anything: exactly the listed sequences are durably accounted for.
    static func acknowledged(sequences: [Int], ingested: Int, duplicates: Int = 0, delay: TimeInterval = 0) -> StubReply {
        .json(201, ["ingested": ingested, "duplicates": duplicates, "acknowledged_sequences": sequences], delay: delay)
    }
}

/// Process-wide stub state (XCTest runs a class's tests serially; every test
/// resets it in setUp).
final class StubServerState: @unchecked Sendable {
    private let lock = NSLock()
    private var requests: [RecordedRequest] = []
    private var handler: @Sendable (RecordedRequest) -> StubReply = { _ in .offline }
    private var inFlight = 0
    private var peakInFlight = 0

    func reset() {
        lock.withLock {
            requests = []
            handler = { _ in .offline }
            inFlight = 0
            peakInFlight = 0
        }
    }

    func setHandler(_ newHandler: @escaping @Sendable (RecordedRequest) -> StubReply) {
        lock.withLock { handler = newHandler }
    }

    var recorded: [RecordedRequest] { lock.withLock { requests } }
    var maxConcurrentRequests: Int { lock.withLock { peakInFlight } }

    fileprivate func begin(_ request: RecordedRequest) -> StubReply {
        lock.withLock {
            requests.append(request)
            inFlight += 1
            peakInFlight = max(peakInFlight, inFlight)
            return handler(request)
        }
    }

    fileprivate func end() {
        lock.withLock { inFlight -= 1 }
    }
}

/// URLProtocol instances are handed back to the loading client from another queue.
private struct ProtocolBox: @unchecked Sendable { let protocolInstance: URLProtocol }

final class StubServer: URLProtocol {
    static let state = StubServerState()

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let recorded = RecordedRequest(
            path: request.url?.path ?? "", method: request.httpMethod ?? "GET", body: Self.readBody(of: request)
        )
        let reply = Self.state.begin(recorded)
        let client = self.client
        let url = request.url ?? URL(string: "http://stub.test")!
        let this = ProtocolBox(protocolInstance: self)

        DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay) {
            defer { Self.state.end() }
            let protocolInstance = this.protocolInstance
            if reply.transportFailure {
                client?.urlProtocol(protocolInstance, didFailWithError: URLError(.notConnectedToInternet))
                return
            }
            var headerFields = ["Content-Type": "application/json"]
            for (key, value) in reply.headers { headerFields[key] = value }
            let response = HTTPURLResponse(
                url: url, statusCode: reply.status, httpVersion: "HTTP/1.1",
                headerFields: headerFields
            )!
            client?.urlProtocol(protocolInstance, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(protocolInstance, didLoad: reply.body)
            client?.urlProtocolDidFinishLoading(protocolInstance)
        }
    }

    override func stopLoading() {}

    /// URLSession hands upload bodies to a URLProtocol as a stream.
    private static func readBody(of request: URLRequest) -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

/// Base class: isolated temp queue directory + reset stub + real API client.
class TelemetryTestCase: XCTestCase {
    var root: URL!
    var stub: StubServerState { StubServer.state }

    override func setUpWithError() throws {
        try super.setUpWithError()
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kemet-telemetry-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        StubServer.state.reset()
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
        StubServer.state.reset()
        try super.tearDownWithError()
    }

    func makeAPIClient() -> KemetAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubServer.self]
        return KemetAPIClient(
            configuration: KemetAPIConfiguration(baseURL: URL(string: "http://stub.test")!),
            session: URLSession(configuration: configuration)
        )
    }

    /// A TrackingSample built by decoding JSON, so this file does not depend
    /// on the (internal) memberwise initializer that will gain a field.
    func makeSample(t: Double) -> TrackingSample {
        let json: [String: Any] = [
            "t_seconds": t,
            "player_bbox": [0.4, 0.3, 0.15, 0.4],
            "player_center": [0.475, 0.5],
            "player_confidence": 0.9,
            "player_track_id": 1,
            "gimbal_state": "tracking_active",
            "tracking_mode": "smart_soccer",
            "tracking_status": "locked",
        ]
        // swiftlint:disable:next force_try
        return try! JSONDecoder().decode(TrackingSample.self, from: JSONSerialization.data(withJSONObject: json))
    }
}
