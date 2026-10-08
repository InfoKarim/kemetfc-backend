"""S1C1: the Player Assessment entry inside the one Kemet FC app.

These are source-level checks plus a pure-Swift check of the web view's origin rule. Nothing here builds
the app, installs it, launches it or runs XCTest (a hosted XCTest run launches the real app and its
pending-upload retry). Only committed behaviour is asserted, so the checks hold whether or not local
work-in-progress edits are present in the same files.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios/KemetFCTracker/Sources"
WEB = (IOS / "UI/WebDashboardScreen.swift").read_text()
HOME = (IOS / "UI/CoachHomeView.swift").read_text()
REVIEW = (IOS / "UI/VideoFirstReviewView.swift").read_text()
APP = (IOS / "App/KemetFCTrackerApp.swift").read_text()


def test_the_web_screen_defaults_still_open_the_full_dashboard_unrestricted():
    assert 'var pagePath: String = "dashboard"' in WEB
    assert "var restrictToOrigin: Bool = false" in WEB
    assert "baseURL.appendingPathComponent(pagePath)" in WEB
    # The existing Full Dashboard call is unchanged, so it gets the defaults.
    assert "WebDashboardScreen(baseURL: webDashboardBaseURL, onClose: { showWebDashboard = false })" in HOME


def test_the_manual_measurement_page_is_still_a_restricted_web_view_reached_from_the_saved_screen():
    cover = re.search(r"\.fullScreenCover\(isPresented: \$showManualMeasurements\) \{(.*?)\n        \}", REVIEW, re.S).group(1)
    assert 'pagePath: "v1/sprint"' in cover and "restrictToOrigin: true" in cover
    # The app's own configured origin, nothing hard-coded; the optional assessment-only override resolves to it when absent.
    assert "baseURL: AssessmentWebConfiguration.baseURL(fallback: session.sharedAPIClient.baseURL)" in cover
    assert "Manual measurements (advanced)" in REVIEW
    assert "showPlayerAssessment" not in HOME and "v1/sprint" not in HOME     # Home no longer opens it: Player Assessment is video-first


def test_recording_and_pending_uploads_are_wired_to_the_same_actions_as_before():
    assert "Button(action: onViewPendingUploads)" in HOME
    assert "Record Assessment" in HOME and "Camera and player tracking" in HOME
    record_button = HOME[HOME.index('Text("Record Assessment")') - 220:HOME.index('Text("Record Assessment")')]
    assert "VideoFirstAssessmentMode.shared.end()" in record_button and "onStartAssessment()" in record_button
    assert "onStartAssessment: { coachWantsToRecord = true }" in APP          # RootView still starts recording the same way
    assert "onViewPendingUploads: { showPendingUploads = true }" in APP
    assert "CoachHomeView(" in APP and "onPlayerAssessment" not in APP + HOME   # no new parameter, so RootView is untouched


def test_the_player_assessment_entry_only_starts_the_existing_recording_route():
    block = HOME[HOME.index("VideoFirstAssessmentMode.shared.begin()"):HOME.index('Text("Select a player, record, save")')]
    assert block.count("onStartAssessment()") == 1 and "VideoFirstAssessmentMode.shared.begin()" in block
    for forbidden in ("Timer", "timer", "startAssessment", "stopAssessment", "pendingStore", "session.", "Camera", "AVFoundation", "confirmPlayer"):
        assert forbidden not in block, forbidden
    protected = list((IOS / "Capture").glob("*.swift")) + list((IOS / "Tracking").glob("*.swift")) + \
        list((IOS / "DockKit").glob("*.swift")) + list((IOS / "Networking").glob("*.swift")) + \
        [IOS / "App/AssessmentSessionViewModel.swift", IOS / "App/RuntimeEnvironment.swift"]
    for path in protected:
        text = path.read_text()
        assert "v1/sprint" not in text and "pagePath" not in text and "showPlayerAssessment" not in text, path.name
        assert "VideoFirst" not in text, path.name


def test_the_production_default_is_unchanged_and_no_development_address_is_in_the_source():
    assert 'ProcessInfo.processInfo.environment["KEMET_BACKEND_BASE_URL"] ?? "https://app.kemetfc.com"' in APP
    for path in IOS.rglob("*.swift"):
        text = path.read_text()
        assert not re.search(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(\.\d{1,3})?\b", text), path.name
        assert "localhost:" not in text and "127.0.0.1" not in text, path.name


def test_the_restriction_is_a_real_navigation_policy():
    assert "decidePolicyFor navigationAction: WKNavigationAction" in WEB
    assert "WebOriginPolicy.allows(navigationAction.request.url, within: allowedOrigin)" in WEB
    assert "Coordinator(isLoading: $isLoading, allowedOrigin: restrictToOrigin ? origin : nil)" in WEB
    assert "guard let allowedOrigin else {\n                decisionHandler(.allow)" in WEB   # unrestricted pages are untouched


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="the Swift compiler is not installed")
def test_the_origin_rule_accepts_only_the_backend_origin():
    enum = re.search(r"// BEGIN WebOriginPolicy\n(.*?)// END WebOriginPolicy", WEB, re.S).group(1)
    harness = (ROOT / "tests/ios/web_origin_policy_harness.swift").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "check.swift"
        source.write_text("import Foundation\n" + enum + harness.replace("import Foundation\n", "", 1))
        build = subprocess.run(["swiftc", "-O", str(source), "-o", str(Path(tmp) / "check")],
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[-2000:]
        run = subprocess.run([str(Path(tmp) / "check")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr
