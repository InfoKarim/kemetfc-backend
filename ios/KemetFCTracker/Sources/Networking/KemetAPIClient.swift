//
//  KemetAPIClient.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Thin HTTP client for the KEMET FC backend's tracking API
//  (app/routers/tracking.py, genuinely built, tested, and running in
//  this same repository — 909 backend tests passing as of this commit).
//  Reuses the EXISTING cookie-session + CSRF-token auth the web app
//  already uses (main.py's SESSION_COOKIE_NAME/CSRF_COOKIE_NAME) rather
//  than inventing a separate iOS auth scheme — the coach logs in once
//  via a WKWebView-hosted /login (or a small native login form posting
//  to the same /auth/login endpoint) and this client reuses the
//  resulting session cookie for every request, via URLSession's shared
//  HTTPCookieStorage.
//

import Foundation

public struct KemetAPIConfiguration {
    public var baseURL: URL
    public init(baseURL: URL) {
        self.baseURL = baseURL
    }
}

public enum KemetAPIError: Error {
    case notAuthenticated
    case server(status: Int, detail: String)
    case decoding(Error)
    case transport(Error)
}

/// `@unchecked Sendable`: a real Xcode build (Swift 6 strict
/// concurrency) flagged every `await apiClient.post(...)` call in
/// AssessmentSessionViewModel ("sending 'self.apiClient' risks causing
/// data races") because of `csrfToken`'s mutable state below — this
/// class is called only from @MainActor contexts in practice
/// (AssessmentSessionViewModel, VideoUploadManager) plus TelemetryUploader
/// (its own @unchecked Sendable, lock-protected), but Swift can't prove
/// that from the class's shape alone. Fixed with a real `NSLock`
/// guarding the one piece of mutable state, not by asserting safety
/// that doesn't exist.
public final class KemetAPIClient: @unchecked Sendable {
    private let configuration: KemetAPIConfiguration
    private let session: URLSession
    private let csrfTokenLock = NSLock()
    private var _csrfToken: String?
    private var csrfToken: String? {
        get { csrfTokenLock.withLock { _csrfToken } }
        set { csrfTokenLock.withLock { _csrfToken = newValue } }
    }

    public init(configuration: KemetAPIConfiguration, session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
    }

    /// Exposed for BackendDiagnostics (Field Test Mode's connectivity
    /// panel) — the actual configured base URL, never a guess.
    public var baseURL: URL { configuration.baseURL }

    /// Reads the CSRF token from the same cookie the web app's own
    /// auth-client.js reads (CSRF_COOKIE_NAME in main.py) — set once
    /// after login, reused for every unsafe request, matching the
    /// existing app's own CSRF scheme exactly rather than adding a
    /// parallel one.
    public func refreshCSRFToken() {
        guard let cookies = HTTPCookieStorage.shared.cookies(for: configuration.baseURL) else { return }
        csrfToken = cookies.first(where: { $0.name == "trainingbuddy_pilot2_csrf" })?.value
    }

    public func post<Body: Encodable, Response: Decodable>(
        path: String,
        body: Body,
        as type: Response.Type
    ) async throws -> Response {
        try await request(path: path, method: "POST", body: body, as: type)
    }

    public func post(path: String) async throws {
        try await requestNoBody(path: path, method: "POST")
    }

    public func get<Response: Decodable>(path: String, as type: Response.Type) async throws -> Response {
        try await request(path: path, method: "GET", body: Optional<Int>.none, as: type)
    }

    /// Exposed for VideoUploadManager, which needs a raw URLRequest (not
    /// this client's own JSON-body `request(...)`) to attach a
    /// multipart/form-data body and stream it via
    /// `URLSession.upload(for:fromFile:)` — this returns the SAME
    /// base-URL + CSRF-header construction every other request uses,
    /// so multipart uploads authenticate identically to JSON requests.
    public func authenticatedRequest(path: String, method: String) -> URLRequest {
        var urlRequest = URLRequest(url: configuration.baseURL.appendingPathComponent(path))
        urlRequest.httpMethod = method
        if let csrfToken, method != "GET" {
            urlRequest.setValue(csrfToken, forHTTPHeaderField: "X-CSRF-Token")
        }
        return urlRequest
    }

