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
        webView.uiDelegate = context.coordinator
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

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
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

        // Without a WKUIDelegate, WKWebView has no default presentation
        // for JS alert()/confirm()/prompt() — it just no-ops the
        // completion handler, which is why "Delete Plan" (window.confirm)
        // on the embedded web dashboard silently did nothing. These three
        // present the real native dialog and forward the user's choice
        // back to the page's own JS exactly like a real browser would.
        func webView(
            _ webView: WKWebView,
            runJavaScriptAlertPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping () -> Void
        ) {
            presentAlert(message: message, hasCancel: false) { _ in completionHandler() }
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptConfirmPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (Bool) -> Void
        ) {
            presentAlert(message: message, hasCancel: true) { confirmed in
                completionHandler(confirmed)
            }
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptTextInputPanelWithPrompt prompt: String,
            defaultText: String?,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (String?) -> Void
        ) {
            let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
            alert.addTextField { $0.text = defaultText }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in
                completionHandler(nil)
            })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in
                completionHandler(alert.textFields?.first?.text)
            })
            Self.topViewController()?.present(alert, animated: true)
        }

        private func presentAlert(message: String, hasCancel: Bool, completion: @escaping (Bool) -> Void) {
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            if hasCancel {
                alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completion(false) })
            }
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completion(true) })

            guard let presenter = Self.topViewController() else {
                completion(hasCancel ? false : true)
                return
            }
            presenter.present(alert, animated: true)
        }

        private static func topViewController() -> UIViewController? {
            let scene = UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .first { $0.activationState == .foregroundActive }
            guard var top = scene?.keyWindow?.rootViewController else { return nil }
            while let presented = top.presentedViewController {
                top = presented
            }
            return top
        }
    }
}
