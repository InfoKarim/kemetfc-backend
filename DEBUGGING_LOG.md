# Recording-termination bug — debugging log

## Symptom

Normal production assessment: `AVCaptureMovieFileOutput` starts recording, `didStartRecordingTo`
fires, then within ~0.2–0.6s the recording spontaneously terminates:

```
AVFoundationErrorDomain -11818 "Recording Stopped"
underlying NSOSStatusErrorDomain -16414
```

`AVCaptureSession.isRunning` stays `true` throughout. The resulting `.mov` is ~0.05–0.23s for
an assessment that ran tens of seconds. Commit `83af6e3`'s recording-integrity check correctly
rejects these files (never uploads, never deletes, preserves them) — that protection was never
touched during this investigation.

## Method

Physical-device-only (Simulator is impossible — DockKit). Two phases:

1. **Isolation controls (A–I):** progressively rebuilt the production pipeline from a bare
   `AVCaptureSession + AVCaptureMovieFileOutput` up to the full real object graph, adding one
   variable at a time, using either hand-built stand-ins or (from Control H onward) the real,
   unmodified `CameraCaptureManager`/`TrackingCoordinator`/`FrameForwarder`/`TelemetryUploader`
   classes. All of the following were individually proven **not** to reproduce the failure:
   - true 60 fps vs true 30 fps
   - `.inputPriority` vs `.high` session preset
   - HEVC vs H.264
   - `AVCaptureVideoDataOutput` present vs removed
   - the real backend `POST /tracking/sessions` call, mid-recording
   - the real `TelemetryUploader.start()` (timer + queue read)
   - the real `TrackingCoordinator`/`GimbalController`/`PlayerDetector`/`ClassicalCVBallDetector`
     object graph, constructed but never fed frames
   - the real `CameraCaptureManager` alone, in the exact original bug configuration
   - real live frame delivery through the real `FrameForwarder` → `TrackingCoordinator
     .processFrame()` → Vision + `CIContext` ball detection + tracking/framing, for 15+ seconds,
     with a **static** SwiftUI view (no view transition)
   - DockKit attach — ruled out by code inspection alone (the gimbal was never connected during
     any failed recording, so `GimbalController.attach()` was never reached)

2. **In-place instrumentation of the real app:** additive-only `AssessmentDiag.log(...)` calls
   (one monotonic `DispatchTime` clock, reset at `startAssessment()` entry) added around every
   meaningful operation in the real `AssessmentSessionViewModel.startAssessment()`/
   `wireTracking()`, `CameraCaptureManager`'s delegate callbacks, and
   `AssessmentCameraView`/`CameraPreviewRepresentable`. No production ordering, camera setting,
   or logic was changed — only log statements were added. Four independent real-device runs were
   captured this way.

## Root cause (confirmed)

**File:** `ios/KemetFCTracker/Sources/UI/AssessmentCameraView.swift`, `CameraPreviewRepresentable
.makeUIView` (`view.videoPreviewLayer.session = session`).

**File:** `ios/KemetFCTracker/Sources/App/KemetFCTrackerApp.swift`, `RootView.body` — the
condition `trackingCoordinator != nil` deciding when `AssessmentCameraView` (and therefore its
`CameraPreviewRepresentable`) enters the view tree.

**Mechanism:** `trackingCoordinator` only becomes non-nil inside `wireTracking()`, which only
runs from inside `startAssessment()` **after** `cameraCaptureManager.startRecordingAndWait(to:)`
has already confirmed `AVCaptureMovieFileOutput` is recording. So the coach's "connect gimbal /
press Start Assessment" screen has **no camera preview at all** — `AssessmentCameraView` (and
with it, the app's only `AVCaptureVideoPreviewLayer`) is created for the first time only once
recording is already active.

Creating a brand-new `AVCaptureVideoPreviewLayer` and assigning it a running `AVCaptureSession`
(`view.videoPreviewLayer.session = session`) is not a cheap reference assignment — it is real,
synchronous AVFoundation work. Measured directly, in production, on the main thread:

