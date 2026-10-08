"""The Player Assessment web view's optional, separate base URL (KEMET_ASSESSMENT_WEB_BASE_URL).

Source-level checks plus a pure-Swift check of the resolver. Nothing here builds the app, installs it,
launches it or runs XCTest (a hosted XCTest run launches the real app and its pending-upload retry).
The checks assert committed-style facts about the text of the Swift files, so they hold whether or not
unrelated local work-in-progress edits are present in the same files.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios/KemetFCTracker/Sources"
CONFIG = (IOS / "UI/AssessmentWebConfiguration.swift").read_text()
HOME = (IOS / "UI/CoachHomeView.swift").read_text()
REVIEW = (IOS / "UI/VideoFirstReviewView.swift").read_text()
APP = (IOS / "App/KemetFCTrackerApp.swift").read_text()
KEY = "KEMET_ASSESSMENT_WEB_BASE_URL"
PILOT_PORT = "87" + "65"        # the local pilot server's port, built so that it is not written out in this file
HELPER = "AssessmentWebConfiguration"


def swift_files():
    return sorted(IOS.rglob("*.swift"))


def cover(name: str) -> str:
    return re.search(r"\.fullScreenCover\(isPresented: \$" + name + r"\) \{(.*?)\n        \}", HOME, re.S).group(1)


# ---- A, B, C, E: the resolver itself, compiled with swiftc -----------------------------------

@pytest.mark.skipif(shutil.which("swiftc") is None, reason="the Swift compiler is not installed")
def test_the_resolver_falls_back_uses_the_override_and_rejects_malformed_values():
    enum = re.search(r"// BEGIN AssessmentWebConfiguration\n(.*?)// END AssessmentWebConfiguration", CONFIG, re.S).group(1)
    harness = (ROOT / "tests/ios/assessment_web_configuration_harness.swift").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "main.swift"
        source.write_text("import Foundation\n" + enum + harness.replace("import Foundation\n", "", 1))
        build = subprocess.run(["swiftc", "-O", str(source), "-o", str(Path(tmp) / "check")],
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[-2000:]
        run = subprocess.run([str(Path(tmp) / "check")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr


def test_the_resolver_reads_only_the_assessment_setting_and_returns_the_fallback_otherwise():
    assert f'static let environmentKey = "{KEY}"' in CONFIG
    code = "\n".join(line for line in CONFIG.splitlines() if not line.lstrip().startswith("//"))
    assert "KEMET_BACKEND_BASE_URL" not in code and "sharedAPIClient" not in code and "KemetAPIClient" not in code
    assert "else { return fallback }" in CONFIG
    assert "scheme == \"http\" || scheme == \"https\"" in CONFIG


# ---- the production default and the shared client are unchanged ------------------------------

def test_the_production_default_and_the_shared_client_are_unchanged():
    assert 'ProcessInfo.processInfo.environment["KEMET_BACKEND_BASE_URL"] ?? "https://app.kemetfc.com"' in APP
    assert "KemetAPIClient(configuration: .init(baseURL: baseURL))" in APP
    assert "webDashboardBaseURL: session.sharedAPIClient.baseURL" in APP
    assert KEY not in APP and HELPER not in APP


def test_no_development_address_is_in_the_swift_source():
    for path in swift_files():
        text = path.read_text()
        assert not re.search(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(\.\d{1,3})?\b", text), path.name
        assert "localhost:" not in text and "127.0.0.1" not in text, path.name
        assert PILOT_PORT not in text, path.name


# ---- the override reaches ONLY the Player Assessment web view --------------------------------

def test_the_setting_is_named_in_exactly_one_file():
    assert [p.name for p in swift_files() if KEY in p.read_text()] == ["AssessmentWebConfiguration.swift"]


def test_the_helper_is_used_only_by_the_manual_measurement_cover_on_the_saved_screen():
    users = [p.name for p in swift_files() if HELPER in p.read_text() and p.name != "AssessmentWebConfiguration.swift"]
    assert users == ["VideoFirstReviewView.swift"]
    assert REVIEW.count(HELPER) == 1
    assert "baseURL: AssessmentWebConfiguration.baseURL(fallback: session.sharedAPIClient.baseURL)" in REVIEW
    assert 'pagePath: "v1/sprint"' in REVIEW


def test_the_full_dashboard_and_the_record_and_pending_buttons_do_not_use_the_override():
    assert "WebDashboardScreen(baseURL: webDashboardBaseURL, onClose: { showWebDashboard = false })" in HOME
    assert HELPER not in HOME
    assert "Button(action: onViewPendingUploads)" in HOME and "onStartAssessment()" in HOME
    assert "onStartAssessment: { coachWantsToRecord = true }" in APP
    assert "onViewPendingUploads: { showPendingUploads = true }" in APP


def test_the_web_view_itself_is_unchanged_and_still_takes_its_origin_from_its_caller():
    web = (IOS / "UI/WebDashboardScreen.swift").read_text()
    assert HELPER not in web and KEY not in web
    assert "let baseURL: URL" in web and 'var pagePath: String = "dashboard"' in web


# ---- D: video, pending uploads and telemetry stay on the shared client -----------------------

PROTECTED = ["Capture", "Tracking", "DockKit", "Networking"]


def test_nothing_in_capture_tracking_dockkit_or_networking_can_see_the_setting():
    for folder in PROTECTED:
        for path in (IOS / folder).rglob("*.swift"):
            text = path.read_text()
            assert KEY not in text and HELPER not in text and "ASSESSMENT_WEB" not in text, path
    for name in ("App/AssessmentSessionViewModel.swift", "App/RuntimeEnvironment.swift", "App/KemetFCTrackerApp.swift"):
        text = (IOS / name).read_text()
        assert KEY not in text and HELPER not in text, name


def test_the_only_environment_reads_in_the_app_are_the_two_known_ones():
    reads = {}
    for path in swift_files():
        for match in re.findall(r'environment\[\s*"([A-Za-z0-9_]+)"\s*\]', path.read_text()):
            reads.setdefault(match, set()).add(path.name)
    assert reads.get("KEMET_BACKEND_BASE_URL") == {"KemetFCTrackerApp.swift"}
    assert reads.get("XCTestConfigurationFilePath") == {"RuntimeEnvironment.swift"}
    assert KEY not in reads                                     # the helper takes it from a parameter, by variable name
    assert set(reads) == {"KEMET_BACKEND_BASE_URL", "XCTestConfigurationFilePath"}


def test_uploads_pending_retries_and_telemetry_are_built_from_the_one_shared_client():
    model = (IOS / "App/AssessmentSessionViewModel.swift").read_text()
    assert "public var sharedAPIClient: KemetAPIClient { apiClient }" in model
    assert "TelemetryReplayService(apiClient: apiClient)" in model
    assert "MultipartVideoUploader(apiClient: apiClient" in model
    assert "TelemetryUploader(apiClient: apiClient, sessionId: sessionId)" in model
    assert "for record in pendingStore.pendingUploads where record.isRetryable" in model     # retries run through the same view model
    for name in ("Networking/TelemetryUploader.swift", "Networking/TelemetryReplayService.swift",
                 "Networking/MultipartVideoUploader.swift", "Networking/VideoUploadManager.swift"):
        text = (IOS / name).read_text()
        assert "ProcessInfo" not in text and "environment[" not in text, name     # no environment-driven URL in the upload paths
