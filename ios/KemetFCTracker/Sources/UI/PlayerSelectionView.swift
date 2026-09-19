//
//  PlayerSelectionView.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  The coach's KEMET player identification step (spec section 5) —
//  Search / Shirt Number / Player ID / Assessment Session — happening
//  BEFORE any tracking begins. Reuses the existing KEMET player model
//  shape (app/db_models.py PlayerDB: first/last name, team, jersey
//  number, etc.) via KemetAPIClient rather than inventing a parallel
//  player concept.
//
//  QR Scan was removed at the product owner's request in favor of Shirt
//  Number (a coach reads the jersey number off the player and types it
//  in — there is no camera-based automatic jersey-number recognition
//  model, so this is never faked as one). The backend's QR check-in
//  token endpoints (app/routers/player_checkin.py) are untouched and
//  still tested — only this screen's use of them was removed.
//

import SwiftUI

public struct KemetPlayerSummary: Codable, Identifiable {
    public var playerId: String
    public var firstNameEn: String
    public var lastNameEn: String
    public var teamName: String?
    public var ageGroup: String?
    public var jerseyNumber: Int?
    public var photoURL: URL?

    public var id: String { playerId }
    public var fullName: String { "\(firstNameEn) \(lastNameEn)" }

    enum CodingKeys: String, CodingKey {
        case playerId = "player_id"
        case firstNameEn = "first_name_en"
        case lastNameEn = "last_name_en"
        case teamName = "team_name"
        case ageGroup = "age_group"
        case jerseyNumber = "jersey_number"
        case photoURL = "photo_url"
    }
}

public struct PlayerSelectionView: View {
    public enum SelectionMethod: String, CaseIterable, Identifiable {
        case search = "Search Player"
        case shirtNumber = "Shirt Number"
        case playerId = "Player ID"
        case session = "Assessment Session"
        public var id: String { rawValue }
    }

    @State private var method: SelectionMethod = .search
    @State private var searchText = ""
    @State private var searchResults: [KemetPlayerSummary] = []
    @State private var selected: KemetPlayerSummary?
    @State private var isSearching = false

    @State private var jerseyNumberText = ""
    @State private var jerseyResults: [KemetPlayerSummary] = []
    @State private var jerseyStatus: String?
    @State private var isLookingUpJersey = false

    let onPlayerConfirmed: (KemetPlayerSummary) -> Void
    let searchPlayers: (String) async -> [KemetPlayerSummary]
    let findByJerseyNumber: (Int) async -> [KemetPlayerSummary]
    let onSignOut: () async -> Void
    let currentUsername: String?

    @State private var isSigningOut = false

    public init(
        onPlayerConfirmed: @escaping (KemetPlayerSummary) -> Void,
        searchPlayers: @escaping (String) async -> [KemetPlayerSummary],
        findByJerseyNumber: @escaping (Int) async -> [KemetPlayerSummary],
        onSignOut: @escaping () async -> Void,
        currentUsername: String? = nil
    ) {
        self.onPlayerConfirmed = onPlayerConfirmed
        self.searchPlayers = searchPlayers
        self.findByJerseyNumber = findByJerseyNumber
        self.onSignOut = onSignOut
        self.currentUsername = currentUsername
    }

    public var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()

