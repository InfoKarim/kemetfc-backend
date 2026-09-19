//
//  AssessmentSessionViewModel.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  The full workflow (spec section 24), owning every piece and wiring
//  them together in the right order:
//
//    Select/Confirm Player -> Connect Gimbal -> Select Smart Soccer ->
//    Tap Player -> PLAYER LOCKED -> Start Assessment -> [tracking +
//    recording run concurrently] -> Stop Assessment -> Upload video ->
//    Complete tracking session -> Publish assessment -> Player Profile
//
//  Reliability fix: recording must NEVER depend on backend connectivity
//  (a real physical-device failure — a single dropped request after
//  Stop Assessment threatened the recording). Local video persistence
//  and the upload queue (PendingAssessmentStore) now come FIRST in both
//  startAssessment() and stopAssessment(); every backend call after
//  that is best-effort, retryable, and never deletes or blocks on the
//  local recording.
//

import AVFoundation
import Combine
import Foundation
import Network
import UIKit
import os.log

/// Real-time backend reachability, surfaced ONLY in Field Test Mode
/// (spec Phase 3) — never shown in the normal production HUD. Updated
/// after every API call this view model makes, from the ACTUAL
/// response/error of that call — never a fabricated "connected" guess.
public struct BackendDiagnostics: Equatable {
    public var baseURL: URL
    public var isReachable: Bool?
    public var lastRequestDescription: String?
    public var lastErrorDescription: String?
    public var lastLatencyMs: Double?
}

/// Shared response shape for GET /auth/me and POST /auth/login — both
/// return {"user": {...}} (main.py get_current_user/login), and the
/// login view only needs the username back to confirm who signed in.
private struct AuthUserResponse: Decodable {
    struct User: Decodable {
        let username: String
        let role: String
        let avatarURL: String?
        enum CodingKeys: String, CodingKey {
            case username, role
            case avatarURL = "avatar_url"
        }
    }
    let user: User
}

