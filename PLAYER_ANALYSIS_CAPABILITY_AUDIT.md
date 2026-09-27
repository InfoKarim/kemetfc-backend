# Player Live Analysis — Capability Audit

Audit only. No production code changed. Read directly from the current repository state
(commit `618374a` and later, working tree), not from prior summaries.

---

## 1. Current CV architecture — per-component table

| File | Class/Function | Input | Output | Update freq | Confidence? | Player ID? | Ball state? | Pose? | History stored? | Coord system | Limitations |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `Vision/PlayerDetector.swift` | `detectPeople(in:orientation:)` — `VNDetectHumanRectanglesRequest` (Apple built-in) | `CVPixelBuffer` | `[PersonDetection]` (box + confidence), **every** person in frame | Per inference tick (≤15Hz, see below) | Yes (Vision's own) | No — no identity, just boxes | — | — | No — one-shot per call | Normalized image-space, top-left origin (flipped from Vision's bottom-left) | Apple's general person detector, not soccer-specific; no jersey/number OCR; no team classification |
| `Vision/PlayerDetector.swift` | `detectPoses(in:orientation:)` — `VNDetectHumanBodyPoseRequest` (Apple built-in) | `CVPixelBuffer` | `[[PoseKeypoint]]`, every pose Vision finds, **only 12 joints kept**: L/R shoulder, elbow, wrist, hip, knee, ankle | Per inference tick | Per-joint (Vision's own) | No — order is arbitrary, not tied to a person | — | Yes, but restricted set | No | Normalized, top-left origin | **`trackedJointNames` (line 50-57) explicitly excludes `.neck`, `.nose`, `.leftEye`, `.rightEye`, `.leftEar`, `.rightEar`, `.root`** — Vision's own `VNHumanBodyPoseObservation` DOES return these, the app just never asks for them |
| `Tracking/PoseAssociator.swift` | `associate(poses:target:targetIsObserved:otherPeople:)` | poses + selected player's box | one pose or none, geometrically matched | Per inference tick | Match confidence via joint-fraction, not exposed as a number | Uses the tracker's box, not identity itself | — | Yes/no (binary) | No | Normalized | Pure geometry; "no pose is better than the wrong pose" — frequently returns none by design |
| `Tracking/PlayerTracker.swift` | `update(detections:appearanceSignatures:at:)` | one frame's person detections + appearance | mutates `state`, `currentBoundingBox`, `currentConfidence`, `currentVelocity`, `lastMatchWasAmbiguous` | Per inference tick | Yes | **Exactly one player**: `targetTrackId` (always `1`, set at lock time) | — | — | **No rolling history** — only latest values as `@Published`, no buffer of past positions | Normalized image-space | Single-player only; velocity is image-space units/sec, not physical speed (see §7); appearance is mean torso/legs color, explicitly non-biometric |
| `Vision/BallDetector.swift` | `ClassicalCVBallDetector.detectBallCandidates` | `CVPixelBuffer` | `[BallDetection]` — bright, roughly-circular blob candidates | Per inference tick | Yes, but **capped 0.20–0.40** (line 127-129) — deliberately low | — | — | — | No | Normalized | **Not a trained model.** Classical color/circularity heuristic (`BallBlobDetector`); confuses cones, chalk lines, white socks with a ball; `ballModelStatus="fallback_classical"` reported honestly to backend |
| `Tracking/BallTracker.swift` | `update(detections:at:)` | ball candidates | `currentBoundingBox`, `currentConfidence`, `currentTrackId`, `state` | Per inference tick | Yes (inherited from detector, capped low) | Track ID (integer, increments per new track) | Yes: tracked/lost/searching | — | Internal-only: `velocity` (private), decoy memory (private) — **not published, not in telemetry** | Normalized | No possession/touch/pass/shot logic anywhere in this file — only spatial tracking of "a bright circular thing" |
| `Tracking/TrackingCoordinator.swift` | `processFrame` → `runInference` → `apply(result:)` | `CMSampleBuffer` | orchestrates all of the above, builds `FrameTelemetry`, drives framing/gimbal | **Rate-limited to ≤15Hz** (`targetInferenceIntervalSeconds = 1/15`, line 98), widened further under thermal pressure | — | — | — | — | No — only `@Published` "latest" values (`latestDetections`, `lastStageTiming`, etc.) | Normalized | Single point of orchestration; no aggregation across frames beyond instantaneous state |
| `Tracking/FrameTelemetry.swift` | `FrameTelemetryAssembler.assemble` | tracker states | one `FrameTelemetry` struct per processed frame | Per inference tick | Yes (player + ball) | Yes (player track id, ball track id) | Yes | Yes (associated only) | This IS the record sent to the backend, but the **iOS app itself doesn't retain a buffer of these** — each is built, sent to `TelemetryUploader`, and forgotten locally | Normalized | Honest-nil semantics: every field is nil when not truly observed this frame (documented in the file header) |
| `Tracking/FramingCalculator.swift` / `FramingPipeline.swift` | pure math: `combinedFramingTarget`, `applyLeadRoom`, `dampedCorrection` | player/ball boxes + velocity | one framing target rectangle | Per inference tick | — | — | — | — | No | Normalized | Camera-framing math only — not a measurement of the player, a decision about where to POINT the camera |
| `DockKit/GimbalController.swift` | `updateTarget`, `attach` | framing target | moves the physical gimbal | Rate-limited internally | — | — | — | — | No | — | No CV signal of its own; consumes framing target, produces motion — irrelevant to player analysis except as a confound (see §7) |
| `Networking/TelemetryUploader.swift` / `TrackTypes.swift` (`TrackingSample`) | `recordSample` | one `FrameTelemetry` | uploads `TrackingSample` (backend wire format) | Every processed frame, batched every ~2s | Yes | Yes | Yes | Yes | Backend-side only, once acknowledged the local copy is deleted | Normalized (as sent) | **This is the only place per-frame history could exist at all — and it lives on the backend, not the device.** No backend service currently aggregates these into tactical metrics (confirmed: no such endpoint/service exists in `app/services/`) |
| `ActionRecognition/*.swift` | `ActionRecognitionService` → `CoreMLActionClassifier` | a **previously-recorded video file** (offline, not live) | `ActionSegment` timeline: passing/shooting/dribbling/firstTouch/sprinting/standing | Post-hoc, not live | Required field, but... | — | — | Yes (via `ActionPoseExtractor`, offline) | Segments only, no per-frame retained | N/A (post-hoc) | **`CoreMLActionClassifier.isAvailable` is hard-coded `false`; `classify()` always `throws .modelUnavailable`.** No trained model exists anywhere in this repo (confirmed via the file's own header and `ml/DATASET_CARD.md`). This is a real, honest placeholder, not a working feature |
| `UI/PlayerTrackingOverlay.swift`, `UI/FieldTestOverlay.swift` | pure presentation | `TrackingCoordinator`'s published state | on-screen boxes/text | Every SwiftUI redraw | — | — | — | — | No | Screen points (converted from normalized) | `FieldTestOverlay` is the panel this project replaces — it currently exposes backend URL, latency, thermal state, model names: everything Phase 12 says must move behind a debug-only gate |

---

## 2. Current raw signals available on every processed frame

- Selected player: box, confidence, track state (locked/temporarily_lost/searching/reacquired/lost), image-space velocity, ambiguous-match flag.
- Ball: box, confidence (0.20-0.40 ceiling), track id, track state.
- Pose: up to 12 joints (shoulders/elbows/wrists/hips/knees/ankles) **only for the one selected player**, and only when `PoseAssociator` can prove the pose belongs to them.
- Gimbal: connection state, control-lost flag, battery.
- Nothing about any other person's identity/history, nothing about the field, nothing about real-world units.

---

## 3. Capability matrix (A–E)

**A** = available now, reliable · **B** = available now, approximate · **C** = raw data exists, metric not implemented · **D** = needs new CV/model · **E** = not reliably possible with this camera setup

### PLAYER

| Item | Class | Evidence |
|---|---|---|
| Player bounding box | **A** | `PlayerTracker.currentBoundingBox`, committed only on a real detection |
| Selected-player identity/track ID | **B** | `targetTrackId` exists but is always the literal constant `1` (`TrackingCoordinator.lockPlayer`, line 186) — it identifies "the one locked player," not a real re-identifiable ID across sessions |
| Player center position | **A** | `NormalizedRect.center`, derived from the box |
| Player trajectory over time | **C** | Each frame's position is telemetered (`TrackingSample`) but **no code anywhere retains or reconstructs a trajectory** — on-device or backend |
| Movement direction | **B** | `currentVelocity` gives a 2D vector; direction is derivable, but is image-space, confounded by gimbal motion (§7) |
| Movement speed (image coordinates) | **B** | Same `currentVelocity` — units/second in normalized image space, not physical speed |
| Acceleration/deceleration | **C** | Would need a derivative of `currentVelocity` over time — not computed anywhere |
| Distance traveled | **E** (as physical distance) / **C** (as image-space path length) | No field calibration exists (§7); an image-space path-length integral isn't implemented either |
| Stationary vs moving | **B** | Derivable from velocity magnitude — not currently exposed as a discrete state anywhere in the app |
| Direction changes | **C** | Would need velocity-vector-angle deltas over a window — raw velocity exists, the counting logic does not |
| Player confidence | **A** | `PlayerTracker.currentConfidence`, real Vision confidence |
| Lost/reacquired state | **A** | `PlayerTrackState` enum, exhaustively handled |

### POSE

| Item | Class | Evidence |
|---|---|---|
| Body landmarks | **A** (12 joints only) | `PlayerDetector.trackedJointNames` |
| Shoulder positions | **A** | `.leftShoulder`/`.rightShoulder` in the tracked set |
| Hip positions | **A** | `.leftHip`/`.rightHip` in the tracked set |
| Torso orientation | **B** | Derivable from shoulder+hip vector geometry — **not currently computed anywhere**, but the joints exist |
| Body orientation (facing) | **B** | Same as above, coarse proxy only |
| Head/neck landmarks | **E (not requested)** | `.neck`, `.nose` exist in `VNHumanBodyPoseObservation.JointName` per Apple's API but are **excluded** from `trackedJointNames` — see §4 |
| Head orientation | **E** | No head landmarks are ever requested; true head-pose estimation would need `VNDetectFaceRectanglesRequest`/`VNDetectFaceLandmarksRequest` or an explicit head-pose model, neither present |
| Left/right foot landmarks | **B (proxy only)** | Code comment is explicit: *"ankle == feet proxy, no dedicated foot joint in Vision"* (`PlayerDetector.swift:56`) |
| Stance / running posture | **D** | Would require a pose-classification model or hand-built geometric rules over the 12 joints — neither exists |

### BALL

| Item | Class | Evidence |
|---|---|---|
| Ball bounding box | **B** | `BallDetector`/`BallTracker`, but the underlying detector is a classical heuristic, not a trained model |
| Ball center | **B** | Derived from the box |
| Ball confidence | **B** | Explicitly capped 0.20-0.40 by design (`ClassicalCVBallDetector.confidence`) — never claims high certainty |
| Ball trajectory | **C** | Per-frame positions exist in telemetry; nothing aggregates them |
| Ball velocity | **C** | `BallTracker.velocity` is computed **internally for prediction only** — it's `private`, never published, never in `TrackingSample` |
| Ball possession association | **E** | No possession logic exists anywhere in the codebase |
| Distance player↔ball | **B** | Trivial to compute from the two boxes' centers (both available), but **no code currently computes or reports it** |
| Touches | **E** | No event/state machine for this exists |
| Dribble events | **E** | Same |
| Pass events | **E** | Same |
| Shot events | **E** | Same |

### CONTEXT

| Item | Class | Evidence |
|---|---|---|
| Other players | **B** | `TrackingCoordinator.apply` receives `result.people` = every person detected this frame, used only for appearance-collision checks (`hasConfusableNeighbor`) — **not tracked, no per-person history, no IDs** |
| Teammate/opponent classification | **E** | No jersey-color-based team assignment or any classification logic exists |
| Field boundaries / touchlines | **E** | No field-line detection anywhere |
| Goal location / penalty area | **E** | Same |
| Field coordinates | **E** | No homography/calibration exists at all — see §7 |
| Attacking direction | **E** | Would require field calibration first |
| Open space | **E** | Would require multi-player tracking + field geometry, neither exists |
| Pressure from opponents | **E** | Same |

---

## 4. Special review: Scanning / Vision (head-check before receiving)

**Do we have true head orientation? No.**

`PlayerDetector.trackedJointNames` (Vision/PlayerDetector.swift:50-57) requests only: left/right shoulder, elbow, wrist, hip, knee, ankle. It explicitly does **not** request `.neck`, `.nose`, `.leftEye`, `.rightEye`, `.leftEar`, `.rightEar`, or `.root` — even though `VNHumanBodyPoseObservation.JointName` (Apple's real API, confirmed in the file's own header comment) provides all of them. This is a configuration choice already in the code, not a Vision limitation.

Even with those extra joints, `VNDetectHumanBodyPoseRequest` reports **2D image-space landmark positions**, not a 3D head-pose orientation (pitch/yaw/roll). Genuine head-orientation estimation is a distinct capability (`VNDetectFaceRectanglesRequest` + `VNFaceLandmarks2D`, or a dedicated head-pose model) and is not present anywhere in this repo.

**Three levels, what each needs:**

- **LEVEL 1 — body/torso orientation-change proxy.** Achievable with the *currently tracked* joints: shoulder-to-shoulder vector angle over consecutive samples. **Classification: D** (the joints exist, but no code computes this angle or a "changed orientation" event today — it must be built).
- **LEVEL 2 — shoulder-check proxy.** Same shoulder-hip geometry, looking for a rotation of the shoulder line relative to the hip line (torso twist) shortly before the ball arrives. Still no head/face data — this is inferring "player rotated their upper body," not "player looked over their shoulder." **Classification: D.**
- **LEVEL 3 — true head-pose scanning.** Requires either (a) adding `.neck`/`.nose`/`.leftEye`/`.rightEye`/`.leftEar`/`.rightEar` to `trackedJointNames` and building 2D head-yaw-proxy math (still not true 3D orientation, but closer), or (b) a genuine face-landmark/head-pose pipeline (`VNDetectFaceLandmarksRequest`, or a trained head-pose model) plus enough image resolution on the face at typical assessment framing distance (a real, unverified assumption — the player is often far from the camera). **Classification: D**, and the face-based route is likely **E** at typical assessment distances without a dedicated model, since face detail at that range/resolution is not proven usable.

**Bottom line: nothing above Level 1-2 (a torso-rotation proxy) can be honestly claimed as "scanning" today, and even Level 1 requires new code, not just new UI.**

---

## 5. Positioning / Space Awareness

**Do we track multiple players with identity/history? No.**

`TrackingCoordinator.apply` receives `result.people` fresh every inference tick from `PlayerDetector.detectPeople` — a flat, unordered list of boxes with no track IDs, no continuity, and no storage across frames. It's used only for two narrow purposes: (1) computing an appearance signature for the *locked* player's box, and (2) telling `PlayerTracker`/`PoseAssociator` "here are the other people nearby, don't confuse them with the target." There is no `OtherPlayerTracker`, no per-person history, no team assignment.

**Therefore: real tactical positioning relative to teammates/opponents, or genuine "open space" analysis, is not possible with the current system. Do not claim it.**

What **can** be truthfully computed today (all **B/C**, none implemented yet):
- The selected player's own screen-space movement (position/velocity over time) — **B**, raw data exists.
- Distance to the ball, when both are detected in the same frame — **B**, trivial geometry, not implemented.
- Whether the player is staying near vs. far from the ball over a window — **C**.
- Movement into/out of specific camera regions (e.g., "spent more time in the wide areas of frame") — **C**, but this is *camera-frame* geometry, confounded by gimbal panning (§7), not field geometry.
- Relative movement toward the ball (closing distance) — **C**, derivable from the two positions.
- General activity level while the ball is far away — **C**.

These are all single-player, image-space observations — genuinely useful as "how active/engaged was this player," not as "tactical positioning."

---

## 6. Ball possession / touches

**Can we reliably infer possession, touches, dribble, pass, or shot events today? No — none of this exists in the codebase**, and it must not be inferred merely from box overlap (bounding-box overlap between a coarse person box and a low-confidence classical-heuristic ball box is a weak, noisy signal on its own).

Minimum signals a real state machine would need (design only, **not implemented**):
- `distance(player_center, ball_center)` over time — both already available per frame when both are detected.
- Ball velocity **change** (a real touch should show a discontinuity in the ball's trajectory) — requires exposing `BallTracker`'s currently-private `velocity`, or recomputing from consecutive telemetry samples.
- Player/ball co-motion (both moving together at a similar velocity = carrying/dribbling; sudden ball-velocity divergence = released/passed/shot).
- Proximity **duration**, not just instantaneous distance (a fleeting overlap is not possession).
- Explicit entry/exit thresholds (e.g., ball within N body-widths for M consecutive samples = "in possession"; leaves that radius with a velocity spike = "released").
- Pose-based foot proximity (ankle keypoints, already tracked, vs. ball center) — a much stronger touch signal than box overlap alone, and the ankle joints already exist in the current pose set.
- Trajectory discontinuity detection on the ball path (a sharp direction/speed change independent of player position = candidate pass/shot moment).

This is a real, buildable state machine, but it does not exist today and every input above (ball velocity exposure, a distance-over-time buffer, a discontinuity detector) needs new code before any possession/touch claim is trustworthy. **Classification: D** across the board for this whole category.

---

## 7. Camera/gimbal geometry limitations (critical)

**All current measurements are IMAGE-SPACE, not real-world field-space.** There is no field-line detection, no homography, no known field dimensions, and no calibration step anywhere in this codebase (confirmed by exhaustive search — no such file/class exists).

**Consequences, stated plainly:**
- **Do not report meters, mph, or "true" distance covered.** No conversion factor from normalized image units to real-world units exists or is derivable without calibration.
- **Do not compare screen-space velocity across zoom changes as if it were physical speed** — the same physical run produces a different `currentVelocity` magnitude depending on the negotiated camera format/framing.
- **The camera itself moves.** `GimbalController`/`FramingPipeline` actively pan/tilt the physical gimbal to keep the player framed (`dampedCorrection`, `applyLeadRoom`). A real-world stationary player can show nonzero image-space velocity purely from gimbal correction; a real-world sprinting player can show near-zero image-space velocity if the gimbal is successfully tracking them. **`PlayerTracker.currentVelocity` conflates the player's real motion with the camera's own motion — there is no camera-motion compensation anywhere in the pipeline.**
- Any "open space" or positioning calculation using raw image coordinates is really measuring "where in the CURRENT FRAME the player is," which changes as the gimbal reframes — not where they are on the field.

**What would be required, per metric class:**
- Physical distance/speed → field calibration (homography from image to a known field size) + camera-motion compensation using gimbal orientation telemetry (DockKit does expose orientation, but nothing in this codebase reads or uses it for this purpose today).
- True open-space/positioning → the above, plus multi-player tracking (§5) and field-line/marking detection.
- Any comparison across different assessments/zoom levels/framing choices → the same calibration, applied consistently.

**Until field calibration + gimbal-motion compensation exist, every velocity/distance-based metric must be treated as a relative, image-space, camera-motion-confounded signal — described in those terms to the coach, never presented as a physical measurement.**

---

## 8. V1 trustworthy metrics — proposed split

### LIVE NOW (supported by current, real, per-frame signals — descriptive states, no invented scores)
- **Tracking confidence** — direct from `PlayerTracker.currentConfidence` / `playerState`.
- **Movement state** (e.g., "Moving" / "Stationary") — threshold on `currentVelocity` magnitude. New, trivial code; data already exists.
- **Ball visible / not visible**, and **ball confidence band** — direct from `BallTracker`.
- **Ball proximity** ("Near ball" / "Away from ball") — new trivial distance-threshold code over two already-available positions.

### POST-ASSESSMENT NOW (needs new aggregation code over already-uploaded telemetry, no new CV)
- **Activity / time-in-motion** — integrate "moving" state over the session's `TrackingSample`s.
- **Direction-change count** — angle-delta counting over the velocity sequence.
- **Ball involvement time** — fraction of samples where "near ball" was true.
- **Tracking reliability summary** (% of session with a confident lock, number of temporarily-lost episodes) — directly aggregable from existing `trackingStatus`/`identityAmbiguous` fields already stored.

### NEEDS NEXT CV DEVELOPMENT (raw signal exists but needs meaningful new logic)
- Torso-orientation proxy → Level 1 "scanning" proxy (§4).
- Ball velocity exposure + discontinuity detection (foundation for touch detection, §6).
- Distance-player-ball state machine (possession/touch attempt, still coarse).

### FUTURE ADVANCED TACTICAL ANALYSIS (needs new CV/models, larger investment)
- Multi-player tracking with identity/history (§5) — prerequisite for any real positioning/space-awareness/pressure metric.
- Field calibration + gimbal-motion compensation (§7) — prerequisite for any physical distance/speed claim.
- True head-pose scanning (§4, Level 3).
- Trained ball-detection model (replacing the classical heuristic) — prerequisite for trustworthy possession/touch/pass/shot detection.
- Trained action-recognition model (`ActionRecognition/*` — the integration seam exists, no model does).

---

## 9. Proposed `PlayerLiveAnalysisSnapshot` (design only, not implemented)

```swift
struct PlayerLiveAnalysisSnapshot {
    let timestamp: Date
    let playerTrackID: Int              // currently always 1 — honest about the limitation
    let trackingConfidence: Double?     // PlayerTracker.currentConfidence, nil when not observed

    let movementState: MovementState    // .stationary / .moving / .unknown — NEW, trivial threshold logic
    // No movementTrend/intensity live — that needs a time window, better as post-assessment (§8)

    let ballVisible: Bool
    let ballConfidence: Double?
    let ballProximityState: BallProximityState?  // .near / .away / .unknown — NEW, trivial distance logic

    // Explicitly OMITTED from this snapshot, per the audit: ball involvement STATE requires a
    // duration window, not a single frame — belongs in a rolling summary, not this struct.

    let scanningObservation: ScanningObservation?  // .notYetSupported until Level 1 (§4) ships
    let positioningObservation: String?            // free text like "near the ball" — never a score

    let coachingCue: CoachingCue?        // see §11 — only ever a cue with a real trigger
    let analysisConfidence: AnalysisConfidence  // .collectingData / .lowConfidence / .normal
}
```

No backend URL, HTTP status, model internals, thermal state, or raw FPS in this struct — those stay in the existing `BackendDiagnostics`/`FieldTestOverlay` path, debug-only.

## 10. Proposed `PlayerAssessmentSummary` (post-session, design only)

```swift
struct PlayerAssessmentSummary {
    let sessionId: String
    let totalDurationSeconds: Double
    let trackingCoverageFraction: Double       // % of samples with a confident lock
    let temporarilyLostEpisodes: Int
    let movementActiveFraction: Double         // % of time in "moving" state
    let directionChangeCount: Int?             // nil until the counting logic (§8) exists
    let ballInvolvementFraction: Double?       // % of time "near ball" — nil until built
    let scanningEventCount: Int?               // nil until Level 1 scanning proxy exists
    let dataSufficiency: DataSufficiency        // .sufficient / .partial / .insufficientTracking
}
```

## 11. Coaching-cue feasibility

| Cue | Trigger | Required signals | Cooldown | Confidence threshold | False-positive risks | Status |
|---|---|---|---|---|---|---|
| "Good tracking, keep going" (neutral encouragement) | Sustained `locked` state for N seconds | `PlayerTracker.state` (have) | session-long, once | N/A | None (purely reflects tracking, not player behavior) | **SUPPORTED** (trivial) |
| "Keep moving when the ball is away" | Player velocity near zero for a sustained window while ball is far/not visible | `currentVelocity`, ball distance/visibility (have both raw) | e.g. 10s | needs a defined stillness threshold (undetermined) | Player could be intentionally holding position (valid tactic); camera framing changes could fake "stillness" (§7) | **NOT YET SUPPORTED** — needs the stillness+ball-distance rule built and validated |
| "Check your shoulder before receiving" | Torso-rotation event shortly before ball proximity increases | Level 1 torso-orientation proxy (does not exist yet), ball-approaching signal (partially have) | per reception event | undetermined without real data | High — a torso proxy is not a real scan; could reward incidental turning | **NOT YET SUPPORTED** — depends on §4 Level 1/2 shipping and being validated against real footage first |
| "Create more separation" | Distance to nearest other detected person below a threshold, sustained | would need per-OTHER-person distance, currently only used for identity-safety, not exposed as a metric | per episode | undetermined | Very high without team/opponent classification — "close to someone" isn't necessarily bad positioning | **NOT YET SUPPORTED** — also blocked on §5's multi-player-tracking gap for any tactical meaning |
| "Good recovery movement" | Rapid movement toward own defensive area after losing the ball | needs field-space attacking direction (§7) — do not have | per episode | undetermined | Cannot be built without field calibration | **NOT YET SUPPORTED** |

Every cue beyond the first is honestly blocked on a real, named, unbuilt capability — none should ship as a "cue" until its trigger is implemented and validated against real assessment footage.

## 12. Recommended next CV engineering milestone

Not a UI task. In priority order, each unblocks a real, honestly-labelable metric:
1. **Expose `BallTracker.velocity`** and add a simple player-ball distance/proximity computation — smallest change, unlocks "ball proximity" live and "ball involvement" post-session.
2. **Torso-orientation proxy** (shoulder-hip vector angle over time) — smallest step toward any "scanning"/positioning cue, still explicitly labeled a proxy, not head-pose.
3. **On-device (or backend) session aggregation service** over already-uploaded `TrackingSample`s — needed for every "post-assessment" metric in §8; currently nothing computes these even though the raw samples exist.
4. Only after 1-3 are built and validated against real footage: consider multi-player tracking and field calibration (§5/§7) as a larger, separate initiative — these are prerequisites for any genuinely tactical (not just single-player-descriptive) metric.

---

# Summary

**WHAT WE CAN SHOW TODAY** (real, already-computed, no new CV): tracking confidence, lock/lost state, ball visible + its (deliberately low) confidence — as descriptive states, not scores.

**WHAT WE CAN BUILD FROM EXISTING RAW DATA** (new logic, no new model): movement state (moving/stationary), ball proximity, direction-change counting, activity/movement-active fraction, ball-involvement fraction, tracking-reliability summary, and a torso-orientation proxy (explicitly not "scanning," a Level 1 building block toward it).

**WHAT REQUIRES NEW CV** (models or field calibration): true head-pose scanning, any possession/touch/pass/shot detection, multi-player tracking with identity, real-world distance/speed, teammate/opponent classification, open-space/pressure analysis, and the entire `ActionRecognition` feature (no trained model exists).

**WHAT WE SHOULD NOT CLAIM YET**: tactical positioning relative to teammates/opponents (no multi-player tracking exists), any physical distance or speed number (no field calibration, camera itself moves), any possession/touch/pass/shot count (no such logic exists, box-overlap is not evidence), true "scanning/vision" behavior (no head-pose data is even collected today), and any 0-100 score for anything above — every eventual score needs a defined raw measurement, normalization, baseline, and confidence rule first (Phase 9), none of which exist yet.

---

# Implementation status (Phase 2 — 2026-09-27)

**Implemented, tested, not yet committed:**

- `PlayerLiveAnalysisSnapshot`, `PlayerAnalysisHistoryEntry`, `PlayerAnalysisSessionSummary` and `PlayerAnalysisEngine` — `Sources/Tracking/PlayerAnalysisEngine.swift`. Pure downstream consumer of `TrackingCoordinator`'s own already-computed `PlayerTracker`/`BallTracker` state; performs no detection, owns no camera/AVFoundation/Vision object.
- Integrated into `TrackingCoordinator.apply(result:)` (one `observe(...)` call, after tracking/framing/gimbal are already decided) and exposed as `@Published var playerAnalysis` plus `playerAnalysisSummary()`.
- `TorsoOrientationExperiment` (`Sources/Tracking/TorsoOrientationExperiment.swift`) — `#if DEBUG`-only, not integrated into the engine, snapshot, summary, or any UI. Exists only to be tested against real footage before any promotion decision.
- `PlayerLiveAnalysisPanel` (`Sources/UI/PlayerLiveAnalysisPanel.swift`) replaces the always-on `FieldTestOverlay` in the normal coach view. `FieldTestOverlay`/its Settings toggle are now `#if DEBUG`-only (compiled out of Release entirely — verified by a Release build).
- 30 new unit tests (24 `PlayerAnalysisEngineTests`, 6 `TorsoOrientationExperimentTests`) — all passing. Full suite: 209 tests, 4 pre-existing failures (`testM2`/`testM5`, unrelated `PlayerTracker` limitation, unchanged).

**Deliberately NOT implemented (see the full report for why):**

- `directionChangeCount` in the summary — no validated noise-rejection rule exists yet; left out rather than faked, per the audit's own instruction.
- Any 0-100 score, `analysisConfidence`, or `movementConfidence` — no mathematically defensible combination exists; per-signal real confidences (`trackingConfidence`, `ballConfidence`) are exposed instead.
- Torso-orientation promotion to production analysis — stays experimental/DEBUG-only until validated against real footage.
- Any UI for `PlayerAnalysisSessionSummary` — the type and `TrackingCoordinator.playerAnalysisSummary()` exist and are tested, but nothing calls them yet (scope was "minimal first pass," live panel only).
