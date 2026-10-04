// Checks for WebOriginPolicy (ios/KemetFCTracker/Sources/UI/WebDashboardScreen.swift).
// tests/test_ios_player_assessment_entry.py puts the real enum in front of this file and runs it
// as a plain command-line program: no app, no XCTest, no device, no app storage.
import Foundation
let origin = URL(string: "https://app.kemetfc.com")!
let lan = URL(string: "http://10.0.0.150:8765")!
var failures = 0
func check(_ name: String, _ got: Bool, _ want: Bool) { if got != want { failures += 1; print("FAIL", name, "got", got, "want", want) } }
func u(_ s: String) -> URL? { URL(string: s) }
// production origin
check("same page", WebOriginPolicy.allows(u("https://app.kemetfc.com/v1/sprint"), within: origin), true)
check("same with query", WebOriginPolicy.allows(u("https://app.kemetfc.com/v1/sprint?assessment=V1A000001"), within: origin), true)
check("login", WebOriginPolicy.allows(u("https://app.kemetfc.com/login?next=/v1/sprint"), within: origin), true)
check("explicit 443", WebOriginPolicy.allows(u("https://app.kemetfc.com:443/x"), within: origin), true)
check("case", WebOriginPolicy.allows(u("HTTPS://APP.KEMETFC.COM/x"), within: origin), true)
check("about:blank", WebOriginPolicy.allows(u("about:blank"), within: origin), true)
check("other host", WebOriginPolicy.allows(u("https://evil.example/x"), within: origin), false)
check("lookalike suffix", WebOriginPolicy.allows(u("https://app.kemetfc.com.evil.example/x"), within: origin), false)
check("lookalike prefix", WebOriginPolicy.allows(u("https://evilapp.kemetfc.com/x"), within: origin), false)
check("subdomain", WebOriginPolicy.allows(u("https://x.app.kemetfc.com/x"), within: origin), false)
check("downgrade to http", WebOriginPolicy.allows(u("http://app.kemetfc.com/x"), within: origin), false)
check("other port", WebOriginPolicy.allows(u("https://app.kemetfc.com:8443/x"), within: origin), false)
check("userinfo trick", WebOriginPolicy.allows(u("https://app.kemetfc.com@evil.example/x"), within: origin), false)
check("javascript", WebOriginPolicy.allows(u("javascript:alert(1)"), within: origin), false)
check("file", WebOriginPolicy.allows(u("file:///etc/passwd"), within: origin), false)
check("data", WebOriginPolicy.allows(u("data:text/html,x"), within: origin), false)
check("mailto", WebOriginPolicy.allows(u("mailto:a@b.c"), within: origin), false)
check("nil", WebOriginPolicy.allows(nil, within: origin), false)
// a development origin (LAN, http, explicit port)
check("lan same", WebOriginPolicy.allows(u("http://10.0.0.150:8765/v1/sprint"), within: lan), true)
check("lan other port", WebOriginPolicy.allows(u("http://10.0.0.150:8766/v1/sprint"), within: lan), false)
check("lan vs prod", WebOriginPolicy.allows(u("https://app.kemetfc.com/x"), within: lan), false)
check("prod vs lan", WebOriginPolicy.allows(u("http://10.0.0.150:8765/x"), within: origin), false)
print(failures == 0 ? "ORIGIN POLICY: ALL CHECKS PASSED" : "ORIGIN POLICY: \(failures) FAILED")
exit(failures == 0 ? 0 : 1)
