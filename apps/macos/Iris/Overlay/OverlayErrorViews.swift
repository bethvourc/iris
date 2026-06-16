import IrisKit
import SwiftUI

/// Renders a failure with exactly one primary recovery action. No raw error
/// codes — each case has a human title and a single button (docs/desktop §8).
struct OverlayErrorView: View {
    let error: VoiceSessionViewModel.OverlayError
    let onRestart: () -> Void
    let onOpenDiagnostics: () -> Void
    let onOpenMicrophoneSettings: () -> Void
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .foregroundStyle(.orange)
                    .accessibilityHidden(true)
                Text(title)
                    .font(.system(size: 14))
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            // Combine only the message; the recovery Button stays a separate
            // element so VoiceOver can still focus and activate it.
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Iris: \(title)")
            Button(actionLabel, action: action)
                .controlSize(.small)
        }
    }

    private var icon: String {
        switch error {
        case .daemonNotRunning: "exclamationmark.circle.fill"
        case .daemonFailing, .portConflict, .tokenMismatch: "exclamationmark.triangle.fill"
        case .microphoneOff: "mic.slash.fill"
        case .generic: "exclamationmark.circle.fill"
        }
    }

    private var title: String {
        switch error {
        case .daemonNotRunning: "Iris isn't running."
        case .daemonFailing: "Something's wrong with Iris."
        case .portConflict: "Another app is using Iris's port."
        case .tokenMismatch: "Another copy of Iris is already running."
        case .microphoneOff: "Microphone access is off."
        case let .generic(message): message
        }
    }

    private var actionLabel: String {
        switch error {
        case .daemonNotRunning: "Restart Iris"
        case .daemonFailing: "Open Diagnostics"
        case .portConflict, .tokenMismatch: "Try Again"
        case .microphoneOff: "Open System Settings"
        case .generic: "Try Again"
        }
    }

    private var action: () -> Void {
        switch error {
        case .daemonNotRunning: onRestart
        case .daemonFailing: onOpenDiagnostics
        // Re-probe the daemon: spawns ours once the port is free / the other
        // Iris is quit. (Rotating the token would worsen an adopted daemon.)
        case .portConflict, .tokenMismatch: onRestart
        case .microphoneOff: onOpenMicrophoneSettings
        case .generic: onRetry
        }
    }
}

/// Small chip shown over a live session while the SSE stream reconnects.
struct ReconnectingChip: View {
    var body: some View {
        HStack(spacing: 5) {
            ProgressView()
                .controlSize(.mini)
            Text("Reconnecting…")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.thinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.separator, lineWidth: 0.5))
        .accessibilityLabel("Reconnecting to Iris")
    }
}
