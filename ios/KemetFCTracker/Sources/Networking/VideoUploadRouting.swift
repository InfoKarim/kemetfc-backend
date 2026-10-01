//
//  VideoUploadRouting.swift
//  KemetFCTracker
//
//  Phase 2.5 Part D (2026-10-01): the ONE place that decides legacy vs.
//  multipart upload by file size — never scattered inline size checks.
//  `AssessmentSessionViewModel.uploadVideoIdempotent` asks this, and
//  nothing else, before choosing which uploader to invoke.
//
//  Threshold rationale: Cloudflare's own documented request-body limits
//  for the plan tiers this project has actually run under (Free/Pro) are
//  100MB — well under the legacy direct-upload path's old 500MB-2GB
//  application-level limits, meaning a file over ~100MB is already at
//  risk of being rejected by the CDN/proxy layer before the app's own
//  size check ever runs. 64MB is used instead of 100MB to stay
//  comfortably under that infrastructure ceiling with margin, since the
//  real failure mode this phase exists to fix (a 1.35GB match recording)
//  is ~20x over either number — the exact boundary only matters for
//  typical short clips, which are all well under 64MB.
//
//  The real match's 1,351,186,620 bytes is far above this threshold in
//  either case and always routes to .multipartUpload.
//

import Foundation

public enum VideoUploadRoute: Equatable, Sendable {
    case legacyDirectUpload
    case multipartUpload
}

public enum VideoUploadRouter {
    public static let multipartRoutingThresholdBytes: Int64 = 64 * 1024 * 1024

    public static func route(forFileSizeBytes fileSizeBytes: Int64) -> VideoUploadRoute {
        fileSizeBytes > multipartRoutingThresholdBytes ? .multipartUpload : .legacyDirectUpload
    }
}
