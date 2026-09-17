# Xcode + Physical Hardware Verification Checklist

**Phase 3 status: Build is DONE and genuinely passing** (real Xcode
27.0, real device SDK — see README.md's "Verification status" and
"Phase 3: real build errors found and fixed" for exactly what that
means and the ~15 real errors fixed to get there). **Everything from
Simulator onward remains PHYSICAL FIELD VALIDATION NOT COMPLETED** — no
iPhone, no Flow 2 Pro were available in this environment, and no
Simulator run is possible for THIS app at all (see below). This is the
exact remaining sequence to run through on real hardware, structured as
requested: Stage A (Basic iPhone) → B (Flow 2 Pro) → C (Player Lock) →
D (Ball) → E (Smart Soccer).

## Build — ✅ DONE in this environment

- [x] `xcodegen generate` from `ios/KemetFCTracker/project.yml` — done,
      produces a real `KemetFCTracker.xcodeproj` (gitignored; regenerate
      with this command, do not hand-edit the generated project).
- [x] `xcodebuild build -sdk iphoneos -destination 'generic/platform=iOS'`
      — **BUILD SUCCEEDED**, 1 benign warning (AppIntents metadata,
      irrelevant — this app doesn't use AppIntents). Took ~15 real fixes
      across ~10 build/fix/rebuild cycles; see README.md for the full
      list (actor-isolation errors, missing Sendable conformances, a
      wrong Optional assumption on a real DockKit API, an iOS-18-only
      API called without an availability guard, an async API called as
      if synchronous, and more).
- [x] `xcodebuild build-for-testing` for the same scheme — **TEST BUILD
      SUCCEEDED**: `KemetFCTrackerTests.xctest` (all 4 files —
      `FramingMathTests`, `TapToLockConverterTests`,
      `PlayerTrackerTests`, `BallBlobDetectorTests`) compiles and links
      via `@testable import KemetFCTracker` against the real SDK.
- [ ] `⌘U` / actually RUNNING those tests — **NOT DONE**. Requires
      either a Simulator (impossible for this app — see below) or a
      connected physical device. Every one of these 4 files' assertions
      already passed as a standalone `swiftc`-compiled executable
      outside Xcode (see each file's header for exact pass counts and,
      for PlayerTracker, the 3 real bugs that run found); running them
      through Xcode's actual XCTest machinery on a device is a
      different code path that could theoretically diverge and has
      never been checked.

## Simulator — ❌ IMPOSSIBLE FOR THIS APP, not just unavailable here

`import DockKit` fails to resolve when building for the
`iphonesimulator` SDK — confirmed by actually attempting a Simulator
build in this environment (separately from the fact that no Simulator
runtime could even be installed here: it needs 8.07GB and only 7.4GB
was free). This is not a temporary constraint: DockKit is a
physical-accessory framework with no Simulator-side implementation at
all, on any Mac. Every remaining verification step requires a real
iPhone. Do not spend time trying to get this running in Simulator.

## Stage A — Basic iPhone, no gimbal

- [ ] Camera permission prompt appears on first launch; denying it
      surfaces `lastError` in the UI rather than crashing.
- [ ] `CameraCaptureManager.selectBestFormat` picks a real resolution/
      frame rate — confirm `activeFormatDescription` and
      `activeVideoDimensions` are sane (non-zero, matching the actual
      negotiated format, not a hard-coded guess).
- [ ] Live camera preview renders in `AssessmentTrackingView`.
- [ ] Tap a real person on screen: confirm the actual tapped person
      locks, NOT frame-center and NOT whoever happens to be closest —
      this specifically exercises the Phase 2 fix wiring
      `TapToLockConverter` through `SpatialTapGesture` →
      `TrackingCoordinator.lockPlayer`. Tap empty space away from
      everyone: confirm NOTHING locks (previously this silently locked
      the nearest person regardless of distance — confirm that bug is
      actually gone on a real device, not just in the standalone test).
- [ ] `PlayerDetector.detectPose` returns non-empty keypoints for the
      locked player — confirms `VNDetectHumanBodyPoseRequest` works and
      `jointName.rawValue.rawValue` (flagged as one of the least
      independently-verified lines in this codebase) produces sane
      joint name strings, not garbage.
- [ ] Start Assessment records a real, playable `.mov` file even with
      tracking/inference running simultaneously.
- [ ] Force an inference failure (e.g. cover the camera) mid-recording
      — confirm the recording keeps running (spec: "Never drop the
      assessment video simply because ML inference temporarily fails").
- [ ] Stop Assessment: confirm the recording actually uploads via
      `POST /videos/upload` (watch `videoUploadState` — PREPARING →
      UPLOADING_VIDEO(progress) → PROCESSING) and the tracking session
      completes. This exercises `VideoUploadManager`, which has never
      sent a byte over a real network before this test.
- [ ] Kill the network mid-upload (airplane mode) — confirm the upload
      retries with backoff for transport errors, and confirm a
      genuinely invalid request (e.g. corrupt the metadata to test a
      4xx) does NOT retry.
- [ ] Field Test Mode overlay shows plausible, non-zero
      `Inference FPS` and non-zero per-stage timings (Player/Ball/Pose/
      Total ms) — confirms the Phase 2 performance instrumentation is
      measuring something real, not always reporting 0.
- [ ] Drain the battery below 15% (or simulate via a low-battery test
      device) with the phone unplugged, then try Start Assessment —
      confirm the pre-flight check blocks it with a clear message
      rather than silently starting and failing mid-recording. Fill
      device storage near-full and repeat for the storage pre-check.
- [ ] Leave the app running 10+ minutes outdoors in direct sun. Confirm
      Field Test Mode's "Thermal state" line progresses past `nominal`
      under real thermal load, confirm inference visibly slows (wider
      effective interval) rather than the app crashing or freezing, and
      confirm recording is NEVER stopped by this app itself.

## Stage B — Real iPhone + real Insta360 Flow 2 Pro, DockKit Test Mode

Open `DockKitTestModeView` (NOT the soccer tracking screen) for this
stage — it exercises DockKit in isolation, with no player/ball tracking
logic that could obscure which layer a problem is in.

- [ ] Mount the iPhone on the Flow 2 Pro, power it on. Confirm
      `DockKitManager.connectionState` transitions to `GIMBAL CONNECTED`
      (`DockAccessory.State.docked` actually fires) and
      `GimbalDiagnosticsView` shows a real name/battery/firmware —
      not placeholders.
- [ ] `DockAccessory.identifier.category == .trackingStand` — Phase 2
      resolved this as "the only category that exists," so this should
      always match any DockKit accessory; confirm it actually does on
      the real Flow 2 Pro (a mismatch here would mean Apple's API
      surface has changed since this was verified against their docs).
- [ ] Tap "Attach Test Controller", then "Center Gimbal" — confirm the
      Flow 2 Pro physically moves to its center orientation. This is
      the FIRST real-hardware confirmation that this app can move the
      gimbal AT ALL.
- [ ] Tap "Move Target: Left" / "Center" / "Right" — confirm the gimbal
      physically rotates toward each corresponding position.
- [ ] Toggle "Stream synthetic sweep" — confirm smooth, continuous
      gimbal motion following the sweep, and "Stop" halts it cleanly.
- [ ] Force a DockKit error (e.g. undock mid-stream) — confirm the
      Test Mode screen's "Last error" field shows the REAL thrown error
      text, not a swallowed/generic failure.
- [ ] Undock and re-dock the accessory — confirm `DockKitManager`
      transitions through `RECONNECTING` before settling on
      `CONNECTED` or `DISCONNECTED` (3-attempt grace period).

## Stage C — Player Lock (soccer tracking screen, gimbal attached)

- [ ] Start Assessment in PLAYER_LOCK mode with one person walking left/
      right. Confirm the gimbal PHYSICALLY ROTATES to keep them framed
      — this is `GimbalController.updateTarget` →
      `DockAccessory.track(_:cameraInformation:)` actually working
      end-to-end from real Vision detections, not synthetic Test Mode
      observations.
- [ ] Confirm motion is smooth, not jerky/oscillating — tune
      `FramingCalculator`'s dead-zone/damping defaults against what
      actually feels right on this specific gimbal.
- [ ] Switch to SPORT speed profile, have the person jog laterally —
      confirm the gimbal keeps up without excessive lag or
      overshoot/oscillation.
- [ ] Walk out of frame and back in after ~2 seconds — confirm
      LOCKED → TEMPORARILY_LOST → LOCKED (reacquired), not a jump to a
      different person if one is in frame.
- [ ] Walk out of frame for 10+ seconds (past both timeout thresholds)
      — confirm LOCKED → TEMPORARILY_LOST → SEARCHING → LOST, and that
      the "PLAYER LOST — TAP TO RESELECT" banner appears and a matching
      detection does NOT silently re-lock without an explicit tap
      (this exact bug was found and fixed in `PlayerTracker.swift` this
      session via a standalone test — confirm the fix holds on-device).
- [ ] Have a second, similarly-dressed person cross between the camera
      and the locked player — confirm tracking stays on the ORIGINAL
      player. This is `PlayerTracker`'s appearance-discriminativeness
      reweighting real test; it passed a synthetic standalone test but
      has never seen a real camera frame.
- [ ] Disconnect the gimbal (or force several consecutive `track()`
      failures) mid-assessment — confirm the "GIMBAL CONTROL LOST —
      RECORDING CONTINUES" banner appears, recording is unaffected, and
      gimbal motion does NOT resume on its own reconnect — only after
      tapping "Resume Gimbal Control".

## Stage D — Ball

- [ ] Confirm the "BALL: EXPERIMENTAL (NO TRAINED MODEL)" badge is
      visible and honest — `ClassicalCVBallDetector` is a real but weak
      classical-CV heuristic, not a trained model (see
      `Sources/Vision/BallBlobDetector.swift`).
- [ ] Roll a real soccer ball through frame on grass — confirm the
      classical detector picks it up AT ALL some meaningful fraction of
      the time (no specific accuracy target claimed; this is a sanity
      check, not a benchmark).
- [ ] Confirm it also produces plausible FALSE POSITIVES on other
      bright round-ish objects (a white cone, a sock, a chalk-line
      intersection) — this is the documented, expected weakness, not a
      bug to "fix" by tightening thresholds blindly (that trades false
      positives for false negatives).
- [ ] BALL_TRACK mode: confirm framing follows the ball reasonably when
      detected, and does something sane (holds last position / does
      nothing) when not detected — never crashes.

## Stage E — Smart Soccer

- [ ] SMART_SOCCER mode with both a player and a ball in frame: confirm
      framing keeps both in view, and that losing the ball briefly does
      NOT cause the gimbal to drop the player to go "hunt" for it
      (`TrackingCoordinator.computeFramingTarget`'s `.smartSoccer` case
      falls back to the player box alone).
- [ ] Test Center Gimbal, Pause Tracking, Switch Tracking Mode mid-
      assessment — confirm each manual override takes effect
      immediately and predictably.
- [ ] Run a full ~15-20 minute mock assessment end-to-end: select
      player → connect → lock → record → stop → upload → confirm the
      tracking session appears at `/tracking-analysis` in the web
      dashboard with real telemetry, the correct `ball_model_status`,
      and (if a coach used the confirmation prompt)
      a `correct_player_tracked` label.

## After all of the above pass

- [ ] Multiple real coaches/players, varied jersey colors, varied field
      lighting (morning/midday/evening) — stress-test the appearance
      signature's documented weakness (two players in identical kit
      look identical to this non-biometric signature).
- [ ] Replace `ClassicalCVBallDetector` with a real trained
      `CoreMLBallDetector` once one exists (see that file's inline
      integration instructions), flip `ball_model_status` to
      `"installed"` only once that model is actually wired in and
      tested, and re-run Stage D/E.
- [ ] Build the native login view and QR check-in flow (explicitly
      out of scope for this pass — see README.md "Known gaps") before
      relying on `coachIdentifier` for real attribution.
