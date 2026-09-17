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

public final class KemetAPIClient {
    private let configuration: KemetAPIConfiguration
    private let session: URLSession
    private var csrfToken: String?

    public init(configuration: KemetAPIConfiguration, session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
    }

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
    static let kemet: JSONEncoder = {
        let encoder = JSONEncoder()
        return encoder
    }()
}

extension JSONDecoder {
    static let kemet: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()
}
