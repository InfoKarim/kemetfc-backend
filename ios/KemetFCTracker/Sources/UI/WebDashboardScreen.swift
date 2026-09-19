//
//  WebDashboardScreen.swift
//  KemetFCTracker
//
//  Embeds the REAL web dashboard (the same pages served at
//  app.kemetfc.com — Players, Teams, Assessments, Training Plans, Drill
//  Library, Videos, Matches, Reports, Calendar, Messaging, Payment)
//  inside the app via WKWebView, rather than re-implementing every one
//  of those sections a second time natively. WKWebView keeps its own,
//  separate cookie store from URLSession's HTTPCookieStorage that
//  KemetAPIClient authenticates via login — this copies that session
//  cookie across first, so the coach is never asked to sign in twice.
//

import SwiftUI
import WebKit

struct WebDashboardScreen: View {
    /// The backend's origin (e.g. https://app.kemetfc.com), same as
    /// KemetAPIClient.baseURL — never guessed or hardcoded separately.
    let baseURL: URL
    let onClose: () -> Void

    @State private var isLoading = true

    var body: some View {
        ZStack(alignment: .top) {
            Color.kemetNavy.ignoresSafeArea()

            WebDashboardWebView(
                origin: baseURL,
                pageURL: baseURL.appendingPathComponent("dashboard"),
                isLoading: $isLoading
            )
            .ignoresSafeArea(edges: .bottom)
            .padding(.top, 52)

            HStack {
                Button(action: onClose) {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.left")
                        Text("Home")
                    }
                    .font(.subheadline.bold())
                    .foregroundStyle(.white)
                }
                Spacer()
                if isLoading {
                    ProgressView().tint(.white)
                }
            }
            .padding()
            .frame(height: 52)
            .background(Color.kemetNavy)
        }
    }
}

private struct WebDashboardWebView: UIViewRepresentable {
    let origin: URL
    let pageURL: URL
    @Binding var isLoading: Bool

    func makeCoordinator() -> Coordinator {
        Coordinator(isLoading: $isLoading)
    }

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.navigationDelegate = context.coordinator
        syncCookiesAndLoad(into: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    /// Copies every cookie URLSession already holds for this origin
    /// (the session + CSRF cookies set by POST /auth/login) into
    /// WKWebView's own WKHTTPCookieStore before the first load.
    private func syncCookiesAndLoad(into webView: WKWebView) {
        let cookieStore = webView.configuration.websiteDataStore.httpCookieStore
        let sharedCookies = HTTPCookieStorage.shared.cookies(for: origin) ?? []

        guard !sharedCookies.isEmpty else {
            webView.load(URLRequest(url: pageURL))
            return
        }

        let group = DispatchGroup()
        for cookie in sharedCookies {
            group.enter()
            cookieStore.setCookie(cookie) { group.leave() }
        }
        group.notify(queue: .main) {
            webView.load(URLRequest(url: pageURL))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        @Binding var isLoading: Bool

        init(isLoading: Binding<Bool>) {
            _isLoading = isLoading
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoading = false
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            isLoading = false
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            isLoading = false
        }
    }
}
