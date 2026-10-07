//
//  SelectPlayerInVideoView.swift
//  KemetFCTracker
//
//  SELECT PLAYER IN VIDEO. After a video is saved (recorded or uploaded) the coach shows the AI which person in
//  it to follow: a frame is shown, the coach taps the player, the person under the tap is highlighted, and
//  CONFIRM PLAYER stores that choice with the assessment. CHOOSE AGAIN clears it.
//
//  The roster player says whose assessment this is; this screen says which person in the picture is that
//  player. The selection is a temporary tracking identity for this video, stored locally on the record. Nothing
//  here starts an analysis, uploads anything, recognises a face or changes the video.
//
//  The frame is decoded upright (the video's own rotation applied), shown aspect-fit, and every tap is mapped
//  back to the video's upright frame with VideoTargetGeometry, which refuses taps in the letterbox bands. A tap
//  that is not on a detected person selects no one; the nearest person is never guessed.
//

import AVFoundation
import SwiftUI

struct SelectPlayerInVideoView: View {
    @ObservedObject var store: PendingAssessmentStore
    let recordId: String
    let onClose: () -> Void

    @State private var image: CGImage?
    @State private var contentSize: CGSize = .zero
    @State private var timeSeconds: Double = 0
    @State private var duration: Double = 1
    @State private var people: [VideoPersonBox] = []
    @State private var state: VideoTargetSelectionState = .choosing
    @State private var message: String?
    @State private var isLoading = true
    @State private var loadFailure: String?
    @State private var lastTap: CGPoint?

    private var record: PendingAssessmentRecord? { store.record(id: recordId) }

