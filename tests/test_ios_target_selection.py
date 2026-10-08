"""SELECT PLAYER IN VIDEO (A1.5, iPhone side): tap mapping, person choice, frame suggestion, and the promise that
the camera, recording, upload, telemetry and DockKit code is untouched.

Nothing here builds or launches the app, touches a phone, or runs XCTest. The rules are compiled from the app's own
source and run with swiftc; the rest are source-level pins.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios/KemetFCTracker/Sources"
MODEL = (IOS / "VideoTarget/VideoTargetModel.swift").read_text()
VIEW = (IOS / "VideoTarget/SelectPlayerInVideoView.swift").read_text() if (IOS / "VideoTarget/SelectPlayerInVideoView.swift").exists() else ""
DETECT = (IOS / "VideoTarget/VideoFramePersonDetector.swift").read_text() if (IOS / "VideoTarget/VideoFramePersonDetector.swift").exists() else ""
STORE = (IOS / "Networking/PendingAssessmentStore.swift").read_text()
APP = (IOS / "App/KemetFCTrackerApp.swift").read_text()
BASE = "e831be6"        # the import-video branch this starts from


def code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def git_show(path: str) -> str:
    return subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="the Swift compiler is not installed")
def test_tap_mapping_person_choice_frame_suggestion_and_selection_rules_hold():
    model = re.search(r"// BEGIN VideoTargetModel\n(.*?)// END VideoTargetModel", MODEL, re.S).group(1)
    harness = re.sub(r"(?m)^import (CoreGraphics|Foundation)\n", "", (ROOT / "tests/ios/video_target_harness.swift").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        main = Path(tmp) / "main.swift"
        main.write_text("import CoreGraphics\nimport Foundation\n" + model + "\n" + harness)
        build = subprocess.run(["swiftc", "-O", "-swift-version", "5", str(main), "-o", str(Path(tmp) / "check")],
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[-3000:]
        run = subprocess.run([str(Path(tmp) / "check")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr


def test_the_core_recording_upload_telemetry_and_dockkit_files_are_byte_for_byte_unchanged():
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", BASE, "ios/KemetFCTracker/Sources/DockKit",
                              "ios/KemetFCTracker/Sources/Tracking", "ios/KemetFCTracker/Sources/Vision",
                              "ios/KemetFCTracker/Sources/Capture"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    core = listing + [
        "ios/KemetFCTracker/Sources/UI/AssessmentCameraView.swift",
        "ios/KemetFCTracker/Sources/Networking/VideoUploadManager.swift", "ios/KemetFCTracker/Sources/Networking/VideoUploadRouting.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartVideoUploader.swift", "ios/KemetFCTracker/Sources/Networking/MultipartUploadAPI.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartUploadModels.swift", "ios/KemetFCTracker/Sources/Networking/MultipartUploadStateStore.swift",
        "ios/KemetFCTracker/Sources/Networking/TelemetryUploader.swift", "ios/KemetFCTracker/Sources/Networking/TelemetryQueueStore.swift",
        "ios/KemetFCTracker/Sources/Networking/TelemetryReplayService.swift", "ios/KemetFCTracker/Sources/Networking/KemetAPIClient.swift",
        "ios/KemetFCTracker/Sources/App/AssessmentSessionViewModel.swift",
    ]
    assert len(listing) > 25
    for path in core:
        assert (ROOT / path).read_text() == git_show(path), f"{path} changed"


def test_nothing_in_the_new_files_touches_the_camera_recording_tracking_telemetry_dockkit_or_the_network():
    for name, text in {"VideoTargetModel": MODEL, "SelectPlayerInVideoView": VIEW, "VideoFramePersonDetector": DETECT}.items():
        body = code(text)
        for word in ("AVCapture", "CameraCaptureManager", "TrackingCoordinator", "DockKit", "Telemetry", "startAssessment", "stopAssessment",
                     "startRecording", "finalizeAndUpload", "URLSession", "KemetAPIClient", "apiClient", "VideoUploadManager"):
            assert word not in body, f"{name} uses {word}"
        assert not re.search(r"https?://", body), name


def test_there_is_no_face_recognition_or_biometric_identity_anywhere_in_target_selection():
    for name, text in {"model": MODEL, "view": VIEW, "detector": DETECT}.items():
        body = code(text)
        for banned in ("VNDetectFaceRectanglesRequest", "VNDetectFaceLandmarksRequest", "VNGenerateImageFeaturePrintRequest",
                       "VNRecognize", "faceObservation", "VNFaceObservation", "embedding", "biometric", "faceprint"):
            assert banned not in body, f"{name}: {banned}"
    assert "VNDetectHumanRectanglesRequest" in code(DETECT)


def test_the_tap_is_mapped_through_the_geometry_and_never_snaps_to_the_nearest_person():
    view = code(VIEW)
    assert "VideoTargetGeometry.normalizedPoint(" in view and "VideoTargetPicker.personIndex(" in view
    assert "VideoTargetGeometry.uprightSize(" in view
    assert "maxDistanceIfOutsideBox" not in view and "TapToLockConverter" not in view     # the live camera's nearest-person fallback
    assert "VideoTargetPicker.noPlayerMessage" in view
    assert "appliesPreferredTrackTransform = true" in view                                # frames arrive upright


def test_confirm_stores_the_selection_locally_and_starts_no_analysis():
    view = code(VIEW)
    assert 'Button("CONFIRM PLAYER")' in view and 'Button("CHOOSE AGAIN")' in view and "SELECTED PLAYER" in view
    assert "Tap the player you want the AI to follow." in view
    assert ".disabled(!state.canConfirm)" in view
    # the only write is the coach's confirmed selection onto the local record
    assert view.count("store.upsert(") == 1 and "targetSelections" in view
    for word in ("retryUpload", "finalizeAndUpload", "analysis start", "KEMET FC Rating", "rating", "score"):
        assert word not in view, word
    assert "AI analysis is not available yet" in view


def test_the_selection_is_a_local_optional_field_so_no_database_or_server_schema_changes():
    assert "public var targetSelections: [VideoTargetSelection]?" in code(STORE)
    diff = subprocess.run(["git", "diff", "--no-color", "-U0", BASE, "--", "ios/KemetFCTracker/Sources/Networking/PendingAssessmentStore.swift"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout
    added = [l[1:].strip() for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert removed == [] and all("targetSelections" in l or l.startswith("//") or l == "" for l in added), added
    assert not list((ROOT / "alembic/versions").glob("*target_selection*"))


def test_select_player_in_video_is_offered_after_save_for_both_a_recording_and_an_imported_video():
    imported = (IOS / "VideoImport/ImportedVideoReviewView.swift").read_text()
    recorded = (IOS / "UI/VideoFirstReviewView.swift").read_text()
    assert "SELECT PLAYER IN VIDEO" in imported and "SELECT PLAYER IN VIDEO" in recorded
    assert "SelectPlayerInVideoView(" in APP and "selectingTargetRecordId" in APP


def test_the_recording_and_upload_buttons_and_the_save_flow_are_still_there():
    imported = (IOS / "VideoImport/ImportedVideoReviewView.swift").read_text()
    recorded = (IOS / "UI/VideoFirstReviewView.swift").read_text()
    for needed in ('Button("SAVE ASSESSMENT")', 'Button("Cancel")', "Retry Upload", "View Pending Uploads"):
        assert needed in imported, needed
    for needed in ('Button("SAVE ASSESSMENT")', "Retry Upload", "View Pending Uploads", "Manual measurements (advanced)"):
        assert needed in recorded, needed
    assert 'static let analysisLabel = "NOT AVAILABLE YET"' in (IOS / "UI/VideoFirstAssessment.swift").read_text()