@MainActor
public final class AssessmentSessionViewModel: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "AssessmentSessionViewModel")

    @Published public private(set) var confirmedPlayer: KemetPlayerSummary?
    @Published public private(set) var backendSessionId: String?
    @Published public private(set) var isAssessmentActive = false
    @Published public private(set) var lastError: String?
    @Published public private(set) var backendDiagnostics: BackendDiagnostics
    /// Bindable by AssessmentCameraView (or a future upload-progress
    /// sheet) to show PREPARING/UPLOADING_VIDEO(progress)/PROCESSING/
    /// COMPLETE/FAILED without polling — see VideoUploadManager.swift.
    @Published public private(set) var videoUploadState: VideoUploadManager.UploadState = .notStarted
    /// The just-finished recording, once stopAssessment() has finalized
    /// and verified the local file — drives the Assessment Summary
    /// screen (spec Phase 5). Cleared when the coach taps Done or starts
    /// a new assessment.
    @Published public private(set) var lastFinishedRecordingId: String?

    /// Native login (LoginView) — checked once at launch against the
    /// EXISTING session cookie (GET /auth/me), so a coach who logged in
    /// on a previous run stays signed in rather than re-entering a
    /// password every launch (the backend cookie itself is the
    /// long-lived credential, exactly like the web app).
    @Published public private(set) var isCheckingSession = true
    @Published public private(set) var isAuthenticated = false
    @Published public private(set) var authenticatedUsername: String?
    /// GET /auth/me's/POST /auth/login's avatar_url, resolved against
    /// apiClient's own baseURL — same field the web dashboard's
    /// #account-avatar reads, updated locally after a successful
    /// uploadAvatar()/removeAvatar() so the UI never waits on a second
    /// round trip to reflect its own change.
    @Published public private(set) var avatarURL: URL?
    /// "coach", "admin", or "guardian" (main.py UserDB.role) — RootView
    /// uses this to route to either the coach recording flow or
    /// GuardianHomeView after login. Never assumed — always the real
    /// value from /auth/me or /auth/login.
    @Published public private(set) var userRole: String?

    /// Real coach identity for `created_by` on the video-upload metadata
    /// (PlayerVideoUploadMetadataSchema.created_by is required) — set
    /// only by a real login (checkExistingSession/login below), never
    /// defaulted to a fabricated-looking name.
    public private(set) var coachIdentifier: String = "ios-app-unidentified-coach"

    public let dockKitManager = DockKitManager()
    public let cameraCaptureManager = CameraCaptureManager()
    /// Durable local recording queue — survives app restart (spec Phase
    /// 1/2). Exposed (not private) so a "Pending Uploads" view can
    /// observe it directly.
    public let pendingStore = PendingAssessmentStore()
    private let apiClient: KemetAPIClient
    /// Exposed so GuardianViewModel (a guardian-role account never
    /// touches any coach/recording state on this class) can issue
    /// requests through the SAME authenticated client/cookie session,
    /// rather than constructing a second one.
    public var sharedAPIClient: KemetAPIClient { apiClient }
    private var gimbalController: GimbalController?
    /// Published (not private) — AssessmentCameraView binds to THIS
    /// exact instance, not one it constructs itself, so the overlay/HUD
    /// reflects the same coordinator actually receiving camera frames.
    /// Stays nil until a backend tracking session actually exists —
    /// recording itself (cameraCaptureManager) never waits on this.
    @Published public private(set) var trackingCoordinator: TrackingCoordinator?
    private var telemetryUploader: TelemetryUploader?
    private let videoUploadManager = VideoUploadManager()
    private var cancellables = Set<AnyCancellable>()
    // Strong reference required: CameraCaptureManager.frameDelegate is
    // `weak` (by design, to avoid a retain cycle back to the manager),
    // so assigning a FrameForwarder to it with nothing else retaining
    // the instance let Xcode's real build flag "instance will be
    // immediately deallocated" — meaning processFrame(...) would NEVER
    // actually fire in production; the entire per-frame inference
    // pipeline would silently do nothing. Never caught by swiftc
    // -typecheck (this file needs AVFoundation/DockKit); only a real
    // Xcode build surfaced it.
    private var frameForwarder: FrameForwarder?
    /// The in-flight recording's local queue ID (see
    /// PendingAssessmentStore) — set the instant Start Assessment is
    /// tapped, before any network call.
    private var activeRecordingId: String?
    /// Retries backend session creation in the background while a
    /// recording that started without connectivity continues (spec
    /// Phase 1/4: a transient outage at start must not block or lose
    /// the recording, and must not force the coach back to player
    /// selection). Cancelled on stopAssessment() or when it succeeds.
    private var sessionRetryTask: Task<Void, Never>?
    /// Spec Phase 5: "Prevent simultaneous duplicate uploads of the same
    /// clientRecordingId" — guards finalizeAndUpload against being
    /// re-entered for a recording it's already actively processing (a
    /// coach double-tapping Retry Upload, or an auto-retry trigger
    /// firing while a manual retry is already in flight).
    private var activeUploadIds: Set<String> = []
    private var networkMonitor: NWPathMonitor?
    private var wasNetworkSatisfied = false
    /// Bounded exponential backoff for AUTOMATIC retries only (spec
    /// Phase 5) — manual "Retry Upload" always retries immediately
    /// regardless of this. 30s, 60s, 120s, ... capped at 10 minutes.
    private static let baseBackoffSeconds: TimeInterval = 30
    private static let maxBackoffSeconds: TimeInterval = 600

    public init(apiClient: KemetAPIClient) {
        self.apiClient = apiClient
        self.backendDiagnostics = BackendDiagnostics(baseURL: apiClient.baseURL)
        videoUploadManager.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in self?.videoUploadState = state }
            .store(in: &cancellables)
    }

    /// Checked once at launch (RootView shows LoginView until this
    /// resolves) — GET /auth/me on the EXISTING session cookie, the same
    /// endpoint auth-client.js polls on every web dashboard page.
    public func checkExistingSession() async {
        do {
            let response = try await apiClient.get(path: "/auth/me", as: AuthUserResponse.self)
            authenticatedUsername = response.user.username
            userRole = response.user.role
            coachIdentifier = response.user.username
            avatarURL = resolveAvatarURL(response.user.avatarURL)
            apiClient.refreshCSRFToken()
            isAuthenticated = true
        } catch {
            isAuthenticated = false
        }
        isCheckingSession = false
    }

    /// avatar_url from the backend is root-relative (e.g.
    /// "/uploads/avatars/…") — resolved against apiClient's own baseURL,
    /// never a second guessed host.
    private func resolveAvatarURL(_ path: String?) -> URL? {
        guard let path, !path.isEmpty else { return nil }
        return URL(string: path, relativeTo: apiClient.baseURL)?.absoluteURL
    }

    /// LoginView's Sign In button — POST /auth/login (PUBLIC_PATHS, no
    /// CSRF token needed pre-login), the same endpoint and credentials
    /// the web dashboard's /login page uses. Returns nil on success, or
    /// a coach-facing message on failure (never a generic "login failed").
    public func login(username: String, password: String) async -> String? {
        struct LoginBody: Encodable { let username: String; let password: String }
        do {
            let response = try await apiClient.post(
                path: "/auth/login",
                body: LoginBody(username: username, password: password),
                as: AuthUserResponse.self
            )
            authenticatedUsername = response.user.username
            userRole = response.user.role
            coachIdentifier = response.user.username
            avatarURL = resolveAvatarURL(response.user.avatarURL)
            apiClient.refreshCSRFToken()
            isAuthenticated = true
            return nil
        } catch KemetAPIError.server(_, let detail) {
            return detail
        } catch {
            return "Could not reach the KEMET server — check your connection and try again."
        }
    }

    /// Sign out (GuardianHomeView) — POST /auth/logout, the same endpoint
    /// the web dashboard's sign-out button uses; clears the session/CSRF
    /// cookies server-side, then resets local auth state so RootView
    /// falls back to LoginView.
    public func logout() async {
        try? await apiClient.post(path: "/auth/logout")
        authenticatedUsername = nil
        userRole = nil
        avatarURL = nil
        isAuthenticated = false
    }

    /// Profile-picture control (AccountAvatarControl, used on
    /// CoachHomeView/GuardianHomeView/PlayerSelectionView) — POST
    /// /auth/me/avatar, the SAME endpoint and multipart shape
    /// (VideoUploadManager already established the pattern for videos)
    /// the web dashboard's own avatar upload button uses. Re-encodes to
    /// JPEG regardless of the photo library's source format (often
    /// HEIC), since the backend only accepts JPEG/PNG/WebP by content
    /// type — never uploads bytes the server would reject outright.
    public func uploadAvatar(imageData: Data) async -> String? {
        guard let uiImage = UIImage(data: imageData),
              let jpegData = uiImage.jpegData(compressionQuality: 0.85) else {
            return "Could not process the selected photo."
        }

        let boundary = "KemetFCBoundary-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"avatar\"; filename=\"avatar.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpegData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        var request = apiClient.authenticatedRequest(path: "/auth/me/avatar", method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        struct AvatarResponse: Decodable {
            let avatarURL: String
            enum CodingKeys: String, CodingKey { case avatarURL = "avatar_url" }
        }

        do {
            let (data, response) = try await apiClient.underlyingSession.upload(for: request, from: body)
            try KemetAPIClient.validate(response: response, data: data)
            let decoded = try JSONDecoder.kemet.decode(AvatarResponse.self, from: data)
            avatarURL = resolveAvatarURL(decoded.avatarURL)
            return nil
        } catch KemetAPIError.server(_, let detail) {
            return detail
        } catch {
            return "Could not upload the photo — check your connection and try again."
        }
    }

    public func removeAvatar() async {
        let request = apiClient.authenticatedRequest(path: "/auth/me/avatar", method: "DELETE")
        _ = try? await apiClient.underlyingSession.data(for: request)
        avatarURL = nil
    }

    public func start() async {
        await checkExistingSession()
        dockKitManager.startMonitoring()
        do {
            try await cameraCaptureManager.requestPermissionAndConfigure()
        } catch {
            lastError = "Camera permission denied — grant camera access in Settings to record an assessment."
        }
        startNetworkMonitoring()
        // A live app always moves a recording OUT of `.recording` via
        // stopAssessment() — any record still in that state on launch
        // means the app was killed (crash, force-quit, OS termination)
        // before Stop Assessment ever ran. Recover or clearly fail each
        // one now, before it can be silently stuck forever (spec Phase
        // 10: "app terminated while upload pending" / "app relaunched
        // with pending assessment" — this is that scenario one step
        // earlier, when termination happened DURING recording itself).
        await recoverOrphanedRecordings()
        // Confirmed defect: a record still `.uploading` on launch means
        // the app was killed mid-upload — `.uploading` was previously
        // excluded from isRetryable, permanently stranding it with no
        // automatic or manual way to ever finish. Normalizing to
        // `.pendingUpload` is always safe regardless of how far the
        // in-flight attempt actually got: finalizeAndUpload() never
        // re-uploads the video if `serverVideoId` is already persisted,
        // and the backend's own idempotency (client_recording_id /
        // stable video/record IDs) covers the rest.
        normalizeStaleUploadingRecords()
        // Spec Phase 5: "Retry pending uploads ... when app launches."
        await retryAllPending()
    }

    private func normalizeStaleUploadingRecords() {
        for var record in pendingStore.records where record.uploadState == .uploading {
            record.uploadState = .pendingUpload
            record.lastError = "Recovered after the app closed mid-upload — will retry."
            pendingStore.upsert(record)
            log.info("Normalized stale UPLOADING record \(record.id) to PENDING_UPLOAD on launch")
        }
    }

    /// See start() above. AVCaptureMovieFileOutput writes its file
    /// incrementally as recording progresses (not only at a clean
    /// stopRecording() call), so a killed app can still leave a
    /// legitimately playable file behind — this is verified the exact
    /// same way stopAssessment() verifies a normal stop, never assumed.
    private func recoverOrphanedRecordings() async {
        let orphaned = pendingStore.records.filter { $0.uploadState == .recording }
        for var record in orphaned {
            switch await pendingStore.verifyFileHealth(for: record) {
            case .missing:
                record.uploadState = .failedPermanent
                record.lastError = "App was closed before this recording finished, and no video file was found."
                pendingStore.upsert(record)
            case .empty:
                record.uploadState = .failedPermanent
                record.lastError = "App was closed before this recording finished — the saved file is empty."
                pendingStore.upsert(record)
            case .unreadable:
                record.uploadState = .failedPermanent
                record.lastError = "App was closed before this recording finished — the saved file isn't playable."
                pendingStore.upsert(record)
            case .verified(let sizeBytes):
                let duration = (try? await AVURLAsset(url: pendingStore.localVideoURL(for: record)).load(.duration).seconds) ?? 0
                record.stopTime = record.startTime.addingTimeInterval(duration)
                record.durationSeconds = duration
                record.fileSizeBytes = sizeBytes
                // Confirmed defect (physical device): only stopAssessment()'s
                // own success path set resolution/frameRateFps — this
                // orphan-recovery path left both nil, and the backend
                // correctly rejects a video upload with frame_rate_fps<=0
                // (app/data_models.py VideoData.__post_init__), so every
                // crash-recovered recording failed to upload with no clear
                // path to a fix (retrying the same nil values forever).
                // The exact format/rate used by the ORIGINAL, now-orphaned
                // recording isn't knowable after the fact, but this is the
                // SAME device running the SAME deterministic
                // selectBestFormat() logic, so the just-negotiated current
                // values are the best available honest approximation —
                // never left as an invalid 0/nil the backend will only
                // ever reject.
                record.resolution = cameraCaptureManager.activeFormatDescription
                record.frameRateFps = cameraCaptureManager.effectiveFrameRate
                record.uploadState = .localSaved
                record.lastError = "Recovered after the app was closed mid-recording — the video was preserved."
                pendingStore.upsert(record)
                log.info("Recovered orphaned recording \(record.id) — \(duration)s, \(sizeBytes) bytes")
            }
        }
    }

    /// Spec Phase 5: "Retry pending uploads ... when app returns to
    /// foreground" — called from the App's scenePhase observer.
    public func handleAppDidBecomeActive() async {
        await retryAllPending()
    }

    /// Watches real network reachability (not a guess) and retries every
    /// pending recording the moment connectivity is restored — spec
    /// Phase 5: "Retry pending uploads ... when network becomes
    /// available."
    private func startNetworkMonitoring() {
        guard networkMonitor == nil else { return }
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                guard let self else { return }
                let isSatisfied = path.status == .satisfied
                if isSatisfied, !self.wasNetworkSatisfied {
                    await self.retryAllPending()
                }
                self.wasNetworkSatisfied = isSatisfied
            }
        }
        monitor.start(queue: DispatchQueue(label: "com.kemetfc.tracker.networkmonitor"))
        networkMonitor = monitor
    }

    /// Retries every recording eligible for retry (spec Phase 5) —
    /// respects bounded exponential backoff for records that have
    /// already failed at least once, so a genuinely offline backend
    /// doesn't get hammered every time the app happens to launch/
    /// foreground/reconnect.
    public func retryAllPending() async {
        for record in pendingStore.pendingUploads where record.isRetryable {
            if record.uploadAttempts > 0, let lastAttemptAt = record.lastAttemptAt {
                let backoff = min(Self.baseBackoffSeconds * pow(2, Double(record.uploadAttempts - 1)), Self.maxBackoffSeconds)
                guard Date().timeIntervalSince(lastAttemptAt) >= backoff else { continue }
            }
            await finalizeAndUpload(recordId: record.id)
        }
    }

    /// Step 1: coach confirms the player (spec section 5). No tracking
    /// starts until this has happened.
    public func confirmPlayer(_ player: KemetPlayerSummary) {
        confirmedPlayer = player
    }

    /// Search/Player ID tabs (spec section 5): the full roster is fetched
    /// once from the EXISTING GET /players endpoint and cached, then
    /// filtered locally per keystroke — same approach the web dashboard's
    /// player picker (app/static/add_video.html) uses, and avoids hitting
    /// the backend on every character typed.
    private var cachedRoster: [KemetPlayerSummary]?

    public func searchPlayers(_ query: String) async -> [KemetPlayerSummary] {
        let roster: [KemetPlayerSummary]
        if let cachedRoster {
            roster = cachedRoster
        } else {
            let start = Date()
            do {
                roster = try await apiClient.get(path: "/players", as: [KemetPlayerSummary].self)
                cachedRoster = roster
                recordDiagnostics(request: "GET /players", startedAt: start, error: nil)
            } catch {
                recordDiagnostics(request: "GET /players", startedAt: start, error: "\(error)")
                return []
            }
        }

        let term = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !term.isEmpty else { return roster }
        return roster.filter {
            $0.fullName.lowercased().contains(term) || $0.playerId.lowercased().contains(term)
        }
    }

    /// Shirt Number tab (spec section 5, replacing QR Scan): a jersey
    /// number is only unique within a team (app/services/player_service.py
    /// JerseyNumberConflictError), so this returns every match — same
    /// EXISTING backend query the web dashboard's picker uses (GET
    /// /players?jersey_number=N, main.py get_all_players).
    public func findByJerseyNumber(_ number: Int) async -> [KemetPlayerSummary] {
        let start = Date()
        do {
            let matches = try await apiClient.get(
                path: "/players?jersey_number=\(number)",
                as: [KemetPlayerSummary].self
            )
            recordDiagnostics(request: "GET /players?jersey_number=\(number)", startedAt: start, error: nil)
            return matches
        } catch {
            recordDiagnostics(request: "GET /players?jersey_number=\(number)", startedAt: start, error: "\(error)")
            return []
        }
    }

    /// Step 2 (implicitly already true if dockKitManager.isConnected):
    /// the gimbal is physically docked. Step 3: start recording LOCALLY
    /// first — this is the entire point of the reliability fix — and
    /// only then attempt the backend tracking session, best-effort.
    public func startAssessment(mode: TrackingMode, speedProfile: TrackingSpeedProfile) async {
        guard let player = confirmedPlayer else {
            lastError = "Select and confirm a player before starting an assessment."
            return
        }
        guard !isAssessmentActive else { return }

        if let preflightFailure = Self.preflightCheck() {
            lastError = preflightFailure
            return
        }

        // --- Phase 1: local recording starts FIRST, unconditionally. ---
        let recordingId = UUID().uuidString
        let videoURL = pendingStore.newRecordingFileURL(recordingId: recordingId)
        var record = PendingAssessmentRecord(
            id: recordingId,
            playerId: player.playerId,
            playerName: player.fullName,
            localVideoRelativePath: pendingStore.relativePath(for: videoURL),
            startTime: Date(),
            trackingMode: mode.rawValue
        )
        pendingStore.upsert(record)

        // Confirmed defect fix: Start Assessment must not present
        // recording as active until AVFoundation genuinely confirms it
        // (didStartRecordingTo) — previously isAssessmentActive was set
        // synchronously right after calling startRecording(to:), before
        // any confirmation. A bounded wait avoids blocking forever on a
        // hung camera; a timeout is surfaced as a warning, never treated
        // as a permanent failure (a late real confirmation, or a real
        // file at the expected path found later by stopAssessment(),
        // both still recover correctly).
        let didConfirmStart = await cameraCaptureManager.startRecordingAndWait(to: videoURL)
        activeRecordingId = recordingId
        isAssessmentActive = didConfirmStart
        lastFinishedRecordingId = nil

        guard didConfirmStart else {
            lastError = "Camera did not confirm recording start in time — tap Start Assessment again. (If it actually started, Stop will still recover the file.)"
            return
        }

        // --- Everything below is best-effort backend wiring. A failure
        // here never stops, discards, or hides the recording above. ---
        let ballDetector: BallDetecting = ClassicalCVBallDetector()
        if let sessionId = await createBackendSession(
            for: record,
            mode: mode,
            ballDetector: ballDetector
        ) {
            record.backendSessionId = sessionId
            pendingStore.upsert(record)
            wireTracking(sessionId: sessionId, mode: mode, speedProfile: speedProfile, ballDetector: ballDetector)
        } else {
            lastError = "Recording started and is saving locally — live tracking will connect automatically once the KEMET server is reachable."
            retrySessionCreationInBackground(mode: mode, speedProfile: speedProfile, ballDetector: ballDetector)
        }
    }

    /// One attempt at POST /tracking/sessions, using the recording's own
    /// local ID as the idempotency key (client_recording_id) — safe to
    /// call more than once for the same recording (see
    /// retrySessionCreationInBackground and finalizeAndUpload).
    private func createBackendSession(
        for record: PendingAssessmentRecord,
        mode: TrackingMode,
        ballDetector: BallDetecting
    ) async -> String? {
        struct CreateSessionBody: Encodable {
            let player_id: String
            let tracking_mode: String
            let gimbal_model: String?
            let player_detector_version: String
            let ball_detector_version: String
            let ball_model_status: String
            let pose_model_version: String
            let tracker_algorithm_version: String
            let framing_algorithm_version: String
            let ios_app_version: String?
            let client_recording_id: String
        }
        struct SessionResponse: Decodable { let session_id: String }

        let start = Date()
        do {
            apiClient.refreshCSRFToken()
            let response = try await apiClient.post(
                path: "/tracking/sessions",
                body: CreateSessionBody(
                    player_id: record.playerId,
                    tracking_mode: mode.rawValue,
                    gimbal_model: dockKitManager.connectedAccessory != nil ? "Insta360 Flow 2 Pro" : nil,
                    player_detector_version: PlayerDetector.detectorVersion,
                    ball_detector_version: ballDetector.modelIdentifier.version,
                    ball_model_status: ballDetector.ballModelStatus,
                    pose_model_version: PlayerDetector.poseModelVersion,
                    tracker_algorithm_version: PlayerTracker.algorithmVersion,
                    framing_algorithm_version: FramingCalculator.algorithmVersion,
                    ios_app_version: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String,
                    client_recording_id: record.id
                ),
                as: SessionResponse.self
            )
            recordDiagnostics(request: "POST /tracking/sessions", startedAt: start, error: nil)
            backendSessionId = response.session_id
            return response.session_id
        } catch {
            recordDiagnostics(request: "POST /tracking/sessions", startedAt: start, error: "\(error)")
            log.error("createBackendSession failed: \(error.localizedDescription)")
            return nil
        }
    }

    /// Wires GimbalController/TrackingCoordinator/TelemetryUploader once
    /// a real backend session exists — never before, since telemetry has
    /// nowhere valid to post to without one.
    private func wireTracking(sessionId: String, mode: TrackingMode, speedProfile: TrackingSpeedProfile, ballDetector: BallDetecting) {
        let uploader = TelemetryUploader(apiClient: apiClient, sessionId: sessionId)
        uploader.start()
        telemetryUploader = uploader

        let controller = GimbalController()
        controller.speedProfile = speedProfile
        if let accessory = dockKitManager.connectedAccessory {
            controller.attach(accessory: accessory)
        }
        gimbalController = controller

        guard let captureDevice = cameraCaptureManager.device else {
            lastError = "Camera is not configured — live tracking overlay unavailable, but recording continues."
            return
        }

        let coordinator = TrackingCoordinator(gimbalController: controller, telemetryUploader: uploader, ballDetector: ballDetector)
        coordinator.setMode(mode)
        trackingCoordinator = coordinator
        let forwarder = FrameForwarder(coordinator: coordinator, device: captureDevice)
        frameForwarder = forwarder
        cameraCaptureManager.frameDelegate = forwarder
    }

    /// Spec Phase 1/4: a recording that started while the backend was
    /// unreachable keeps trying to connect in the background — the
    /// coach is never forced to stop and restart, and never sent back to
    /// player selection just because the FIRST attempt failed.
    private func retrySessionCreationInBackground(mode: TrackingMode, speedProfile: TrackingSpeedProfile, ballDetector: BallDetecting) {
        sessionRetryTask?.cancel()
        let recordingId = activeRecordingId
        sessionRetryTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled, self.activeRecordingId == recordingId, self.backendSessionId == nil,
                      let recordingId, let record = self.pendingStore.record(id: recordingId) else { return }
                if let sessionId = await self.createBackendSession(for: record, mode: mode, ballDetector: ballDetector) {
                    var updated = record
                    updated.backendSessionId = sessionId
                    self.pendingStore.upsert(updated)
                    self.wireTracking(sessionId: sessionId, mode: mode, speedProfile: speedProfile, ballDetector: ballDetector)
                    self.lastError = nil
                    return
                }
            }
        }
    }

    /// Battery/storage pre-checks before starting an assessment (spec
    /// section: "add battery/storage pre-checks") — real measured
    /// values (UIDevice.batteryLevel, FileManager volume capacity), not
    /// guesses, and deliberately conservative/approximate thresholds
    /// rather than a fabricated exact recording-duration prediction
    /// (spec: "Do not fabricate exact recording duration predictions
    /// unless based on measured bitrate/file size" — this app has not
    /// measured its own actual bitrate, so it checks a safety margin
    /// instead of promising "N minutes remaining"). Returns nil when OK,
    /// or a coach-facing message describing what to fix.
    private static func preflightCheck() -> String? {
        UIDevice.current.isBatteryMonitoringEnabled = true
        let batteryState = UIDevice.current.batteryState
        let batteryLevel = UIDevice.current.batteryLevel
        // batteryLevel is -1 (unknown) in the Simulator or briefly at
        // startup — never treated as "low," only a genuinely measured
        // low value is.
        if batteryState != .charging, batteryState != .full, batteryLevel >= 0, batteryLevel < 0.15 {
            return "Battery is below 15% and not charging — plug in before starting a full assessment."
        }

        // Conservative floor, not a duration promise: 1080p H.264 at a
        // typical ~15 Mbps runs well under 150MB/minute in practice, so
        // 1GB free comfortably covers a multi-minute assessment without
        // this app ever claiming to know the coach's exact device
        // codec/bitrate in advance. Checked against the Documents
        // volume (where recordings now live), not temporaryDirectory.
        let minimumFreeBytes: Int64 = 1_000_000_000
        if let values = try? PendingAssessmentStore.defaultDocumentsDirectory
            .resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
            let available = values.volumeAvailableCapacityForImportantUsage,
            available < minimumFreeBytes {
            let availableMB = available / 1_000_000
            return "Only \(availableMB)MB of storage available — free up space before starting an assessment."
        }

        return nil
    }

    /// Coach taps a detected person to lock the target (spec section 6).
    public func handlePlayerTap(_ point: NormalizedPoint, detections: [PersonDetection], pixelBuffer: CVPixelBuffer) {
        trackingCoordinator?.lockPlayer(at: point, detections: detections, pixelBuffer: pixelBuffer)
    }

    /// Coach review workflow (spec section 16): "Correct Player Tracked:
    /// YES/NO/UNSURE" — posts to the EXISTING, tested backend endpoint
    /// (POST /tracking/sessions/{id}/confirm-player, app/routers/
    /// tracking.py). A failure here is deliberately non-fatal to the
    /// caller (the assessment itself already completed) — surfaced via
    /// lastError for visibility, not thrown.
    public func confirmPlayerTracked(_ answer: String, notes: String? = nil) async {
        guard let sessionId = backendSessionId else { return }
        struct ConfirmBody: Encodable { let answer: String; let notes: String? }
        struct LabelResponse: Decodable { let label_id: String }
        do {
            apiClient.refreshCSRFToken()
            _ = try await apiClient.post(
                path: "/tracking/sessions/\(sessionId)/confirm-player",
                body: ConfirmBody(answer: answer, notes: notes),
                as: LabelResponse.self
            )
        } catch {
            log.error("confirmPlayerTracked failed: \(error.localizedDescription)")
            lastError = "Could not record player-tracking confirmation — the assessment itself was saved regardless."
        }
    }

    /// Stop Assessment (spec Phase 1): finalize and VERIFY the local
    /// file before anything else — the video is durably safe on disk
    /// before a single upload byte is sent. Upload is then attempted
    /// best-effort; failure never deletes the recording or the queue
    /// entry, and never forces the coach back to the start screen (spec
    /// Phase 4) — lastFinishedRecordingId drives the Assessment Summary
    /// screen (spec Phase 5) regardless of upload outcome.
    public func stopAssessment() async {
        sessionRetryTask?.cancel()
        sessionRetryTask = nil
        telemetryUploader?.stop()
        isAssessmentActive = false

        guard let recordingId = activeRecordingId, var record = pendingStore.record(id: recordingId) else { return }
        activeRecordingId = nil

        // Await AVFoundation's real finalization first (this drives
        // stopRecording() and waits for didFinishRecordingTo) — but per
        // the confirmed fix, VERIFY against the recording's own KNOWN
        // expected path (pendingStore.localVideoURL(for:), the exact URL
        // originally passed to startRecordingAndWait), not merely
        // whatever stopRecordingAndWait happens to return. This is
        // exactly the fix for the bug a physical-device test caught:
        // stopRecordingAndWait could return nil (or an unresolved state)
        // while AVFoundation had already written a real, valid file to
        // the expected path — declaring it "missing" without checking
        // there first threw away a genuinely safe recording.
        _ = await cameraCaptureManager.stopRecordingAndWait()

        switch await pendingStore.verifyFileHealth(for: record) {
        case .missing:
            // A file-level failure is never retryable — there is nothing
            // to upload. Still never discarded: the queue entry remains,
            // visible in Pending Uploads, for the coach to see exactly
            // what happened, and the record is not deleted.
            record.uploadState = .failedPermanent
            record.lastError = "Recording file was not found after stopping — the camera may have failed to finalize it."
            pendingStore.upsert(record)
            lastError = record.lastError
            lastFinishedRecordingId = recordingId
        case .empty:
            record.uploadState = .failedPermanent
            record.lastError = "Recording file is empty (0 bytes) after stopping."
            pendingStore.upsert(record)
            lastError = record.lastError
            lastFinishedRecordingId = recordingId
        case .unreadable:
            record.uploadState = .failedPermanent
            record.lastError = "Recorded video is not readable — it may be corrupt."
            pendingStore.upsert(record)
            lastError = record.lastError
            lastFinishedRecordingId = recordingId
        case .verified(let sizeBytes):
            // The local video is now VERIFIED safe — everything past
            // this point is upload, which can fail and be retried
            // without any risk to the recording itself.
            let duration = (try? await AVURLAsset(url: pendingStore.localVideoURL(for: record)).load(.duration).seconds) ?? 0
            record.stopTime = Date()
            record.durationSeconds = duration
            record.fileSizeBytes = sizeBytes
            record.resolution = cameraCaptureManager.activeFormatDescription
            record.frameRateFps = cameraCaptureManager.effectiveFrameRate
            record.uploadState = .localSaved
            pendingStore.upsert(record)
            lastFinishedRecordingId = recordingId

            await finalizeAndUpload(recordId: recordingId)
        }
    }

    /// Coach taps Done on the Assessment Summary screen (spec Phase 5) —
    /// only clears which recording is being SHOWN; the recording itself
    /// and its upload state remain fully intact in pendingStore either
    /// way, ready to appear again in Pending Uploads if not yet complete.
    public func clearFinishedRecording() {
        lastFinishedRecordingId = nil
        confirmedPlayer = nil
        trackingCoordinator = nil
        backendSessionId = nil
    }

    /// Retry an upload for any recording still in the queue (spec Phase
    /// 2/5's "Retry Upload") — safe to call any number of times: session
    /// creation and video upload are both idempotent on this recording's
    /// own local ID (see createBackendSession/uploadVideoIdempotent).
    public func retryUpload(recordId: String) async {
        await finalizeAndUpload(recordId: recordId)
    }

    private func finalizeAndUpload(recordId: String) async {
        guard !activeUploadIds.contains(recordId) else { return }
        guard var record = pendingStore.record(id: recordId), record.stopTime != nil else { return }
        guard pendingStore.fileExistsSync(for: record) else {
            record.uploadState = .failedPermanent
            record.lastError = "Local video file is missing — cannot upload."
            pendingStore.upsert(record)
            return
        }

        activeUploadIds.insert(recordId)
        defer { activeUploadIds.remove(recordId) }

        // Defensive backstop (confirmed defect, physical device): a
        // record can reach here with resolution/frameRateFps still nil —
        // e.g. one already persisted by an older build before
        // recoverOrphanedRecordings() was fixed to set them, on a device
        // that still has that record queued. The backend correctly
        // rejects frame_rate_fps<=0, and this record's own local queue
        // entry is never touched to "fix" it directly (data-safety rule:
        // no direct edits to pending_assessments.json) — instead this
        // fills in the same reasonable current-camera fallback at the
        // moment of upload, every time, so no already-broken record can
        // stay permanently un-uploadable.
        if record.resolution == nil || record.resolution?.isEmpty == true {
            record.resolution = cameraCaptureManager.activeFormatDescription
        }
        if record.frameRateFps == nil || record.frameRateFps == 0 {
            record.frameRateFps = cameraCaptureManager.effectiveFrameRate
        }

        record.uploadState = .uploading
        record.uploadAttempts += 1
        record.lastAttemptAt = Date()
        pendingStore.upsert(record)

        // A recording started (or retried) offline may still have no
        // backend session — create it now, idempotently, before
        // attempting the video upload that references it. If it already
        // has a serverVideoId (a previous attempt's video upload
        // succeeded but /complete failed), the video is NEVER re-sent —
        // only the completion call below is retried.
        var sessionId = record.backendSessionId
        if sessionId == nil {
            let ballDetector: BallDetecting = ClassicalCVBallDetector()
            sessionId = await createBackendSession(
                for: record,
                mode: TrackingMode(rawValue: record.trackingMode) ?? .smartSoccer,
                ballDetector: ballDetector
            )
            record.backendSessionId = sessionId
            pendingStore.upsert(record)
        }

        guard let sessionId else {
            record.uploadState = .pendingUpload
            record.lastError = "Could not reach the KEMET server — will retry."
            pendingStore.upsert(record)
            lastError = "Assessment saved on this iPhone. Upload pending."
            return
        }

        do {
            let videoId: String
            if let existingVideoId = record.serverVideoId {
                videoId = existingVideoId
            } else {
                videoId = try await uploadVideoIdempotent(record: record, sessionId: sessionId)
                record.serverVideoId = videoId
                pendingStore.upsert(record)
            }

            struct CompleteBody: Encodable { let video_id: String? }
            struct SessionResponse: Decodable { let status: String }
            let start = Date()
            do {
                apiClient.refreshCSRFToken()
                _ = try await apiClient.post(
                    path: "/tracking/sessions/\(sessionId)/complete",
                    body: CompleteBody(video_id: videoId),
                    as: SessionResponse.self
                )
                recordDiagnostics(request: "POST /tracking/sessions/\(sessionId)/complete", startedAt: start, error: nil)
                record.uploadState = .uploaded
                record.lastError = nil
                pendingStore.upsert(record)
                lastError = nil
            } catch {
                // The video itself uploaded fine (serverVideoId is
                // already persisted above) — only the final linking step
                // failed, and is retryable on its own without ever
                // re-uploading the video.
                recordDiagnostics(request: "POST /tracking/sessions/\(sessionId)/complete", startedAt: start, error: "\(error)")
                record.uploadState = .failedRetryable
                record.lastError = "Video uploaded, but finalizing the session failed — will retry."
                pendingStore.upsert(record)
                lastError = "Assessment saved on this iPhone. Upload pending."
            }
        } catch let conflict as VideoIdentityConflict {
            // A 409 whose existing server-side resource did NOT match
            // this recording's own expected identity — never treated as
            // success. The local video is preserved regardless (spec
            // Phase 4: "preserve the local video"); this needs a human
            // to look at it, since retrying the same deterministic ID
            // would hit the identical conflict forever.
            record.uploadState = .failedPermanent
            record.lastError = "Upload conflict: a different video already exists under this recording's ID (\(conflict.videoId)) on the server — needs manual review."
            pendingStore.upsert(record)
            lastError = "Assessment saved on this iPhone. Upload could not complete — see Pending Uploads."
            log.error("finalizeAndUpload identity conflict: \(conflict.detail)")
        } catch KemetAPIError.server(let status, let detail) where (400..<500).contains(status) {
            // The server rejected this exact request in a way retrying
            // will never fix (e.g. a validation error) — still never
            // deletes the recording, just stops auto-retrying it.
            record.uploadState = .failedPermanent
            record.lastError = "Upload rejected: \(detail)"
            pendingStore.upsert(record)
            lastError = "Assessment saved on this iPhone. Upload could not complete — see Pending Uploads."
            log.error("finalizeAndUpload permanently rejected: \(detail)")
        } catch {
            record.uploadState = .failedRetryable
            record.lastError = "Upload failed: \(error.localizedDescription)"
            pendingStore.upsert(record)
            lastError = "Assessment saved on this iPhone. Upload pending."
            log.error("finalizeAndUpload failed: \(error.localizedDescription)")
        }
    }

    /// Thrown when a 409 from /videos/upload turns out, on verification,
    /// to belong to a DIFFERENT recording than expected — an identity
    /// conflict on our own supposedly-unique ID, which should never
    /// happen but must never be silently treated as success if it does
    /// (spec: "mark it as a permanent identity conflict and preserve the
    /// local video").
    struct VideoIdentityConflict: Error {
        let videoId: String
        let detail: String
    }

    /// Real multipart upload to the EXISTING /videos/upload endpoint —
    /// see VideoUploadManager.swift for the streaming multipart
    /// implementation, retry/backoff, and state machine. `videoId`/
    /// `recordId` are derived deterministically from this recording's
    /// OWN stable local ID (never left nil) — a retried upload after the
    /// backend actually processed a previous attempt hits the backend's
    /// existing 409 dedupe response. That 409 is verified, not assumed:
    /// GET /videos/{stableVideoId} is fetched and its record_id/
    /// session_id are compared against what THIS recording expects
    /// before treating it as success (spec Phase 4: "Do not treat every
    /// HTTP 409 ... as success ... verify ... through the linked data
    /// record if necessary, session_id, and any available
    /// clientRecordingId relationship. Treat it as success only if the
    /// existing resource matches").
    private func uploadVideoIdempotent(record: PendingAssessmentRecord, sessionId: String) async throws -> String {
        let stableVideoId = "VID-\(record.id)"
        let stableRecordId = "REC-\(record.id)"
        let metadata = PlayerVideoUploadMetadata(
            videoId: stableVideoId,
            recordId: stableRecordId,
            playerId: record.playerId,
            videoType: "assessment_smart_tracking",
            durationSeconds: record.durationSeconds ?? 0,
            sessionId: sessionId,
            locationId: "kemetfc_ios_field_capture",
            captureDevice: "iPhone (KemetFCTracker)",
            resolution: record.resolution ?? "",
            frameRateFps: record.frameRateFps ?? 0,
            schemaVersion: "1.0",
            createdBy: coachIdentifier
        )
        do {
            return try await videoUploadManager.uploadVideo(fileURL: pendingStore.localVideoURL(for: record), metadata: metadata, apiClient: apiClient)
        } catch KemetAPIError.server(409, _) {
            struct ExistingVideo: Decodable {
                let video_id: String
                let record_id: String
                let session_id: String
            }
            let existing = try await apiClient.get(path: "/videos/\(stableVideoId)", as: ExistingVideo.self)
            guard existing.video_id == stableVideoId,
                  existing.record_id == stableRecordId,
                  existing.session_id == sessionId
            else {
                throw VideoIdentityConflict(
                    videoId: stableVideoId,
                    detail: "GET /videos/\(stableVideoId) returned record_id=\(existing.record_id) session_id=\(existing.session_id), expected record_id=\(stableRecordId) session_id=\(sessionId)"
                )
            }
            // Verified: the existing server-side video genuinely belongs
            // to THIS recording (matching record_id AND session_id, both
            // themselves derived from/tied to this recording's own
            // clientRecordingId) — the 409 really does mean "already
            // uploaded by us," not a collision.
            return stableVideoId
        }
    }

    private func recordDiagnostics(request: String, startedAt: Date, error: String?) {
        let latencyMs = Date().timeIntervalSince(startedAt) * 1000
        backendDiagnostics = BackendDiagnostics(
            baseURL: backendDiagnostics.baseURL,
            isReachable: error == nil,
            lastRequestDescription: request,
            lastErrorDescription: error,
            lastLatencyMs: latencyMs
        )
    }
}

