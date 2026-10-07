"""Video-first PLAYER ASSESSMENT (Phase 2A): select a player, record with the EXISTING engine, review, save.

Source-level checks plus a pure-Swift check of the review rules. Nothing here builds the app, installs it,
launches it, starts a recording or runs XCTest (a hosted XCTest run launches the real app and its pending-upload
retry). The recording, integrity, pending-store and upload code are not changed by this feature, and these tests
pin that: the new files may only read what that engine already decided.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios/KemetFCTracker/Sources"
MODEL = (IOS / "UI/VideoFirstAssessment.swift").read_text()
MODE = (IOS / "UI/VideoFirstAssessmentMode.swift").read_text()
REVIEW = (IOS / "UI/VideoFirstReviewView.swift").read_text()
SUMMARY = (IOS / "UI/AssessmentSummaryView.swift").read_text()
HOME = (IOS / "UI/CoachHomeView.swift").read_text()
APP = (IOS / "App/KemetFCTrackerApp.swift").read_text()
NEW_FILES = {"VideoFirstAssessment.swift": MODEL, "VideoFirstAssessmentMode.swift": MODE, "VideoFirstReviewView.swift": REVIEW}


def code(text: str) -> str:
    """Swift source without comments, so a rule written in a comment is not mistaken for behaviour."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


# ---- 3, 5, 6, 10: the review rules (compiled and run with swiftc) ---------------------------------------