    var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()
            VStack(spacing: 12) {
                header
                if let loadFailure {
                    Spacer()
                    Text(loadFailure).foregroundStyle(.orange).multilineTextAlignment(.center).padding()
                    Spacer()
                } else {
                    frameView
                    scrubber
                    Text(prompt).font(.subheadline).foregroundStyle(.white).multilineTextAlignment(.center).padding(.horizontal)
                    if let message { Text(message).font(.footnote).foregroundStyle(.orange).padding(.horizontal) }
                }
                buttons
            }
            .padding(.bottom, 12)
        }
        .task { await load() }
    }

    // MARK: pieces

    private var header: some View {
        VStack(spacing: 2) {
            Text("PLAYER ASSESSMENT").font(.caption).bold().foregroundStyle(.white.opacity(0.7))
            Text("Select the player in the video").font(.headline).foregroundStyle(.white)
            if let record { Text("Assessment for \(record.playerName)").font(.footnote).foregroundStyle(.white.opacity(0.7)) }
        }
        .padding(.top, 8)
    }

    private var prompt: String {
        switch state {
        case .choosing: return "Tap the player you want the AI to follow."
        case .selected: return "SELECTED PLAYER — confirm, or choose again."
        case .confirmed: return "Player selected. AI analysis is not available yet."
        }
    }

    private var frameView: some View {
        GeometryReader { proxy in
            let size = proxy.size
            ZStack {
                if let image {
                    Image(decorative: image, scale: 1).resizable().scaledToFit()
                        .frame(width: size.width, height: size.height)
                    overlay(size: size)
                } else if isLoading {
                    ProgressView().tint(.white).frame(width: size.width, height: size.height)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                SpatialTapGesture().onEnded { value in handleTap(value.location, viewSize: size) }
            )
        }
    }

    @ViewBuilder
    private func overlay(size: CGSize) -> some View {
        ForEach(Array(people.enumerated()), id: \.offset) { index, person in
            if let rect = VideoTargetGeometry.viewRect(forNormalized: person.box, viewSize: size, contentSize: contentSize) {
                let selected = { if case .selected(let i) = state { return i == index } else { return state == .confirmed && selectedIndex == index } }()
                Rectangle()
                    .path(in: rect)
                    .stroke(selected ? Color.kemetGold : Color.white.opacity(0.45), lineWidth: selected ? 4 : 1)
                if selected {
                    Text("SELECTED PLAYER").font(.caption2).bold().padding(4)
                        .background(Color.kemetGold).foregroundStyle(Color.kemetNavy)
                        .position(x: rect.midX, y: max(rect.minY - 10, 10))
                }
            }
        }
    }

    private var selectedIndex: Int? { if case .selected(let i) = state { return i } else { return confirmedIndex } }
    @State private var confirmedIndex: Int?

    private var scrubber: some View {
        VStack(spacing: 2) {
            Slider(value: $timeSeconds, in: 0...max(duration, 0.1)) { editing in
                if !editing { Task { await showFrame(at: timeSeconds) } }
            }
            .disabled(state == .confirmed)
            Text(String(format: "%.1f s of %.1f s", timeSeconds, duration)).font(.caption2).foregroundStyle(.white.opacity(0.7))
        }
        .padding(.horizontal)
    }

    private var buttons: some View {
        VStack(spacing: 10) {
            if state == .confirmed {
                Button("Done", action: onClose).buttonStyle(.borderedProminent)
            } else {
                Button("CONFIRM PLAYER") { confirm() }
                    .buttonStyle(.borderedProminent).frame(maxWidth: .infinity)
                    .disabled(!state.canConfirm)
                Button("CHOOSE AGAIN") { state = .choosing; message = nil }
                    .buttonStyle(.bordered).frame(maxWidth: .infinity)
                    .disabled(state == .choosing)
                Button("Back", action: onClose).font(.footnote).foregroundStyle(.white.opacity(0.7))
            }
        }
        .padding(.horizontal)
    }

    // MARK: actions

    private func handleTap(_ location: CGPoint, viewSize: CGSize) {
        guard state != .confirmed, image != nil else { return }
        lastTap = location
        guard let point = VideoTargetGeometry.normalizedPoint(forTap: location, viewSize: viewSize, contentSize: contentSize),
              let index = VideoTargetPicker.personIndex(at: point, among: people) else {
            state = .choosing
            message = VideoTargetPicker.noPlayerMessage
            return
        }
        message = nil
        state = .selected(index: index)
        tapNormalized = point
    }

    @State private var tapNormalized: CGPoint?

    private func confirm() {
        guard case .selected(let index) = state, people.indices.contains(index), let tap = tapNormalized, var record else { return }
        let person = people[index]
        let selection = VideoTargetSelection(
            label: "TARGET-\((record.targetSelections?.count ?? 0) + 1)", source: VideoTargetSelection.coachTapSource,
            timeSeconds: timeSeconds, tapX: Double(tap.x), tapY: Double(tap.y),
            boxX: Double(person.box.minX), boxY: Double(person.box.minY), boxWidth: Double(person.box.width), boxHeight: Double(person.box.height),
            detectionConfidence: person.confidence, detectorVersion: VideoFramePersonDetector.detectorVersion,
            videoSha256: record.contentSha256, confirmedAt: Date())
        record.targetSelections = (record.targetSelections ?? []) + [selection]
        store.upsert(record)
        confirmedIndex = index
        state = .confirmed
    }

    private func load() async {
        guard let record else { loadFailure = "This video is not on this iPhone any more."; return }
        let url = store.localVideoURL(for: record)
        let asset = AVURLAsset(url: url)
        do {
            let length = try await asset.load(.duration).seconds
            guard length.isFinite, length > 0, let track = try await asset.loadTracks(withMediaType: .video).first else {
                loadFailure = "This video cannot be opened."; return
            }
            let (natural, transform) = try await track.load(.naturalSize, .preferredTransform)
            contentSize = VideoTargetGeometry.uprightSize(natural: natural, preferredTransform: transform)
            duration = length
            // not frame 0: sample the video and offer the best frame (people visible, sharp, not dark)
            var candidates: [VideoFrameSuggestion.Candidate] = []
            let samples = 12
            for k in 0..<samples {
                let t = length * (Double(k) + 0.5) / Double(samples)
                if let frame = try? await frame(of: asset, at: t) {
                    let q = VideoFramePersonDetector.quality(of: frame)
                    let found = (try? VideoFramePersonDetector.people(in: frame)) ?? []
                    candidates.append(.init(timeSeconds: t, people: found, brightness: q.brightness, sharpness: q.sharpness))
                }
            }
            let start = VideoFrameSuggestion.best(candidates)?.timeSeconds ?? length / 2
            timeSeconds = start
            await showFrame(at: start)
            if people.isEmpty { message = "No people found on this frame. Move the slider to a frame where the player is visible." }
        } catch {
            loadFailure = "This video cannot be opened. \(error.localizedDescription)"
        }
        isLoading = false
    }

    private func frame(of asset: AVURLAsset, at seconds: Double) async throws -> CGImage {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true          // upright: the video's own rotation is applied
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        generator.maximumSize = CGSize(width: 1600, height: 1600)
        return try await generator.image(at: CMTime(seconds: seconds, preferredTimescale: 600)).image
    }

    private func showFrame(at seconds: Double) async {
        guard let record else { return }
        let asset = AVURLAsset(url: store.localVideoURL(for: record))
        do {
            let frame = try await frame(of: asset, at: seconds)
            image = frame
            // the picture may be scaled down for the screen; the shape is what matters, so keep the upright video size
            people = (try? VideoFramePersonDetector.people(in: frame)) ?? []
            state = .choosing
            tapNormalized = nil
            message = people.isEmpty ? "No people found on this frame. Move the slider to a frame where the player is visible." : nil
        } catch {
            message = "That frame could not be read. Try another position."
        }
    }
}