| Run | Preview attach duration | Recording active during attach? | Result |
|---|---|---|---|
| 1 | 235ms (0.197s→0.432s) | yes | -11818/-16414, 19ms later |
| 2 | 248ms (0.269s→0.517s) | yes | -11818/-16414, 29ms later |
| 3 (pre-attach test, different layer instance, before recording) | 274ms | **no** | **no failure** |
| 3 (AssessmentCameraView's own, separate, later attach) | 235ms (0.386s→0.621s) | yes | -11818/-16414, 19ms later |
| 4 | 218ms (0.197s→0.415s) | yes | -11818/-16414, 10ms later |

Every attach that happened while `AVCaptureMovieFileOutput` was actively recording was followed
by `-11818`/`-16414` within 10–29ms — 4 for 4, with zero exceptions. The one attach captured
while recording was **not** yet active (a diagnostic pre-attach added mid-investigation)
completed in a comparable ~275ms and was followed by **no failure at all** — ruling out a
one-time process-wide "cold start" cost and instead showing the delay recurs per new preview-
layer instance, with the outcome depending entirely on whether a movie writer was active during
it. `AVFoundationErrorDomain -11818`'s own recovery suggestion text — *"Stop any other actions
using the recording device and try again"* — is a direct, literal description of this scenario.

**Elimination of alternatives (Phase 6 instrumentation added for this investigation, kept as
observational diagnostics):** across the real failing runs, `AVCaptureSession
.runtimeErrorNotification`, `.wasInterruptedNotification`, and `.interruptionEndedNotification`
**never fired**. The capture graph (`session.inputs`/`outputs`/connections, `enabled`/`active`/
`stabilization`) was byte-for-byte identical immediately before `didStartRecordingTo` and
immediately before the spontaneous `didFinishRecordingTo`. `AVCaptureDevice.systemPressureState`
read `Nominal` throughout. No `beginConfiguration`/`addOutput`/`removeOutput`/`activeFormat`/
frame-duration/stabilization mutation exists anywhere reachable from `startAssessment()`. No
`stopRecording`/`stopRunning` call occurred before the spontaneous failure in any of the four
runs. The symbolic meaning of `-16414` remains formally undocumented by Apple; it was not
speculated on, only correlated.

## The fix

**File:** `ios/KemetFCTracker/Sources/App/KemetFCTrackerApp.swift` (`RootView.body`) +
`ios/KemetFCTracker/Sources/UI/AssessmentCameraView.swift`.

Mount exactly **one** `CameraPreviewRepresentable` (and therefore exactly one
`AVCaptureVideoPreviewLayer`) for the entire lifetime of a confirmed player — including the
"connect gimbal / press Start Assessment" screen, **before** recording ever starts — instead of
recreating it fresh only once `trackingCoordinator` becomes non-nil. SwiftUI preserves a
`UIViewRepresentable`'s backing `UIView`/layer across body re-evaluations as long as it stays at
the same position in the view tree, so this preview's one-time expensive attach now happens
once, safely, before any `AVCaptureMovieFileOutput` is ever recording. The connect-gimbal
prompt, the "recording without tracking" banner, and the full `AssessmentCameraView` HUD/overlay
are now layered on top of that single persistent preview instead of each owning their own.

No camera setting (fps, preset, codec, stabilization, video data output), no recording
timeout/retry logic, no `startAssessment()`/`wireTracking()` ordering, and no part of the
`83af6e3` recording-integrity check were changed.

## Verification

See the physical-device test log below this section as tests complete.

## Test suite status (macOS XCTest harness, unaffected by this fix — no CV/tracking/telemetry
logic changed)

179 tests run: 2 fail (`testM2_decoyAtTargetsOldPositionWhileTargetMissing_isNotReacquired`,
`testM5_targetReversesBeforeDisappearing_predictionFollowsLastVelocity`). **Pre-existing,
already-accepted `PlayerTracker` limitation** (see project memory
`project_tracker_identity_fixes.md`), not a new regression, not recording-related. No other
failures.