@pytest.mark.skipif(shutil.which("swiftc") is None, reason="the Swift compiler is not installed")
def test_the_review_and_save_rules_hold():
    model = re.search(r"// BEGIN VideoFirstAssessmentModel\n(.*?)// END VideoFirstAssessmentModel", MODEL, re.S).group(1)
    mode = re.sub(r"(?m)^import .*\n", "", MODE)
    # the mode also remembers which video source the coach chose, a type that lives in the import model
    import_model = re.search(r"// BEGIN VideoImportModel\n(.*?)// END VideoImportModel", (IOS / "VideoImport/VideoImportModel.swift").read_text(), re.S).group(1)
    harness = re.sub(r"(?m)^import (Combine|Foundation)\n", "", (ROOT / "tests/ios/video_first_assessment_harness.swift").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "main.swift"
        source.write_text("import Combine\nimport Foundation\n" + model + "\n" + import_model + "\n" + mode + "\n" + harness)
        build = subprocess.run(["swiftc", "-O", "-swift-version", "5", str(source), "-o", str(Path(tmp) / "check")],
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[-2000:]
        run = subprocess.run([str(Path(tmp) / "check")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr


# ---- the new files only read what the existing engine decided ----------------------------------------------

def test_the_new_files_never_touch_the_camera_recording_tracking_telemetry_dockkit_or_upload_code():
    forbidden = ("AVFoundation", "AVCapture", "CameraCaptureManager", "TrackingCoordinator", "DockKit", "Telemetry",
                 "startAssessment", "stopAssessment", "startRecording", "stopRecording", "verifyFileHealth",
                 "measureRecording", "VideoUploadManager", "MultipartVideoUploader", "finalizeAndUpload",
                 "retryAllPending", "URLSession", "KemetAPIClient", "apiClient", ".post(", ".get(")
    for name, text in NEW_FILES.items():
        for word in forbidden:
            assert word not in code(text), f"{name} uses {word}"


def test_the_review_screen_reads_the_pending_record_and_changes_nothing_in_the_store():
    body = code(REVIEW)
    assert "store.record(id: recordId)" in body and "store.fileExistsSync(for: record)" in body
    for write in ("upsert(", "deleteRecord", "deleteLocalFile", "newRecordingFileURL", "pendingStore", "clearFinishedRecording", "confirmPlayer"):
        assert write not in body, write
    # The only actions it offers are the ones the classic summary already offered, handed in by RootView.
    assert "let onRetryUpload: () -> Void" in body and "let onViewPendingUploads: () -> Void" in body and "let onDone: () -> Void" in body


def test_no_network_address_production_or_development_url_is_in_the_new_files():
    for name, text in NEW_FILES.items():
        assert not re.search(r"https?://", code(text)), name
        assert "kemetfc.com" not in text and "8765" not in text, name
        assert not re.search(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}", text), name
    # The one network address it can reach is the app's own configured one, through the existing helper.
    assert "AssessmentWebConfiguration.baseURL(fallback: session.sharedAPIClient.baseURL)" in REVIEW


# ---- 2, 7: the selected player is the one on the saved recording -----------------------------------------------

def test_the_screens_show_the_player_the_recording_was_saved_with_and_create_no_player():
    body = code(REVIEW)
    assert body.count("record.playerName") == 2                              # review and saved screens
    for creator in ("createPlayer", "PlayerDB", "players/", "player_id", "playerId"):
        assert creator not in body, creator
    # Player selection is the existing screen, reached through RootView exactly as before.
    assert "PlayerSelectionView(" in APP and "onPlayerConfirmed: { session.confirmPlayer($0) }" in APP


# ---- 3, 4: recording starts and stops only in the existing code --------------------------------------------------

def test_recording_is_started_and_stopped_only_by_the_existing_root_view_calls():
    assert APP.count("await session.startAssessment(mode: .smartSoccer, speedProfile: .sport)") == 2
    assert "await session.stopAssessment()" in APP
    # RootView only observes the mode (to show the video-source choice) and shows the imported-video screens; it
    # never calls the recording-review code or the model rules directly.
    for name in ("VideoFirstAssessmentModel", "VideoFirstReviewView"):
        assert name not in APP, name
    assert 'onStartAssessment: { coachWantsToRecord = true }' in APP


# ---- 5, 6, 10: save is blocked by a failed recording and cannot happen twice ---------------------------------------

def test_save_is_enabled_only_for_a_healthy_recording_and_is_a_single_no_op_confirmation():
    body = code(REVIEW)
    assert 'Button("SAVE ASSESSMENT") { mode.save(recordId) }' in body
    assert ".disabled(!VideoFirstAssessmentModel.canSave(facts))" in body
    assert "integrityFailed: record.integrityFailure != nil" in body           # the existing check's own verdict
    assert "mode.savedRecordIds.contains(recordId) && VideoFirstAssessmentModel.canSave(facts)" in body
    assert "savedRecordIds.insert(recordId).inserted" in code(MODE)


def test_a_recording_that_failed_integrity_is_never_shown_as_saved_or_uploaded():
    model = code(MODEL)
    assert "if facts.integrityFailed {" in model and "NOT UPLOADED (recording is not valid)" in model
    assert "return .problem(" in model and "reviewStatus(facts) == .ready" in model


# ---- 8, 9, 13: nothing about upload, durability or the classic flow changed ---------------------------------------

def test_the_classic_summary_is_intact_and_only_switched_by_the_video_first_flag():
    assert "if videoFirst.isActive {" in code(SUMMARY) and "classicContent(for: record)" in code(SUMMARY)
    for kept in ("Retry Upload", "View Pending Uploads", "ASSESSMENT COMPLETE", "RECORDING FAILED", "Was the correct player tracked?",
                 "onConfirmPlayerTracked", "Recording Not Found"):
        assert kept in SUMMARY, kept
    assert "private func classicContent(for record: PendingAssessmentRecord) -> some View {" in SUMMARY


def test_record_assessment_keeps_working_and_clears_the_video_first_flag():
    block = HOME[HOME.index('VideoFirstAssessmentMode.shared.end()'):HOME.index('Text("Camera and player tracking")')]
    assert "onStartAssessment()" in block and 'Text("Record Assessment")' in block
    assert HOME.count("VideoFirstAssessmentMode.shared.begin()") == 1 and HOME.count("VideoFirstAssessmentMode.shared.end()") == 1


def test_the_flag_lives_in_memory_only_and_starts_off():
    body = code(MODE)
    assert "private(set) var isActive = false" in body
    for persistence in ("UserDefaults", "FileManager", "Keychain", "CoreData", "write(to", "Codable"):
        assert persistence not in body, persistence


# ---- 14: Phase 1 is kept ---------------------------------------------------------------------------------------

def test_the_manual_measurement_page_is_kept_as_an_advanced_tool_and_not_a_home_button():
    assert "Manual measurements (advanced)" in REVIEW and 'pagePath: "v1/sprint"' in REVIEW
    assert "v1/sprint" not in HOME and "Manual measurements" not in HOME
    # Player Assessment is the first and primary assessment button; Record Assessment stays below it for now.
    assert HOME.index('Text("Player Assessment")') < HOME.index('Text("Record Assessment")') < HOME.index("Button(action: onViewPendingUploads)")
    assert HOME.count('Text("Player Assessment")') == 1


# ---- the analysis line is honest -----------------------------------------------------------------------------------

def test_the_screen_shows_no_score_rating_speed_or_made_up_analysis_status():
    text = (code(MODEL) + code(REVIEW)).lower()
    for word in ("rating", "score", "talent", "km/h", "m/s", "waiting", "processing", "estimate"):
        assert word not in text, word
    assert not re.search(r"\bpace\b", text)
    assert 'static let analysisLabel = "NOT AVAILABLE YET"' in code(MODEL)
