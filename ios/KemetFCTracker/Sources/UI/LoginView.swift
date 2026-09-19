//
//  LoginView.swift
//  KemetFCTracker
//
//  Native coach sign-in — POSTs to the EXISTING /auth/login endpoint
//  (main.py, same one the web dashboard's /login page uses) and reuses
//  the resulting session cookie for every later request via
//  KemetAPIClient (URLSession's shared HTTPCookieStorage). Replaces the
//  earlier "no native login view yet" stopgap (coachIdentifier was a
//  fabricated-looking placeholder until whatever authenticated a
//  WKWebView-hosted /login set it) with a real one.
//

import SwiftUI

struct LoginView: View {
    let onLogin: (String, String) async -> String?

    @State private var username = ""
    @State private var password = ""
    @State private var errorMessage: String?
    @State private var isSubmitting = false

    var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()

            VStack(spacing: 24) {
                Spacer()

                VStack(spacing: 4) {
                    Image("kemet_logo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 96, height: 96)
                    Text("KEMET FC")
                        .font(.largeTitle.bold())
                        .foregroundStyle(Color.kemetGold)
                    Text("Coach Sign In")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.8))
                }

                VStack(spacing: 12) {
                    TextField("Username", text: $username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.plain)
                        .padding(12)
                        .background(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))

                    SecureField("Password", text: $password)
                        .textFieldStyle(.plain)
                        .padding(12)
                        .background(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))

                    if let errorMessage {
                        Text(errorMessage)
                            .font(.subheadline)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                    }

                    Button {
                        submit()
                    } label: {
                        if isSubmitting {
                            ProgressView().tint(Color.kemetNavy)
                                .frame(maxWidth: .infinity)
                                .padding()
                        } else {
                            Text("Sign In")
                                .bold()
                                .frame(maxWidth: .infinity)
                                .padding()
                        }
                    }
                    .background(Color.kemetGold)
                    .foregroundStyle(Color.kemetNavy)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .disabled(isSubmitting || username.isEmpty || password.isEmpty)
                }
                .padding()
                .background(.white.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .padding(.horizontal, 24)

                Spacer()
                Spacer()
            }
        }
    }

    private func submit() {
        errorMessage = nil
        isSubmitting = true
        Task {
            let result = await onLogin(username, password)
            isSubmitting = false
            errorMessage = result
        }
    }
}
