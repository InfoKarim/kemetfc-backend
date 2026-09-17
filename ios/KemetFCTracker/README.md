# KemetFCTracker — Smart Soccer Camera Tracking (iOS source)

Native iOS companion app for KEMET FC's assessment platform: connects to
an Insta360 Flow 2 Pro over Apple DockKit, tracks the coach-selected
player and the ball, physically pans/tilts the gimbal to keep them
framed, records the assessment, and streams telemetry to the existing
KEMET backend (`app/routers/tracking.py`, in this same repository).

## Verification status — read this first

This code was written in an environment with **no full Xcode install**
(Command Line Tools only — `xcodebuild` errors out immediately) and
**no physical iPhone or Insta360 Flow 2 Pro**. Per-file status:

| File | Status |
|---|---|
| `Sources/Tracking/TrackTypes.swift` | **Compiled AND run.** Pure Foundation. Exercised by `Tests/FramingMathTests.swift`'s assertions (run as a standalone `swiftc`-compiled executable, not via `swift test` — see below). |
| `Sources/Tracking/FramingCalculator.swift` | **Compiled AND run**, 20/20 assertions passing on this machine, including catching and fixing two wrong assumptions in the *test* itself (see file header and `Tests/FramingMathTests.swift`). |
| `Sources/Tracking/PlayerTracker.swift`, `BallTracker.swift` | **Type-checked** (`swiftc -typecheck`, zero errors) — pure Foundation/Combine, no iOS-only framework. Logic not exercised by a test (no unit tests written against the state machine itself — see "Known gaps" below). |
| Everything importing `DockKit`, `AVFoundation`, `Vision`, `CoreImage`, `Spatial`, or `SwiftUI` (`DockKitManager`, `GimbalController`, `CameraCaptureManager`, `PlayerDetector`, `BallDetector`, `TrackingCoordinator`, all of `Sources/UI/`, `Sources/App/`, `Sources/Networking/`) | **Written, NOT compiled.** These frameworks aren't available to `swiftc` outside a real iOS SDK/Xcode. Every DockKit/Vision API signature used was individually verified against Apple's live documentation during this session (see citations in each file's header) — this reduces but does not eliminate the risk of a compile error Xcode would catch immediately. |
| Physical gimbal rotation, DockKit pairing, on-device inference performance, thermal behavior | **Not tested at all.** No physical hardware was available in this environment. |

**Two real bugs were caught by review/compilation before you'd have hit
them in Xcode:**
1. `TrackingCoordinator.computeFramingTarget()` was passing a *position*
   (`predictedCenter(dt:)`) where `FramingCalculator.applyLeadRoom`
   expects a *velocity* — fixed by exposing `PlayerTracker.currentVelocity`
   and using that instead.
2. Every file using `@Published`/`ObservableObject` was missing
   `import Combine` — added to all seven affected files.

Neither of these would have been caught without actually compiling
(the missing import) or carefully re-reading call sites against their
declared parameter semantics (the velocity/position mixup). Expect Xcode
to find more — this is real, unverified source code, not a finished,
tested product.

## Why DockKit API details in the code comments are unusually specific

Every `DockKit`/`Vision` type and method signature referenced in this
module's comments was looked up live against
`developer.apple.com/documentation` during this session (fetched via
its JSON API, since the rendered HTML page is a JS single-page app) —
not recalled from training data alone, because DockKit is new/niche
enough that recalled signatures were checked against reality and in
several cases corrected (e.g. `DockAccessory.Limits.Limit` really takes
`positionRange:maximumSpeed:`, not the `min:max:` first guessed;
`recognizedPoints(_:)` takes its argument positionally, not as
`forGroupKey:`). This is why file headers cite exact iOS version numbers
per API.

## Setting up the real Xcode project

No `.xcodeproj`/`.xcworkspace` exists in this repository — create one:

1. Xcode → File → New → Project → iOS → App. Product name
   `KemetFCTracker`, interface **SwiftUI**, language **Swift**.
   Deployment target **iOS 18.0** (required for `DockAccessory.TrackedPerson`/
   `TrackingStates`, which this module uses in comments/future work;
   the base connection/observation APIs only need iOS 17.0 if you want
   to lower this).
2. Delete the template's generated `ContentView.swift`/`...App.swift`.
3. Drag the `Sources/` folder from this directory into the Xcode
   project navigator (check "Copy items if needed" and add to the
   `KemetFCTracker` target).
4. Add frameworks under the target's *Frameworks, Libraries, and
   Embedded Content*: DockKit, AVFoundation, Vision, CoreImage, Spatial.
   (Combine and Foundation are implicit.)
5. Add Info.plist keys: `NSCameraUsageDescription` (required — this app
   is camera-first) and, since DockKit needs it, confirm your target's
   deployment info includes DockKit accessory support (Xcode surfaces
   this automatically once you link the DockKit framework on a
   sufficiently new SDK).
6. Create a `KemetFCTrackerTests` unit test target (iOS Unit Testing
   Bundle), drag `Tests/FramingMathTests.swift` into it.
7. Set the `KEMET_BACKEND_BASE_URL` environment variable in the scheme's
   Run action (Product → Scheme → Edit Scheme → Run → Arguments →
   Environment Variables) to your backend's URL — `https://app.kemetfc.com`
   for production, or `http://<your-mac-LAN-IP>:8010` for on-field
   testing against a laptop running `uvicorn main:app` from this repo.
8. Build (⌘B). Fix whatever Xcode's real compiler finds — expect some;
   see "Known gaps" below for the ones already anticipated.

## Known gaps to close before this is production-ready

- **Ball detection has no trained model.** `ClassicalCVBallDetector` is
  a documented placeholder (`brightestRoughlyCircularBlob` is an
  unimplemented stub) — see `Sources/Vision/BallDetector.swift`'s header
  for what training/exporting a real Core ML model looks like, and
  `CoreMLBallDetector` for the integration point.
- **`TrackingCoordinator.apply(result:)` uses a placeholder appearance
  signature** (flat gray) for every detected person instead of sampling
  real pixels from each bounding box — wire real sampling in before
  relying on the appearance-similarity term in `PlayerTracker.update`.
- **`AssessmentSessionViewModel.uploadVideo(fileURL:playerId:)` is not
  implemented** — it throws immediately. Needs a standard
  `multipart/form-data` body matching `POST /videos/upload`'s existing
  schema (`app/routers/videos.py:70`, `PlayerVideoUploadMetadataSchema`
  in `app/api_schemas.py`).
- **`AssessmentTrackingView`'s tap-to-lock always reports frame center**
  (`NormalizedPoint(x: 0.5, y: 0.5)`) instead of converting the actual
  tap location — needs
  `AVCaptureVideoPreviewLayer.captureDevicePointConverted(fromLayerPoint:)`
  wired into `CameraPreviewRepresentable`.
- **QR-scan player selection is described in a comment, not
  implemented** (`PlayerSelectionView.swift`).
- No unit tests exist yet for `PlayerTracker`/`BallTracker`'s state
  machine transitions or `TrackingCoordinator`'s mode-switching logic —
  only the pure `FramingCalculator` math has real test coverage.
- `Limits.yaw` range `-π..<π` assumes the Flow 2 Pro's claimed 360°
  pan; confirm against real hardware and Apple's DockKit sample rather
  than trusting this comment's assumption.

## What's already built and tested on the backend side

`app/routers/tracking.py`, `app/services/tracking_service.py`,
`tracking_features.py`, `tracking_quality.py`, `skill_inference.py`,
`heatmap_service.py`, `model_registry_service.py` in this same
repository are real, running, and covered by 38 passing backend tests
(`tests/test_tracking*.py`, `test_skill_inference.py`,
`test_heatmap_service.py`) plus the full 909-test suite passing overall,
against both SQLite and a real disposable PostgreSQL container. This iOS
app is the only unverified layer — the server it talks to is not.
