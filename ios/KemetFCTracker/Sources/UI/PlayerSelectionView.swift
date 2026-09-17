//
//  PlayerSelectionView.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  The coach's KEMET player identification step (spec section 5) —
//  QR / Search / Player ID / Assessment Session — happening BEFORE any
//  tracking begins. Reuses the existing KEMET player model shape
//  (app/db_models.py PlayerDB: first/last name, team, etc.) via
//  KemetAPIClient rather than inventing a parallel player concept.
//

import SwiftUI

public struct KemetPlayerSummary: Codable, Identifiable {
    public var playerId: String
    public var firstNameEn: String
    public var lastNameEn: String
    public var teamName: String?
    public var ageGroup: String?
    public var photoURL: URL?

    public var id: String { playerId }
    public var fullName: String { "\(firstNameEn) \(lastNameEn)" }

    enum CodingKeys: String, CodingKey {
        case playerId = "player_id"
        case firstNameEn = "first_name_en"
        case lastNameEn = "last_name_en"
        case teamName = "team_name"
        case ageGroup = "age_group"
        case photoURL = "photo_url"
    }
}

public struct PlayerSelectionView: View {
    public enum SelectionMethod: String, CaseIterable, Identifiable {
        case qrScan = "QR Scan"
        case search = "Search Player"
        case playerId = "Player ID"
        case session = "Assessment Session"
        public var id: String { rawValue }
    }

    @State private var method: SelectionMethod = .search
    @State private var searchText = ""
    @State private var searchResults: [KemetPlayerSummary] = []
    @State private var selected: KemetPlayerSummary?
    @State private var isSearching = false

    let onPlayerConfirmed: (KemetPlayerSummary) -> Void
    let searchPlayers: (String) async -> [KemetPlayerSummary]

    public init(
        onPlayerConfirmed: @escaping (KemetPlayerSummary) -> Void,
        searchPlayers: @escaping (String) async -> [KemetPlayerSummary]
    ) {
        self.onPlayerConfirmed = onPlayerConfirmed
        self.searchPlayers = searchPlayers
    }

    public var body: some View {
        VStack(spacing: 20) {
            Text("Select Assessment Player")
                .font(.title2).bold()

            Picker("Method", selection: $method) {
                ForEach(SelectionMethod.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)

            switch method {
            case .search, .playerId:
                searchSection
            case .qrScan:
                Text("QR scanning uses AVFoundation's AVCaptureMetadataOutput with metadataObjectTypes = [.qr] — reuses the SAME AVCaptureSession CameraCaptureManager already owns, added as a lightweight second output only during player selection, removed once a player is confirmed.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding()
            case .session:
                Text("Lists players already on today's Assessment Session roster via GET /players?session_id=... — reuses the existing assessment-session model, no new backend concept.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding()
            }

            if let selected {
                currentPlayerCard(selected)
            }

            Spacer()
        }
        .padding()
    }

    private var searchSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Search by name or ID", text: $searchText)
                .textFieldStyle(.roundedBorder)
                .onChange(of: searchText) { _, newValue in
                    Task {
                        isSearching = true
                        searchResults = await searchPlayers(newValue)
                        isSearching = false
                    }
                }

            if isSearching {
                ProgressView()
            }

            List(searchResults) { player in
                Button {
                    selected = player
                } label: {
                    HStack {
                        Text(player.fullName)
                        Spacer()
                        if let teamName = player.teamName {
                            Text(teamName).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .listStyle(.plain)
            .frame(maxHeight: 260)
        }
    }

    private func currentPlayerCard(_ player: KemetPlayerSummary) -> some View {
        VStack(spacing: 10) {
            Text("CURRENT PLAYER").font(.caption).bold().foregroundStyle(.secondary)
            AsyncImage(url: player.photoURL) { image in
                image.resizable().aspectRatio(contentMode: .fill)
            } placeholder: {
                Circle().fill(.gray.opacity(0.3))
            }
            .frame(width: 72, height: 72)
            .clipShape(Circle())

            Text(player.fullName).font(.headline)
            if let ageGroup = player.ageGroup {
                Text(ageGroup).font(.subheadline).foregroundStyle(.secondary)
            }
            if let teamName = player.teamName {
                Text(teamName).font(.subheadline).foregroundStyle(.secondary)
            }

            Button {
                onPlayerConfirmed(player)
            } label: {
                Text("START TRACKING")
                    .bold()
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding()
        .background(.thinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
