//
//  KemetTheme.swift
//  KemetFCTracker
//
//  KEMET FC's brand colors, used sparingly (spec section 11: "for
//  identity and active states", never as a dominant background) across
//  the production assessment-camera UI.
//

import SwiftUI

extension Color {
    /// Deep navy — KEMET FC's primary identity color.
    static let kemetNavy = Color(red: 0.05, green: 0.09, blue: 0.20)
    /// Warm gold accent — locked-target brackets, active-state highlights.
    static let kemetGold = Color(red: 0.83, green: 0.68, blue: 0.30)
}
