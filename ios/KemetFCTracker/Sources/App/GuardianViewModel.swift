//
//  GuardianViewModel.swift
//  KemetFCTracker
//
//  Native replica of app/static/guardian_home.html's own data flow: load
//  linked children, then for the selected one, fetch the SAME four
//  existing endpoints that page calls (development-report, assessments,
//  billing status, billing payments) — reused as-is, no parallel
//  guardian API surface invented for the app.
//

import Foundation

@MainActor
public final class GuardianViewModel: ObservableObject {
    @Published public private(set) var children: [GuardianChildSummary] = []
    @Published public var selectedChildId: String?
    @Published public private(set) var report: DevelopmentReport?
    @Published public private(set) var assessments: [PlayerAssessmentSummary] = []
    @Published public private(set) var billingStatus: BillingStatus?
    @Published public private(set) var payments: [GuardianPayment] = []
    @Published public private(set) var isLoading = false
    @Published public private(set) var errorMessage: String?

    private let apiClient: KemetAPIClient

    public init(apiClient: KemetAPIClient) {
        self.apiClient = apiClient
    }

    public var selectedChild: GuardianChildSummary? {
        children.first { $0.id == selectedChildId }
    }

    public func loadChildren() async {
        errorMessage = nil
        do {
            children = try await apiClient.get(path: "/guardian/children", as: [GuardianChildSummary].self)
        } catch {
            errorMessage = "Could not load your linked players."
            return
        }

        guard !children.isEmpty else { return }
        if selectedChildId == nil || !children.contains(where: { $0.id == selectedChildId }) {
            selectedChildId = children[0].id
        }
        if let selectedChildId {
            await loadReport(for: selectedChildId)
        }
    }

    public func selectChild(_ playerId: String) {
        selectedChildId = playerId
        Task { await loadReport(for: playerId) }
    }

    /// Mirrors guardian_home.html's loadPlayerReport(playerId) exactly:
    /// the development report is required (a failure here is the only
    /// thing shown as an error); assessments/billing/payments are
    /// best-effort — a failure on any of those degrades to an empty
    /// section rather than blocking the whole screen.
    public func loadReport(for playerId: String) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            report = try await apiClient.get(
                path: "/players/\(playerId)/development-report",
                as: DevelopmentReport.self
            )
        } catch {
            report = nil
            errorMessage = "Could not load this player's development report."
            return
        }

        struct AssessmentsResponse: Decodable { let assessments: [PlayerAssessmentSummary] }
        assessments = (try? await apiClient.get(
            path: "/players/\(playerId)/assessments",
            as: AssessmentsResponse.self
        ))?.assessments ?? []

        billingStatus = try? await apiClient.get(
            path: "/billing/status/\(playerId)",
            as: BillingStatus.self
        )

        struct PaymentsResponse: Decodable { let payments: [GuardianPayment] }
        payments = (try? await apiClient.get(
            path: "/billing/payments/\(playerId)",
            as: PaymentsResponse.self
        ))?.payments ?? []
    }

    public func assessments(forPillar pillar: String) -> [PlayerAssessmentSummary] {
        assessments.filter { $0.pillar == pillar }
    }
}
