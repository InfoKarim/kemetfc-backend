# Xcode + Physical Hardware Verification Checklist

Nothing in this checklist has been run in this session — no full Xcode,
no iPhone, no Flow 2 Pro were available. This is the exact sequence to
run through on your own machine/devices before trusting this code.

## Stage 0 — Build

- [ ] Follow `README.md`'s "Setting up the real Xcode project" section.
- [ ] `⌘B` in Xcode. Fix every compile error. Expect at least the
      "Known gaps" items in the README to need real code, not just a
      config fix.
- [ ] Run `⌘U` (unit tests) — confirm `FramingMathTests` all pass in
      Xcode's own XCTest runner (they already passed as a standalone
      `swiftc` executable in this session; this step confirms they also
      pass through the actual XCTest machinery, which is a different
      code path).

## Stage 1 — Simulator (proves UI/logic wiring only — DockKit/camera do NOT work in Simulator)

- [ ] App launches, `PlayerSelectionView` appears.
- [ ] Search returns players once `searchPlayers` is wired to a real
      `GET /players?search=...` call.
- [ ] Confirming a player transitions to the "Connect gimbal / Start
      Assessment" screen.
- [ ] `DockKitManager.connectionState` correctly shows
      `GIMBAL NOT FOUND` (Simulator has no dock hardware — this is the
      expected, not a bug).

## Stage 2 — Real iPhone 17 Pro Max, no gimbal

- [ ] Camera permission prompt appears on first launch; denying it
      surfaces `lastError` in the UI rather than crashing.
- [ ] `CameraCaptureManager.selectBestFormat` picks a real resolution/
      frame rate — log or breakpoint `activeFormatDescription` and
      `effectiveFrameRate` and confirm they're sane (not 0, not a
      hard-coded fallback).
- [ ] Live camera preview renders in `AssessmentTrackingView`.
- [ ] `PlayerDetector.detectPeople` draws a green box around at least
      one real person standing in frame — confirms Vision inference
      actually runs on-device.
- [ ] `PlayerDetector.detectPose` returns non-empty keypoints for the
      locked player — confirms `VNDetectHumanBodyPoseRequest` works and
      `jointName.rawValue.rawValue` (flagged as the least-verified
      single line in this codebase — see `PlayerDetector.swift`) produces
      sane joint name strings, not garbage.
- [ ] Tap-to-lock actually locks the tapped person, not a different one
      — this requires fixing the `NormalizedPoint(x: 0.5, y: 0.5)`
      placeholder in `AssessmentTrackingView` first (see README "Known
      gaps").
- [ ] Start Assessment records a real, playable `.mov` file even with
      tracking/inference running simultaneously.
- [ ] Force an inference failure (e.g. cover the camera) mid-recording
      — confirm the recording keeps running (spec requirement: "Never
      drop the assessment video simply because ML inference temporarily
      fails").
- [ ] Field Test Mode overlay shows a plausible, non-zero
      `Inference FPS`.
- [ ] Leave the app running 10+ minutes outdoors in direct sun — check
      `ProcessInfo.processInfo.thermalState` (not yet wired into the UI
      — add this before a real field trial) and confirm the app doesn't
      crash or freeze under thermal pressure.

## Stage 3 — Real iPhone 17 Pro Max + real Insta360 Flow 2 Pro (the part that actually matters)

This is the stage nothing in this session could touch at all.

- [ ] Mount the iPhone on the Flow 2 Pro, power it on. Confirm
      `DockKitManager.connectionState` transitions to `GIMBAL CONNECTED`
      (`DockAccessory.State.docked` actually fires).
- [ ] Confirm `DockAccessory.identifier.category == .trackingStand`
      really matches the Flow 2 Pro — if Insta360 reports a different
      category, `DockKitManager.handle(stateChange:)`'s guard will
      silently ignore it. **This is an unverified assumption and the
      single most likely reason this whole pipeline could silently do
      nothing on real hardware.**
- [ ] Start Assessment in PLAYER_LOCK mode with one person walking left/
      right. Confirm the gimbal PHYSICALLY ROTATES to keep them framed.
      This is the actual test of `GimbalController.updateTarget` →
      `DockAccessory.track(_:cameraInformation:)` — nothing before this
      point proves the accessory ever receives or acts on an
      observation.
- [ ] Confirm motion is smooth, not jerky/oscillating — tune
      `FramingCalculator`'s `deadZoneRadius`/`dampingFactor` defaults
      against what actually feels right on this specific gimbal.
- [ ] Switch to SPORT speed profile, have the person jog laterally —
      confirm the gimbal keeps up without excessive lag AND without
      overshoot/oscillation. Tune `GimbalController.applySpeedProfileLimits`'s
      `maxSpeedRadiansPerSecond` constants against reality.
- [ ] Walk out of frame and back in after ~2 seconds — confirm
      `PLAYER_LOCK` → `TEMPORARILY_LOST` → `REACQUIRED` (not a jump to a
      different person, if a second person is in frame).
- [ ] Have a second, similarly-dressed person cross between the camera
      and the locked player — confirm tracking stays on the ORIGINAL
      player (this is the `PlayerTracker` motion+IoU+appearance gating
      logic's real test — it type-checked but was never executed).
- [ ] Test SMART_SOCCER mode with a ball: confirm framing keeps both
      player and ball in frame, and that losing the ball briefly doesn't
      cause the gimbal to drop the player to go "hunt" for it (spec
      requirement, `TrackingCoordinator.computeFramingTarget`'s
      `.smartSoccer` case).
- [ ] Test Center Gimbal, Pause Tracking, Switch Tracking Mode — confirm
      each manual override takes effect immediately and predictably.
- [ ] Confirm `DockAccessory.batteryStates`/`firmwareVersion` report
      real, sane values in Field Test Mode, not placeholder/garbage
      data.
- [ ] Run a full ~15-20 minute mock assessment end-to-end: select
      player → connect → lock → record → stop → upload → confirm the
      tracking session appears at `/tracking-analysis` in the web
      dashboard with a real heat map and metrics.

## Stage 4 — Only after Stage 3 passes

- [ ] Multiple real coaches/players, varied jersey colors, varied field
      lighting (morning/midday/evening) — stress-test the appearance
      signature's known weakness (documented in `PlayerTracker.swift`:
      "two players on the same team look identical to this").
- [ ] Replace `ClassicalCVBallDetector` with a real trained model
      (`CoreMLBallDetector`) once one exists, and re-run the SMART_SOCCER
      checks above — the classical fallback is explicitly not expected
      to perform well enough for production ball tracking.
