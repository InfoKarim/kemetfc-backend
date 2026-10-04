//
//  AssessmentWebConfiguration.swift
//  KemetFCTracker
//
//  Which origin the Player Assessment web view loads from.
//
//  By default it is the app's one configured backend (KemetAPIClient.baseURL, i.e.
//  KEMET_BACKEND_BASE_URL with the production default). A developer or tester may set the
//  separate, optional environment variable KEMET_ASSESSMENT_WEB_BASE_URL on a launch to point
//  ONLY the Player Assessment web view elsewhere (for example a local pilot server).
//
//  This type is read in exactly one place: the Player Assessment cover in CoachHomeView. It is
//  not read by the API client, video upload, pending-upload retry, telemetry, or the Full
//  Dashboard, so none of them can be redirected by it. No development address is stored in the
//  source: when the variable is absent or is not a plain http(s) origin, the fallback is used.
//

import Foundation

// BEGIN AssessmentWebConfiguration
enum AssessmentWebConfiguration {
    static let environmentKey = "KEMET_ASSESSMENT_WEB_BASE_URL"

    /// The override when it is a plain http(s) origin (scheme, host, optional port, nothing
    /// else); otherwise `fallback`, which callers pass as the shared API client's base URL.
    static func baseURL(
        fallback: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> URL {
        guard let raw = environment[environmentKey]?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty,
              let parts = URLComponents(string: raw),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              let url = parts.url
        else { return fallback }
        return url
    }
}
// END AssessmentWebConfiguration
