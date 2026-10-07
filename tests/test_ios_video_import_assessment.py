"""PLAYER ASSESSMENT accepts RECORD VIDEO or UPLOAD VIDEO; both end up as the same pending record and upload path.

Three kinds of check, none of which builds or launches the app, installs it, touches a phone, or runs XCTest:
  * the imported-video rules, compiled from the app's own source and run with swiftc;
  * the REAL PendingAssessmentStore + VideoImportService compiled for macOS and run on real video files
    (hash, durability, rotation, extension, cancel, save, restart, old records);
  * source pins: the recording, camera, DockKit, tracking, telemetry and upload code is unchanged, and the new code
    cannot transcode, read a whole video into memory, infer a player from a file name, or reach the network.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios/KemetFCTracker/Sources"
IMPORT_DIR = IOS / "VideoImport"
MODEL = (IMPORT_DIR / "VideoImportModel.swift").read_text()
SERVICE = (IMPORT_DIR / "VideoImportService.swift").read_text()
CHOICE = (IMPORT_DIR / "VideoSourceChoiceView.swift").read_text()
REVIEW = (IMPORT_DIR / "ImportedVideoReviewView.swift").read_text()
STORE = (IOS / "Networking/PendingAssessmentStore.swift").read_text()
VM = (IOS / "App/AssessmentSessionViewModel.swift").read_text()
APP = (IOS / "App/KemetFCTrackerApp.swift").read_text()
MODE = (IOS / "UI/VideoFirstAssessmentMode.swift").read_text()
NEW = {"VideoImportModel": MODEL, "VideoImportService": SERVICE, "VideoSourceChoiceView": CHOICE, "ImportedVideoReviewView": REVIEW}
BASE = "4d05592"            # the video-first branch this feature starts from
HAS_SWIFTC = shutil.which("swiftc") is not None


def code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def git_show(path: str) -> str:
    return subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def function_body(text: str, signature: str) -> str:
    """The text of one Swift function, from its signature to the matching closing brace."""
    start = text.index(signature)
    depth, i = 0, text.index("{", start)
    first = i
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


# ---- the imported-video rules (pure Swift) -------------------------------------------------------------------------

HARNESS = r"""
import Foundation
var failures: [String] = []
func check(_ c: Bool, _ m: String) { if !c { failures.append(m) } }
typealias I = ImportedVideoIntegrity
func good() -> ImportedVideoMeasurement {
    ImportedVideoMeasurement(fileExists: true, sizeBytes: 46_745_716, isReadable: true, isPlayable: true, hasProtectedContent: false,
        hasVideoTrack: true, durationSeconds: 31.7367, width: 1080, height: 1920, frameRate: 59.99)
}
check(I.evaluate(good()) == .valid, "a normal iPhone video is valid")
// no camera coverage rule: a short clip with no assessment timer is fine
var short = good(); short.durationSeconds = 0.4; check(I.evaluate(short) == .valid, "a very short video is valid (no recording-coverage rule)")
func bad(_ name: String, _ edit: (inout ImportedVideoMeasurement) -> Void) {
    var m = good(); edit(&m)
    if case .invalid(let why) = I.evaluate(m) { check(!why.isEmpty, name + " explains itself") } else { check(false, name + " must be invalid") }
}
bad("missing file") { $0.fileExists = false }
bad("empty file") { $0.sizeBytes = 0 }
bad("unreadable") { $0.isReadable = false }
bad("protected") { $0.hasProtectedContent = true }
bad("no video track") { $0.hasVideoTrack = false }
bad("not playable") { $0.isPlayable = false }
bad("zero duration") { $0.durationSeconds = 0 }
bad("negative duration") { $0.durationSeconds = -3 }
bad("NaN duration") { $0.durationSeconds = Double.nan }
bad("infinite duration") { $0.durationSeconds = Double.infinity }
bad("zero width") { $0.width = 0 }
bad("zero height") { $0.height = 0 }
bad("tiny picture") { $0.width = 8 }
bad("absurd picture") { $0.height = 100_000 }
bad("no frame rate") { $0.frameRate = 0 }
bad("NaN frame rate") { $0.frameRate = Double.nan }
bad("absurd frame rate") { $0.frameRate = 5000 }
bad("over the upload limit") { $0.sizeBytes = 2 * 1024 * 1024 * 1024 + 1 }
var exactlyLimit = good(); exactlyLimit.sizeBytes = 2 * 1024 * 1024 * 1024; check(I.evaluate(exactlyLimit) == .valid, "exactly the limit is allowed")