            VStack(spacing: 20) {
                HStack {
                    Text("Select Assessment Player")
                        .font(.title2).bold()
                        .foregroundStyle(.white)
                    Spacer()
                    accountBadge
                    Button {
                        isSigningOut = true
                        Task {
                            await onSignOut()
                            isSigningOut = false
                        }
                    } label: {
                        Text(isSigningOut ? "Signing out..." : "Sign out")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.bordered)
                    .tint(.white)
                    .disabled(isSigningOut)
                }

                methodTabs

                switch method {
                case .search, .playerId:
                    searchSection
                case .shirtNumber:
                    shirtNumberSection
                case .session:
                    Text("Lists players already on today's Assessment Session roster via GET /players?session_id=... — reuses the existing assessment-session model, no new backend concept.")
                        .font(.footnote)
                        .foregroundStyle(.white.opacity(0.7))
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(.white.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                }

                if let selected {
                    currentPlayerCard(selected)
                }

                Spacer()
            }
            .padding()
        }
    }

    /// Matches the web dashboard's own `#account-avatar` treatment
    /// (auth_client.js ensureAccountWidget): a white circle with the
    /// signed-in user's initials in bold navy — same visual identity
    /// element on both platforms, never a fabricated photo.
    @ViewBuilder
    private var accountBadge: some View {
        if let currentUsername, !currentUsername.isEmpty {
            HStack(spacing: 6) {
                Circle()
                    .fill(.white)
                    .frame(width: 28, height: 28)
                    .overlay(
                        Text(String(currentUsername.prefix(2)).uppercased())
                            .font(.caption2.bold())
                            .foregroundStyle(Color.kemetNavy)
                    )
                Text(currentUsername)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.85))
            }
        }
    }

    private var methodTabs: some View {
        HStack(spacing: 8) {
            ForEach(SelectionMethod.allCases) { candidate in
                Button {
                    method = candidate
                } label: {
                    Text(candidate.rawValue)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .frame(maxWidth: .infinity)
                        .background(method == candidate ? Color.kemetGold : Color.white.opacity(0.08))
                        .foregroundStyle(method == candidate ? Color.kemetNavy : .white)
                        .clipShape(Capsule())
                }
            }
        }
    }

    private var searchSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Search by name or ID", text: $searchText)
                .textFieldStyle(.plain)
                .padding(12)
                .background(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .onChange(of: searchText) { _, newValue in
                    Task {
                        isSearching = true
                        searchResults = await searchPlayers(newValue)
                        isSearching = false
                    }
                }

            if isSearching {
                ProgressView().tint(.white)
            }

            playerResultsList(searchResults)
        }
    }

    /// Shirt Number tab: the coach reads the number off the player's
    /// jersey and types it in — matches app/static/add_video.html's web
    /// picker exactly, including that a number is not globally unique
    /// (only unique within a team), so every match is shown for the
    /// coach to disambiguate rather than picking one silently.
    private var shirtNumberSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                TextField("Shirt number, e.g. 10", text: $jerseyNumberText)
                    .keyboardType(.numberPad)
                    .textFieldStyle(.plain)
                    .padding(12)
                    .background(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                Button("Find") {
                    lookUpJerseyNumber()
                }
                .buttonStyle(.borderedProminent)
                .tint(Color.kemetGold)
                .foregroundStyle(Color.kemetNavy)
            }

            if isLookingUpJersey {
                ProgressView().tint(.white)
            }

            if let jerseyStatus {
                Text(jerseyStatus)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
            }

            playerResultsList(jerseyResults)
        }
    }

    private func lookUpJerseyNumber() {
        let trimmed = jerseyNumberText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let number = Int(trimmed), (1...99).contains(number) else {
            jerseyStatus = "Enter a shirt number between 1 and 99."
            jerseyResults = []
            return
        }

        Task {
            isLookingUpJersey = true
            jerseyResults = await findByJerseyNumber(number)
            isLookingUpJersey = false
            jerseyStatus = jerseyResults.isEmpty
                ? "No player is wearing #\(number)."
                : (jerseyResults.count == 1
                    ? "1 player found."
                    : "\(jerseyResults.count) players found wearing #\(number) — pick the right one.")
        }
    }

    private func playerResultsList(_ players: [KemetPlayerSummary]) -> some View {
        List(players) { player in
            Button {
                selected = player
            } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(player.fullName).foregroundStyle(Color.kemetNavy)
                        Text(playerMetaText(player))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
            .listRowBackground(Color.white)
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .frame(maxHeight: 260)
        .background(.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func playerMetaText(_ player: KemetPlayerSummary) -> String {
        var parts: [String] = [player.playerId]
        if let teamName = player.teamName { parts.append(teamName) }
        if let ageGroup = player.ageGroup { parts.append(ageGroup) }
        if let jerseyNumber = player.jerseyNumber { parts.append("#\(jerseyNumber)") }
        return parts.joined(separator: " · ")
    }

    private func currentPlayerCard(_ player: KemetPlayerSummary) -> some View {
        VStack(spacing: 10) {
            Text("CURRENT PLAYER").font(.caption).bold().foregroundStyle(.secondary)
            AsyncImage(url: player.photoURL) { image in
                image.resizable().aspectRatio(contentMode: .fill)
            } placeholder: {
                Circle().fill(Color.kemetNavy.opacity(0.15))
            }
            .frame(width: 72, height: 72)
            .clipShape(Circle())
            .overlay(Circle().stroke(Color.kemetGold, lineWidth: 2))

            Text(player.fullName).font(.headline).foregroundStyle(Color.kemetNavy)
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
                    .background(Color.kemetGold)
                    .foregroundStyle(Color.kemetNavy)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding()
        .background(.white)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
