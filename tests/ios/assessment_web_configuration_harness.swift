import Foundation

// Pure checks of AssessmentWebConfiguration, compiled together with the enum copied out of the
// app source by tests/test_ios_assessment_web_config.py. No app, simulator or device is involved.

var failures: [String] = []
func check(_ condition: Bool, _ message: String) {
    if !condition { failures.append(message) }
}

let key = AssessmentWebConfiguration.environmentKey
let shared = URL(string: "https://app.kemetfc.com")!

// The setting has the agreed name.
check(key == "KEMET_ASSESSMENT_WEB_BASE_URL", "environment key name")

// A. No override: the assessment web view uses exactly the shared client's base URL.
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [:]) == shared, "A: absent -> fallback")
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: ["KEMET_BACKEND_BASE_URL": "http://other.test:1"]) == shared,
      "A: the main backend setting is not an assessment override")

// B. A valid override is used.
let dev = "http://dev-host.test:9123"
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: dev]) == URL(string: dev)!, "B: http origin with port")
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: "https://pilot.test"]) == URL(string: "https://pilot.test")!, "B: https origin")
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: "  \(dev)\n"]) == URL(string: dev)!, "B: surrounding whitespace is trimmed")
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: "HTTP://Dev-Host.test:9123/"]).host?.lowercased() == "dev-host.test", "B: scheme case and trailing slash")
let withSlash = AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: dev + "/"])
check(withSlash.appendingPathComponent("v1/sprint").absoluteString == dev + "/v1/sprint", "B: the sprint page resolves under the override")

// C. The shared base URL is a value the override cannot change; the fallback is returned untouched.
let before = shared.absoluteString
_ = AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: dev])
check(shared.absoluteString == before && shared == URL(string: "https://app.kemetfc.com")!, "C: shared base URL unchanged")
check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: dev]) != shared, "C: override differs from shared only in the result")
let lan = URL(string: "http://dev-host.test:8010")!
check(AssessmentWebConfiguration.baseURL(fallback: lan, environment: [:]) == lan, "C: a different shared URL is returned as given")

// E. Malformed or unsafe values fall back to the shared URL.
let bad = ["", "   ", "not a url", "http://", "https://:9123", "ftp://dev-host.test", "dev-host.test:9123",
           "192.0.2.10:9123", "//dev-host.test", "javascript:alert(1)", "file:///tmp/x",
           "http://dev-host.test/some/path", "http://dev-host.test?x=1", "http://dev-host.test#frag",
           "http://user:x@dev-host.test", "http://de v.test"]
for value in bad {
    check(AssessmentWebConfiguration.baseURL(fallback: shared, environment: [key: value]) == shared, "E: \(value.debugDescription) must fall back")
}

if failures.isEmpty {
    print("ALL CHECKS PASSED")
} else {
    for failure in failures { print("FAILED: \(failure)") }
    exit(1)
}