typealias R = ImportedVideoReviewModel
func facts(file: Bool = true, problem: String? = nil, saved: Bool = false) -> ImportedVideoReviewFacts {
    ImportedVideoReviewFacts(fileExists: file, integrityProblem: problem, durationSeconds: 31.7367, width: 1080, height: 1920, frameRate: 59.99, sizeBytes: 46_745_716, coachSaved: saved)
}
check(R.status(facts()) == .ready && R.canSave(facts()) && R.statusWord(R.status(facts())) == "READY", "READY and savable")
check(!R.canSave(facts(problem: "x")) && R.statusWord(R.status(facts(problem: "x"))) == "PROBLEM", "PROBLEM blocks save")
check(!R.canSave(facts(file: false)), "a missing file blocks save")
check(!R.canSave(facts(saved: true)), "saving twice is not offered")
check(R.durationText(31.7367) == "00:31" && R.durationText(nil) == "—" && R.durationText(Double.nan) == "—" && R.durationText(-1) == "—", "duration text")
check(R.resolutionText(width: 1080, height: 1920, frameRate: 59.99) == "1080 × 1920 · 60 fps", "resolution text")
check(R.resolutionText(width: 1080, height: 1920, frameRate: nil) == "1080 × 1920" && R.resolutionText(width: nil, height: nil, frameRate: 30) == "—", "unknown parts are dashes")
check(R.sizeText(46_745_716) == "44.6 MB" && R.sizeText(nil) == "—" && R.sizeText(3 * 1_073_741_824) == "3.00 GB", "size text")
check(AssessmentVideoSource.cameraRecording.rawValue == "CAMERA_RECORDING" && AssessmentVideoSource.importedVideo.rawValue == "IMPORTED_VIDEO", "source names")
check(AssessmentVideoSource.importedVideo.displayName == "Uploaded Video", "the review says Uploaded Video")
check(R.analysisLabel == "NOT AVAILABLE YET", "no analysis claimed")
if failures.isEmpty { print("ALL CHECKS PASSED") } else { failures.forEach { print("FAIL:", $0) }; exit(1) }
"""


def compile_and_run(sources: list[str], main_text: str, env: dict | None = None, name="check", timeout=600):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        files = []
        for i, text in enumerate(sources):
            path = tmp / f"s{i}.swift"
            path.write_text(text)
            files.append(str(path))
        main = tmp / "main.swift"
        main.write_text(main_text)
        build = subprocess.run(["swiftc", "-O", "-swift-version", "5", *files, str(main), "-o", str(tmp / name)],
                               capture_output=True, text=True, timeout=timeout)
        assert build.returncode == 0, build.stderr[-3000:]
        return subprocess.run([str(tmp / name)], capture_output=True, text=True, timeout=300, env={**os.environ, **(env or {})})


@pytest.mark.skipif(not HAS_SWIFTC, reason="the Swift compiler is not installed")
def test_the_imported_video_integrity_and_review_rules_hold():
    model = re.search(r"// BEGIN VideoImportModel\n(.*?)// END VideoImportModel", MODEL, re.S).group(1)
    run = compile_and_run(["import Foundation\n" + model], HARNESS)
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr


# ---- the real store + importer on real video files ----------------------------------------------------------------

@pytest.mark.skipif(not HAS_SWIFTC or shutil.which("ffmpeg") is None, reason="swiftc and ffmpeg are needed")
def test_the_real_importer_copies_hashes_measures_saves_cancels_and_survives_a_restart_on_real_files():
    with tempfile.TemporaryDirectory() as fx:
        fx = Path(fx)

        def ffmpeg(*args):
            subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

        ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fx / "landscape.mov"))
        ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fx / "base.mp4"))
        ffmpeg("-display_rotation", "90", "-i", str(fx / "base.mp4"), "-c", "copy", str(fx / "portrait_rotated.mp4"))
        (fx / "not_a_video.mov").write_bytes(os.urandom(5000))
        (fx / "empty.mov").write_bytes(b"")
        # the store needs RecordingMeasurement from the (unchanged) recording-integrity file
        sources = [(IOS / "Networking/PendingAssessmentStore.swift").read_text(), (IOS / "Capture/RecordingIntegrity.swift").read_text(),
                   MODEL, SERVICE]
        harness = (ROOT / "tests/ios/video_import_files_harness.swift").read_text()
        run = compile_and_run(sources, harness, env={"FIXTURES": str(fx)}, name="import_files")
    assert run.returncode == 0 and "ALL CHECKS PASSED" in run.stdout, run.stdout + run.stderr


# ---- RECORD VIDEO is the unchanged existing flow -----------------------------------------------------------------------

def test_the_camera_recording_flow_and_every_core_file_are_byte_for_byte_unchanged():
    core = [
        "ios/KemetFCTracker/Sources/Capture/CameraCaptureManager.swift",
        "ios/KemetFCTracker/Sources/Capture/RecordingIntegrity.swift",
        "ios/KemetFCTracker/Sources/Capture/CaptureFormatChooser.swift",
        "ios/KemetFCTracker/Sources/UI/AssessmentCameraView.swift",
        "ios/KemetFCTracker/Sources/UI/AssessmentControlDock.swift",
        "ios/KemetFCTracker/Sources/UI/AssessmentHUD.swift",
        "ios/KemetFCTracker/Sources/Networking/VideoUploadManager.swift",
        "ios/KemetFCTracker/Sources/Networking/VideoUploadRouting.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartVideoUploader.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartUploadAPI.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartUploadModels.swift",
        "ios/KemetFCTracker/Sources/Networking/MultipartUploadStateStore.swift",
        "ios/KemetFCTracker/Sources/Networking/TelemetryUploader.swift",
        "ios/KemetFCTracker/Sources/Networking/TelemetryQueueStore.swift",
        "ios/KemetFCTracker/Sources/Networking/TelemetryReplayService.swift",
        "ios/KemetFCTracker/Sources/Networking/KemetAPIClient.swift",
    ]
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", BASE, "ios/KemetFCTracker/Sources/DockKit",
                              "ios/KemetFCTracker/Sources/Tracking", "ios/KemetFCTracker/Sources/Vision"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert len(listing) > 20
    for path in core + listing:
        assert (ROOT / path).read_text() == git_show(path), f"{path} changed"


def test_start_and_stop_assessment_and_the_recording_session_code_are_unchanged():
    path = "ios/KemetFCTracker/Sources/App/AssessmentSessionViewModel.swift"
    before = git_show(path)
    for signature in ("public func startAssessment(mode: TrackingMode, speedProfile: TrackingSpeedProfile) async {",
                      "public func stopAssessment() async {", "public func confirmPlayer(_ player: KemetPlayerSummary) {",
                      "private func wireTracking(", "public func clearFinishedRecording() {", "public func retryUpload(recordId: String) async {",
                      "public func handleAppDidBecomeActive() async {"):
        assert function_body(VM, signature) == function_body(before, signature), signature


def test_the_only_changes_to_the_shared_session_code_are_the_import_hunks():
    diff = subprocess.run(["git", "diff", "--no-color", "-U0", BASE, "--", "ios/KemetFCTracker/Sources/App/AssessmentSessionViewModel.swift"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout
    lines = [l for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    removed = [l[1:].strip() for l in lines if l.startswith("-")]
    added = [l[1:].strip() for l in lines if l.startswith("+")]
    # what was removed: only the live-tracking model fields (made optional / made conditional) and the two
    # constant upload strings (made a function of the record)
    allowed_removed = ("let player_detector_version: String", "let ball_detector_version: String", "let pose_model_version: String",
                       "let tracker_algorithm_version: String", "let framing_algorithm_version: String", "gimbal_model:",
                       "player_detector_version:", "ball_detector_version:", "ball_model_status:", "pose_model_version:",
                       "tracker_algorithm_version:", "framing_algorithm_version:", 'locationId: "kemetfc_ios_field_capture",',
                       'captureDevice: "iPhone (KemetFCTracker)",')
    assert removed and all(any(l.startswith(a) for a in allowed_removed) for l in removed), removed
    # what was added: every non-comment line is one of the import-aware forms
    allowed_added = ("let player_detector_version: String?", "let ball_detector_version: String?", "let pose_model_version: String?",
                     "let tracker_algorithm_version: String?", "let framing_algorithm_version: String?", "let wasTrackedLive",
                     "gimbal_model: wasTrackedLive", "player_detector_version: wasTrackedLive", "ball_detector_version: wasTrackedLive",
                     "ball_model_status: wasTrackedLive", "pose_model_version: wasTrackedLive", "tracker_algorithm_version: wasTrackedLive",
                     "framing_algorithm_version: wasTrackedLive", "guard !record.awaitingCoachSave else { return }",
                     "private static func uploadLocationId", "private static func uploadCaptureDevice", "record.isImportedVideo ?", "}", "",
                     "locationId: Self.uploadLocationId(for: record),", "captureDevice: Self.uploadCaptureDevice(for: record),")
    code_added = [l for l in added if not l.startswith("//")]
    assert all(any(l.startswith(a) for a in allowed_added) for l in code_added), [l for l in code_added if not any(l.startswith(a) for a in allowed_added)]


def test_the_camera_recording_values_sent_to_the_server_are_exactly_what_they_were():
    body = code(VM)
    assert 'record.isImportedVideo ? "kemetfc_ios_imported_video" : "kemetfc_ios_field_capture"' in body
    assert 'record.isImportedVideo ? "iPhone (KemetFCTracker) imported video" : "iPhone (KemetFCTracker)"' in body
    for live in ("PlayerDetector.detectorVersion", "ballDetector.modelIdentifier.version", "ballDetector.ballModelStatus",
                 "PlayerDetector.poseModelVersion", "PlayerTracker.algorithmVersion", "FramingCalculator.algorithmVersion"):
        assert f"wasTrackedLive ? {live} :" in body, live
    assert "let wasTrackedLive = !record.isImportedVideo" in body


# ---- one pipeline, not two --------------------------------------------------------------------------------------------

def test_an_imported_video_uses_the_existing_pending_record_and_the_existing_upload_code():
    body = code(APP)
    assert "Task { await session.retryUpload(recordId: importedId) }" in body          # the same upload entry a recording uses
    assert "PendingAssessmentRecord(" in code(SERVICE) and "store.upsert(record)" in code(SERVICE)
    for second in ("URLSession", "KemetAPIClient", "apiClient", "VideoUploadManager", "MultipartVideoUploader", "uploadVideo(", ".post(", "upload(for"):
        for name, text in NEW.items():
            assert second not in code(text), f"{name} must not carry its own upload code ({second})"
    assert "class VideoUploader" not in "".join(NEW.values())


def test_the_unsaved_import_is_never_uploaded_by_any_path():
    assert "if awaitingCoachSave { return false }" in code(STORE)
    assert 'public var awaitingCoachSave: Bool { videoSource == "IMPORTED_VIDEO" && coachSavedAt == nil }' in code(STORE)
    assert "guard !record.awaitingCoachSave else { return }" in code(VM)
    # the two records the automatic paths read both go through isRetryable
    assert "records.filter { $0.isRetryable }" in code(STORE)
    assert "$0.uploadState == .recording || $0.isRetryable || $0.uploadState == .uploading" in code(STORE)


def test_saving_marks_the_record_then_hands_it_to_the_existing_upload_and_cancel_only_removes_the_fresh_copy():
    body = code(APP)
    assert body.index("importer.markSaved(recordId: importedId)") < body.index("session.retryUpload(recordId: importedId)")
    assert "importer.discardUnsaved(recordId: importedId)" in body
    svc = code(SERVICE)
    assert "record.isImportedVideo, record.coachSavedAt == nil" in svc and "store.deleteRecord(id: recordId)" in svc


# ---- player, durability, hash, no transcoding ----------------------------------------------------------------------

def test_the_selected_player_is_the_only_source_of_the_player_id_never_a_file_name():
    svc = code(SERVICE)
    assert "playerId: player.playerId" in svc and "playerName: player.fullName" in svc
    for inference in ("lastPathComponent", "deletingPathExtension", "split(separator", "playerId = ", "searchPlayers", "findByJerseyNumber"):
        assert inference not in svc, inference
    # the player is chosen before the source screen can even appear
    assert "} else if let player = session.confirmedPlayer, videoFirst.isActive, videoFirst.sourceChoice == nil {" in code(APP)
    assert code(APP).index("PlayerSelectionView(") < code(APP).index("VideoSourceChoiceView(") < code(APP).index("CameraPreviewRepresentable(capture:")


def test_the_media_is_never_recompressed_trimmed_or_read_into_memory_at_once():
    for name, text in NEW.items():
        body = code(text)
        for banned in ("AVAssetExportSession", "AVAssetWriter", "AVAssetReader", "AVVideoCompression", "exportAsynchronously",
                       "presetHighest", "preferredItemEncoding: .compatible", "preferredItemEncoding: .automatic",
                       "Data(contentsOf", "readDataToEndOfFile", ".mappedIfSafe"):
            assert banned not in body, f"{name}: {banned}"
    assert "preferredItemEncoding: .current" in code(CHOICE)
    assert "upToCount: 4 * 1_048_576" in code(SERVICE) and "FileManager.default.copyItem" in code(SERVICE)
    assert "FileManager.default.moveItem(at: stagedURL, to: destination)" in code(SERVICE)
    assert "hasher.update(data: chunk)" in code(SERVICE)


def test_the_import_files_never_touch_camera_recording_tracking_telemetry_dockkit_or_the_network():
    for name, text in NEW.items():
        body = code(text)
        for word in ("AVCapture", "CameraCaptureManager", "TrackingCoordinator", "DockKit", "Telemetry", "startAssessment", "stopAssessment",
                     "startRecording", "stopRecording", "finalizeAndUpload", "RecordingIntegrity"):
            assert word not in body, f"{name} uses {word}"
        assert not re.search(r"https?://", body), name
    assert not re.search(r"https?://", code(STORE))


def test_the_review_screen_is_honest_about_what_has_not_happened():
    text = (code(MODEL) + code(REVIEW)).lower()
    for word in ("rating", "score", "talent", "km/h", "m/s", "waiting", "processing", "estimate", "analysed"):
        if word == "analysed":
            continue                                                         # "cannot be analysed" is an integrity reason
        assert word not in text, word
    assert 'static let analysisLabel = "NOT AVAILABLE YET"' in code(MODEL)
    for needed in ('"Video source"', '"Duration"', '"Resolution"', '"File size"', '"Video status"', '"SAVE ASSESSMENT"', '"Cancel"'):
        assert needed in code(REVIEW), needed


def test_the_camera_recording_integrity_rule_is_not_applied_to_an_imported_video():
    assert "RecordingIntegrity" not in code(SERVICE) + code(MODEL)
    assert "wallSeconds" not in code(SERVICE) + code(MODEL)


# ---- the flow -----------------------------------------------------------------------------------------------------------

def test_both_sources_are_offered_and_record_video_hands_over_to_the_existing_camera_screen_unchanged():
    choice = code(CHOICE)
    assert 'Text(title).bold()' in choice and '"RECORD VIDEO"' in choice and '"UPLOAD VIDEO"' in choice
    assert "mode.chooseRecording()" in choice
    assert '"Photo Library"' in choice and '"Files"' in choice
    assert ".photosPicker(" in choice and ".fileImporter(" in choice
    app = code(APP)
    # the camera branch is the same one as before: it only runs once the source is not "choose" (or no mode at all)
    assert app.count("CameraPreviewRepresentable(capture: session.cameraCaptureManager)") == 1
    assert app.count("await session.startAssessment(mode: .smartSoccer, speedProfile: .sport)") == 2
    assert "await session.stopAssessment()" in app


def test_record_assessment_and_the_classic_flow_still_skip_the_source_choice():
    mode = code(MODE)
    assert "func chooseRecording() { sourceChoice = .cameraRecording }" in mode
    assert "videoFirst.isActive, videoFirst.sourceChoice == nil" in code(APP)       # inactive (RECORD ASSESSMENT) never matches
    for persistence in ("UserDefaults", "FileManager", "Keychain", "CoreData", "Codable"):
        assert persistence not in mode, persistence


def test_the_staging_folder_is_cleaned_and_the_cancel_button_cancels_the_import():
    assert "VideoImportService.cleanStaging()" in code(CHOICE) and "importTask?.cancel()" in code(CHOICE)
    assert "removeItem(at: staged)" in code(CHOICE)


def test_pending_uploads_says_an_unsaved_import_has_not_been_saved_or_uploaded():
    view = (IOS / "UI/PendingUploadsView.swift").read_text()
    assert "record.awaitingCoachSave" in view and "not saved yet, not uploaded" in view and '"NOT SAVED"' in view


def test_no_new_permission_prompt_is_needed_and_none_was_added():
    project = (ROOT / "ios/KemetFCTracker/project.yml").read_text()
    assert "NSPhotoLibrary" not in project           # the system Photos picker reads only what the coach selects
