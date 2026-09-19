//
//  GuardianHomeView.swift
//  KemetFCTracker
//
//  Native equivalent of app/static/guardian_home.html — same sections,
//  same underlying endpoints (via GuardianViewModel), same "never
//  fabricate a number" rule the web page documents for the coverage
//  score (spec: only ever "—" or "Assessment in progress", never an
//  invented composite score).
//

import SwiftUI

private let pillarOrder: [(key: String, label: String)] = [
    ("physical", "Physical"),
    ("technical", "Technical"),
    ("tactical", "Tactical"),
    ("coach", "Coach Assessment"),
]

struct GuardianHomeView: View {
    @StateObject var viewModel: GuardianViewModel
    let onSignOut: () async -> Void
    var currentUsername: String? = nil
    var avatarURL: URL? = nil
    var onUploadAvatar: (Data) async -> String? = { _ in nil }
    var onRemoveAvatar: () async -> Void = {}

    @State private var expandedPillar: String?
    @State private var isSigningOut = false

    var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()

            VStack(spacing: 0) {
                header

                if !viewModel.children.isEmpty || viewModel.children.count > 1 {
                    childSwitcher
                }

                ScrollView {
                    VStack(spacing: 14) {
                        content
                    }
                    .padding()
                }
            }
        }
        .task { await viewModel.loadChildren() }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("KEMET FC").font(.headline.bold()).foregroundStyle(.white)
                Text("GUARDIAN PORTAL")
                    .font(.caption2.bold())
                    .foregroundStyle(.white.opacity(0.6))
            }
            Spacer()
            AccountAvatarControl(
                username: currentUsername,
                avatarURL: avatarURL,
                onUpload: onUploadAvatar,
                onRemove: onRemoveAvatar
            )
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
        .padding()
    }

    @ViewBuilder
    private var childSwitcher: some View {
        if viewModel.children.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(viewModel.children) { child in
                        Button {
                            expandedPillar = nil
                            viewModel.selectChild(child.id)
                        } label: {
                            Text(child.name)
                                .font(.subheadline.weight(.semibold))
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(child.id == viewModel.selectedChildId ? Color.kemetGold : Color.white.opacity(0.08))
                                .foregroundStyle(child.id == viewModel.selectedChildId ? Color.kemetNavy : .white)
                                .clipShape(Capsule())
                        }
                    }
                }
                .padding(.horizontal)
            }
            .padding(.bottom, 8)
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading && viewModel.report == nil {
            ProgressView().tint(.white).padding(.top, 60)
        } else if let errorMessage = viewModel.errorMessage, viewModel.report == nil {
            emptyCard(errorMessage)
        } else if viewModel.children.isEmpty {
            emptyCard("No player is linked to your account yet. Please contact KEMET FC staff to link your child's profile.")
        } else if let report = viewModel.report, let child = viewModel.selectedChild {
            playerAndCoverageCard(child: child, report: report)
            if !report.progress.isEmpty { progressCard(report.progress) }
            strengthsCard(report.strengths)
            prioritiesCard(report.priorities)
            coachMessageCard(report.coachMessage)
            exploreAssessmentCard()
            membershipCard()
            if !viewModel.payments.isEmpty { paymentHistoryCard() }
        }
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) { content() }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.white)
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption.bold())
            .foregroundStyle(.secondary)
    }

    private func emptyCard(_ text: String) -> some View {
        card {
            Text(text)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)
        }
    }

    private func playerAndCoverageCard(child: GuardianChildSummary, report: DevelopmentReport) -> some View {
        card {
            HStack(spacing: 12) {
                Circle()
                    .fill(Color.kemetNavy)
                    .frame(width: 52, height: 52)
                    .overlay(
                        Text(String(child.name.prefix(2)).uppercased())
                            .font(.headline.bold())
                            .foregroundStyle(Color.kemetGold)
                    )
                VStack(alignment: .leading, spacing: 2) {
                    Text(child.name).font(.title3.bold()).foregroundStyle(Color.kemetNavy)
                    Text(child.teamId.map { "Team \($0)" } ?? "No team assigned")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            VStack(spacing: 6) {
                Text("PLAYER DEVELOPMENT SCORE")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                Text(report.coverage.coveragePercent >= 100 ? "—" : "Assessment in progress")
                    .font(.title2.bold())
                    .foregroundStyle(Color.kemetGold)
                Text("We're building a complete picture of \(child.firstName)'s development. Assessment coverage: \(report.coverage.coveragePercent)% (\(report.coverage.coveredPillars.count) of \(report.coverage.pillars.count) areas assessed so far).")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 8)
        }
    }

    private func progressCard(_ items: [DevelopmentReport.ProgressItem]) -> some View {
        card {
            sectionTitle("Progress Since Last Assessment")
            ForEach(items) { item in
                HStack {
                    Text(item.label).font(.subheadline).foregroundStyle(Color.kemetNavy)
                    Spacer()
                    let arrow = item.change > 0 ? "↑" : (item.change < 0 ? "↓" : "→")
                    let sign = item.change > 0 ? "+" : ""
                    Text("\(arrow) \(sign)\(formatNumber(item.change))")
                        .font(.subheadline.bold())
                        .foregroundStyle(item.change >= 0 ? .green : .red)
                }
                Divider()
            }
        }
    }

    private func strengthsCard(_ items: [DevelopmentReport.NotedItem]) -> some View {
        card {
            sectionTitle("Current Strengths")
            if items.isEmpty {
                Text("Strengths will appear here once assessment results are recorded.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("★ \(item.title.uppercased())").font(.subheadline.bold()).foregroundStyle(Color.kemetNavy)
                        Text(item.detail).font(.footnote).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func prioritiesCard(_ items: [DevelopmentReport.NotedItem]) -> some View {
        card {
            sectionTitle("Current Development Priorities")
            if items.isEmpty {
                Text("Development priorities will appear here once assessment results are recorded.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    VStack(alignment: .leading, spacing: 2) {
                        (Text("0\(index + 1) — ").foregroundStyle(Color.kemetGold).bold()
                            + Text(item.title.uppercased()).foregroundStyle(Color.kemetNavy).bold())
                            .font(.subheadline)
                        Text(item.detail).font(.footnote).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func coachMessageCard(_ message: DevelopmentReport.CoachMessage) -> some View {
        card {
            sectionTitle("Message From Your Coach")
            if let text = message.message, !text.isEmpty {
                Text("\"\(text)\"")
                    .font(.subheadline.italic())
                    .foregroundStyle(Color.kemetNavy)
            } else {
                Text("No message from the coaching staff yet.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if !message.nextFocus.isEmpty {
                sectionTitle("Next Development Focus")
                FlowLayout(spacing: 8) {
                    ForEach(message.nextFocus, id: \.self) { focus in
                        Text(focus)
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.kemetNavy)
                            .foregroundStyle(Color.kemetGold)
                            .clipShape(Capsule())
                    }
                }
            }
        }
    }

    private func exploreAssessmentCard() -> some View {
        card {
            sectionTitle("Explore The Assessment")
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(pillarOrder, id: \.key) { pillar in
                    Button {
                        expandedPillar = expandedPillar == pillar.key ? nil : pillar.key
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(pillar.label).font(.subheadline.bold()).foregroundStyle(.white)
                            Text("View Details →").font(.caption2).foregroundStyle(.white.opacity(0.6))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(Color.kemetNavy)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
            }

            if let expandedPillar {
                let label = pillarOrder.first { $0.key == expandedPillar }?.label ?? expandedPillar
                let matching = viewModel.assessments(forPillar: expandedPillar)
                VStack(alignment: .leading, spacing: 8) {
                    sectionTitle("\(label) — Assessment History")
                    if matching.isEmpty {
                        Text("No assessments recorded yet for this area.")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        ForEach(matching) { assessment in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(assessment.testType) — \(assessment.testDate)")
                                    .font(.footnote.bold()).foregroundStyle(Color.kemetNavy)
                                Text(assessment.rawData
                                    .sorted { $0.key < $1.key }
                                    .map { "\($0.key): \($0.value.displayText)" }
                                    .joined(separator: " · "))
                                    .font(.footnote).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 4)
                            Divider()
                        }
                    }
                }
                .padding(.top, 8)
            }
        }
    }

    private func membershipCard() -> some View {
        card {
            sectionTitle("Membership")

            if viewModel.billingStatus?.configured != true {
                Text("Membership payments are not yet set up for this academy. Contact KEMET FC staff for details.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if let subscription = viewModel.billingStatus?.subscription {
                let badge = membershipBadge(for: subscription)
                Text(badge.0)
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(badge.1.opacity(0.15))
                    .foregroundStyle(badge.1)
                    .clipShape(Capsule())

                if subscription.cancelAtPeriodEnd, let end = subscription.currentPeriodEnd {
                    Text("Membership will end on \(formatDate(end)).")
                        .font(.footnote).foregroundStyle(.secondary)
                } else if subscription.status == "active", let end = subscription.currentPeriodEnd {
                    HStack {
                        Text("Next Payment").font(.subheadline).foregroundStyle(Color.kemetNavy)
                        Spacer()
                        Text(formatDate(end)).font(.subheadline.bold()).foregroundStyle(Color.kemetNavy)
                    }
                }
            } else {
                Text("No membership on file.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private func membershipBadge(for subscription: BillingStatus.Subscription) -> (String, Color) {
        switch subscription.status {
        case "active", "trialing": return ("✓ ACTIVE", .green)
        case "past_due": return ("PAST DUE", .orange)
        case "unpaid": return ("PAYMENT DUE", .orange)
        case "canceled": return ("CANCELLED", .secondary)
        default: return (subscription.status.uppercased(), .secondary)
        }
    }

    private func paymentHistoryCard() -> some View {
        card {
            sectionTitle("Payment History")
            ForEach(viewModel.payments.prefix(6)) { payment in
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(formatDate(payment.createdAt)).font(.subheadline).foregroundStyle(Color.kemetNavy)
                        Text(payment.status == "paid" ? "Membership — Paid" : "Membership — Payment failed")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(formatMoney(payment.amount, currency: payment.currency))
                            .font(.subheadline.bold()).foregroundStyle(.green)
                        if let url = payment.hostedInvoiceUrl, let link = URL(string: url) {
                            Link("Receipt →", destination: link)
                                .font(.caption2)
                        }
                    }
                }
                Divider()
            }
        }
    }

    private func formatNumber(_ value: Double) -> String {
        value.truncatingRemainder(dividingBy: 1) == 0 ? String(Int(value)) : String(format: "%.2f", value)
    }

    private func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .long
        return formatter.string(from: date)
    }

    private func formatMoney(_ cents: Int, currency: String) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currency.uppercased()
        return formatter.string(from: NSNumber(value: Double(cents) / 100)) ?? "—"
    }
}

/// Minimal wrapping HStack for the "next focus" chips — SwiftUI has no
/// built-in flow layout pre-iOS 16's `Layout` protocol equivalent here
/// that's simpler than this for a handful of short chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x: CGFloat = bounds.minX, y: CGFloat = bounds.minY, rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