## Remaining temporary diagnostics (uncommitted, observational-only, safe to keep or remove)

- `Sources/App/AssessmentDiag.swift`
- `AssessmentDiag.log(...)` calls in `AssessmentSessionViewModel.swift`, `CameraCaptureManager.swift`,
  `TrackingCoordinator.swift`, `AssessmentCameraView.swift`
- `CameraCaptureManager.swift`: `observeSessionNotificationsForDiag()`, `dumpCaptureGraphForDiag(_:)`,
  frame-counting fields

None of these change behavior, ordering, or camera configuration.

## Additional fix found during verification (unrelated to preview-layer bug)

**File:** `ios/KemetFCTracker/Sources/DockKit/GimbalController.swift`, `attach(accessory:)`.

**Symptom:** app force-quits to the home screen the instant "Start Assessment" is tapped
**while a real DockKit accessory (Flow 2 Pro) is actually docked/connected**. Never seen
before in this investigation because no test — mine or the user's — had a real gimbal
connected until now.

**Console evidence (`xcrun devicectl device process launch --console`):**
```
DockKit/DockKitAccessory.swift:1219: Fatal error: API violation: setting limits only
supported when system tracking disabled
```
An uncatchable `fatalError` inside Apple's own DockKit framework, not a throwing Swift error.

**Root cause:** `attach(accessory:)` called `applySpeedProfileLimits()` (→
`accessory.setLimits(...)`) **synchronously, before** the `Task` that calls
`DockAccessoryManager.shared.setSystemTrackingEnabled(false)` had even started running.
`setLimits` requires system tracking to already be disabled.

**Fix:** moved `applySpeedProfileLimits()` inside the same `Task`, **after** `await
setSystemTrackingEnabled(false)` completes, before `setFramingMode(.automatic)`. No behavior
change beyond correct ordering.

---

## Product backlog (do NOT implement until the recording bug is fully verified)

### Player Live Analysis panel (replaces Field Test Mode in production builds)

Requested: replace the large internal "Field Test Mode" diagnostics panel in
`AssessmentCameraView` with a coach-facing "Player Live Analysis" panel for production
builds. Field Test Mode (backend URL, API status, HTTP info, latency, raw FPS, thermal/debug,
model names) must stay debug-only, reachable via a developer-only toggle, never shown in
production UI.

**Target panel:**
- Header: "PLAYER LIVE ANALYSIS", player name.
- **TACTICAL**: Positioning, Scanning/Vision, Space Awareness, Off-ball Movement, Decision
  Making.
- **MOVEMENT**: Activity/Work Rate, Movement intensity, Direction changes, Recovery movement.
- **BALL/TECHNICAL** (only when actually measurable): Ball involvement, Ball control
  observations, Dribbling involvement, possession-related observations.
- **LIVE COACHING INSIGHT**: 1–3 short actionable observations (e.g. "Scan before receiving",
  "Move into the open space").
- Keep the small Tracking/Gimbal/AI status indicators at the top.

**Hard data-integrity rule: no invented scores.** Before implementing each metric, audit the
existing CV pipeline (`PlayerTracker`, `BallTracker`, `PoseAssociator`, `FramingCalculator`,
`FrameTelemetry`) and document, per metric:

`METRIC | SOURCE DATA | CV COMPONENT | FORMULA/RULE | CONFIDENCE REQUIREMENT | MINIMUM
OBSERVATION WINDOW | WHEN TO DISPLAY N/A | LIMITATIONS`

If the current CV cannot reliably support a metric, show "—"/"Collecting data" or omit it —
never fake a score. Separate real-time observations from post-assessment analysis; some
tactical metrics may only be meaningful over several seconds or the full assessment.

This work has not started. No CV/tracking files were audited for this feature yet.