    /// Exposed for VideoUploadManager's own `session.upload(for:fromFile:
    /// delegate:)` call, which needs the SAME URLSession (and therefore
    /// the same cookie storage/session) this client uses for every other
    /// request — never a separate, differently-configured session.
    public var underlyingSession: URLSession { session }

    private func request<Body: Encodable, Response: Decodable>(
        path: String,
        method: String,
        body: Body?,
        as type: Response.Type
    ) async throws -> Response {
        var urlRequest = URLRequest(url: configuration.baseURL.appendingPathComponent(path))
        urlRequest.httpMethod = method
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let csrfToken, method != "GET" {
            urlRequest.setValue(csrfToken, forHTTPHeaderField: "X-CSRF-Token")
        }
        if let body {
            urlRequest.httpBody = try JSONEncoder.kemet.encode(body)
        }

        let (data, response) = try await performOrThrow(urlRequest)
        try Self.validate(response: response, data: data)

        do {
            return try JSONDecoder.kemet.decode(Response.self, from: data)
        } catch {
            throw KemetAPIError.decoding(error)
        }
    }

    private func requestNoBody(path: String, method: String) async throws {
        var urlRequest = URLRequest(url: configuration.baseURL.appendingPathComponent(path))
        urlRequest.httpMethod = method
        if let csrfToken {
            urlRequest.setValue(csrfToken, forHTTPHeaderField: "X-CSRF-Token")
        }
        let (data, response) = try await performOrThrow(urlRequest)
        try Self.validate(response: response, data: data)
    }

    private func performOrThrow(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch {
            throw KemetAPIError.transport(error)
        }
    }

    static func validate(response: URLResponse, data: Data) throws {
        guard let httpResponse = response as? HTTPURLResponse else { return }
        guard (200...299).contains(httpResponse.statusCode) else {
            if httpResponse.statusCode == 401 { throw KemetAPIError.notAuthenticated }
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw KemetAPIError.server(status: httpResponse.statusCode, detail: detail ?? "Request failed")
        }
    }
}

extension JSONEncoder {
    // .iso8601, matching JSONDecoder.kemet below — a real bug this
    // session's physical device test caught: PendingAssessmentStore
    // (Sources/Networking/PendingAssessmentStore.swift) uses this SAME
    // encoder/decoder pair to persist its queue to disk, and every
    // record has a `startTime: Date`. Without this, the encoder wrote
    // dates as a raw number (Foundation's default .deferredToDate)
    // while the decoder expected an ISO8601 string — every decode
    // failed with a silently swallowed type-mismatch error, resetting
    // the whole local queue to empty on every fresh load. That broke
    // "survives app restart" for every real recording, not just tests.
    // No existing network request body encodes a Date field, so this
    // has no effect on any API call already in production use.
    static let kemet: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}

extension JSONDecoder {
    // Tries ISO8601 first (everything this app writes today, and every
    // backend response) — falling back to the raw-number format
    // (seconds since the 2001 reference date) ONLY for backward
    // compatibility with PendingAssessmentRecord queue files written by
    // the pre-fix app version, before JSONEncoder.kemet.dateEncodingStrategy
    // was set to .iso8601 above. Without this fallback, a device that
    // still has an old-format queue file on disk would hit the exact
    // silent-decode-failure bug this pairing fixed, a second time, for
    // anyone who installs this fix without ever having a clean queue.
    static let kemet: JSONDecoder = {
        let decoder = JSONDecoder()
        let iso8601Formatter = ISO8601DateFormatter()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            if let string = try? container.decode(String.self) {
                if let date = iso8601Formatter.date(from: string) {
                    return date
                }
                throw DecodingError.dataCorruptedError(
                    in: container,
                    debugDescription: "Expected an ISO8601 date string, got \"\(string)\""
                )
            }
            if let seconds = try? container.decode(Double.self) {
                return Date(timeIntervalSinceReferenceDate: seconds)
            }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Expected an ISO8601 string or a legacy numeric timestamp"
            )
        }
        return decoder
    }()
}