/// Bridges CameraCaptureManager's AVCaptureVideoDataOutputSampleBufferDelegate
/// callback to TrackingCoordinator.processFrame, computing session-elapsed
/// time from the sample buffer's own presentation timestamp rather than
/// wall-clock time (so a brief AVFoundation hiccup never desyncs
/// telemetry from the actual recorded video timeline).
private final class FrameForwarder: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let coordinator: TrackingCoordinator
    // Captured ONCE at init, on the MainActor (the caller — startAssessment
    // — is @MainActor-isolated), rather than read from
    // captureManagerRef.device inside captureOutput(...) below. That
    // read would be a real Swift 6 build error found only by a genuine
    // Xcode build (never caught by swiftc -typecheck, since this file
    // depends on AVFoundation/DockKit unavailable outside Xcode):
    // captureOutput(...) is a NONISOLATED delegate callback (invoked on
    // CameraCaptureManager's own dispatch queue), and CameraCaptureManager
    // .device is @MainActor-isolated — reading it from there is not
    // just unsafe, it does not compile under Swift 6 strict concurrency.
    // The negotiated capture device does not change for the lifetime of
    // one assessment, so capturing it once here is also correct, not
    // just a compile-error workaround.
    private let device: AVCaptureDevice
    private var sessionStartTime: CMTime?

    @MainActor
    init(coordinator: TrackingCoordinator, device: AVCaptureDevice) {
        self.coordinator = coordinator
        self.device = device
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        if sessionStartTime == nil { sessionStartTime = presentationTime }
        let elapsed = CMTimeGetSeconds(CMTimeSubtract(presentationTime, sessionStartTime ?? presentationTime))

        // Phase 3 fix: a real Xcode build flagged this Task closure
        // sending both `self` (implicitly, via `coordinator.processFrame
        // (...)` reading FrameForwarder's own stored property) and
        // `sampleBuffer` (a non-Sendable CMSampleBuffer) into a
        // `@MainActor` context. Capturing `coordinator` into a local
        // avoids the implicit `self` capture; boxing `sampleBuffer`
        // documents the same single-ownership handoff invariant as
        // TrackingCoordinator's own CVPixelBuffer box (see that file).
        let coordinator = self.coordinator
        let boxedDevice = UncheckedSendableBox(value: self.device)
        let boxedSampleBuffer = UncheckedSendableBox(value: sampleBuffer)
        Task { @MainActor in
            coordinator.processFrame(sampleBuffer: boxedSampleBuffer.value, captureDevice: boxedDevice.value, sessionElapsedSeconds: elapsed)
        }
    }
}
