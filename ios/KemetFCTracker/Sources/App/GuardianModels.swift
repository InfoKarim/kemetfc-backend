//
//  GuardianModels.swift
//  KemetFCTracker
//
//  Response shapes for the EXISTING guardian-facing endpoints (already
//  built, tested, and serving app/static/guardian_home.html on the web
//  dashboard) — reused as-is, never a parallel guardian data model.
//

import Foundation

/// GET /guardian/children.
public struct GuardianChildSummary: Codable, Identifiable {
    public var playerId: String
    public var name: String
    public var teamId: String?

    public var id: String { playerId }
    public var firstName: String { name.split(separator: " ").first.map(String.init) ?? name }

    enum CodingKeys: String, CodingKey {
        case playerId = "player_id"
        case name
        case teamId = "team_id"
    }
}

/// GET /players/{id}/development-report — app/services/development_report_service.py build_development_report.
public struct DevelopmentReport: Decodable {
    public struct Coverage: Decodable {
        public let coveredPillars: [String]
        public let pillars: [String]
        public let coveragePercent: Int
        enum CodingKeys: String, CodingKey {
            case coveredPillars = "covered_pillars"
            case pillars
            case coveragePercent = "coverage_percent"
        }
    }
    public struct ProgressItem: Decodable, Identifiable {
        public let label: String
        public let change: Double
        public var id: String { label }
    }
    public struct NotedItem: Decodable, Identifiable {
        public let title: String
        public let detail: String
        public var id: String { title }
    }
    public struct CoachMessage: Decodable {
        public let message: String?
        public let nextFocus: [String]
        enum CodingKeys: String, CodingKey {
            case message
            case nextFocus = "next_focus"
        }
    }

    public let coverage: Coverage
    public let progress: [ProgressItem]
    public let strengths: [NotedItem]
    public let priorities: [NotedItem]
    public let coachMessage: CoachMessage

    enum CodingKeys: String, CodingKey {
        case coverage, progress, strengths, priorities
        case coachMessage = "coach_message"
    }
}

/// A JSON value of unknown shape — PlayerAssessmentDB.raw_data varies by
/// test_type (Yo-Yo Kids: numbers; Ball Mastery: integers 1-5), so this
/// decodes whatever is there rather than assuming one type, matching the
/// web page's own `Object.entries(a.raw_data)` (no assumed shape either).
public enum JSONValue: Decodable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            self = .null
        }
    }

    public var displayText: String {
        switch self {
        case .string(let value): return value
        case .number(let value): return value.truncatingRemainder(dividingBy: 1) == 0
            ? String(Int(value)) : String(value)
        case .bool(let value): return value ? "true" : "false"
        case .null: return "—"
        }
    }
}

/// GET /players/{id}/assessments — one row of PlayerAssessmentDB.
public struct PlayerAssessmentSummary: Decodable, Identifiable {
    public let assessmentId: String
    public let pillar: String
    public let testType: String
    public let testDate: String
    public let rawData: [String: JSONValue]

    public var id: String { assessmentId }

    enum CodingKeys: String, CodingKey {
        case assessmentId = "assessment_id"
        case pillar
        case testType = "test_type"
        case testDate = "test_date"
        case rawData = "raw_data"
    }
}

/// GET /billing/status/{id} — app/routers/billing.py get_billing_status.
public struct BillingStatus: Decodable {
    public struct Subscription: Decodable {
        public let status: String
        public let currentPeriodEnd: Date?
        public let cancelAtPeriodEnd: Bool
        enum CodingKeys: String, CodingKey {
            case status
            case currentPeriodEnd = "current_period_end"
            case cancelAtPeriodEnd = "cancel_at_period_end"
        }
    }
    public let configured: Bool
    public let subscription: Subscription?
}

/// GET /billing/payments/{id} — app/routers/billing.py _payment_payload.
public struct GuardianPayment: Decodable, Identifiable {
    public let stripeInvoiceId: String
    public let amount: Int
    public let currency: String
    public let status: String
    public let hostedInvoiceUrl: String?
    public let createdAt: Date

    public var id: String { stripeInvoiceId }

    enum CodingKeys: String, CodingKey {
        case stripeInvoiceId = "stripe_invoice_id"
        case amount, currency, status
        case hostedInvoiceUrl = "hosted_invoice_url"
        case createdAt = "created_at"
    }
}
