import IrisKit
import SwiftUI

/// Per-state overlay content. Every state has exactly one visual treatment;
/// silence is never ambiguous — connecting/thinking show explicit progress.
struct OverlayContent: View {
    let model: VoiceSessionViewModel

    var body: some View {
        switch model.displayState {
        case .connecting:
            StatusRow(systemActivity: true, label: "Connecting…")
        case .listening, .userSpeaking:
            ListeningView(
                userText: model.userText,
                assistantText: model.assistantText
            )
        case .thinking:
            StatusRow(systemActivity: true, label: model.statusLine ?? "Thinking…")
        case .speaking:
            SpeakingView(
                userText: model.userText,
                assistantText: model.assistantText,
                onInterrupt: { model.interrupt() }
            )
        case .interrupted:
            StatusRow(systemActivity: false, label: "…")
        case .meeting:
            StatusRow(systemActivity: false, label: "In a meeting — listening quietly")
        case let .ended(summary):
            EndedView(summary: summary)
        case let .error(message, retryable):
            ErrorView(message: message, retryable: retryable, onRetry: { model.retry() })
        }
    }
}

/// Header row: the brand mark plus a status label, with optional progress.
private struct StatusRow: View {
    let systemActivity: Bool
    let label: String

    var body: some View {
        HStack(spacing: 12) {
            SiriNewMark()
                .frame(width: 22, height: 22)
                .foregroundStyle(.tint)
            Text(label)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.primary)
                .lineLimit(1)
            Spacer(minLength: 0)
            if systemActivity {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Iris: \(label)")
    }
}

private struct ListeningView: View {
    let userText: String
    let assistantText: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                SiriNewMark()
                    .frame(width: 22, height: 22)
                    .foregroundStyle(.tint)
                Text("Listening…")
                    .font(.system(size: 15, weight: .medium))
                Spacer(minLength: 0)
                ListeningPulse()
            }
            if !userText.isEmpty || !assistantText.isEmpty {
                TranscriptView(userText: userText, assistantText: assistantText)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Iris is listening")
    }
}

private struct SpeakingView: View {
    let userText: String
    let assistantText: String
    let onInterrupt: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                SiriNewMark()
                    .frame(width: 22, height: 22)
                    .foregroundStyle(.tint)
                Text("Iris")
                    .font(.system(size: 15, weight: .medium))
                Spacer(minLength: 0)
                Button(action: onInterrupt) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 11))
                }
                .buttonStyle(.borderless)
                .help("Interrupt")
                .accessibilityLabel("Interrupt")
            }
            TranscriptView(userText: userText, assistantText: assistantText)
        }
    }
}

private struct EndedView: View {
    let summary: String?

    var body: some View {
        HStack(spacing: 12) {
            SiriNewMark()
                .frame(width: 22, height: 22)
                .foregroundStyle(.secondary)
            Text(summary ?? "Done")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Session ended")
    }
}

private struct ErrorView: View {
    let message: String
    let retryable: Bool
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundStyle(.orange)
                Text(message)
                    .font(.system(size: 14))
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if retryable {
                Button("Try Again", action: onRetry)
                    .controlSize(.small)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Iris error: \(message)")
    }
}

/// Three dots that breathe — a listening affordance (we don't have real mic
/// levels over SSE, so this signals "live" without faking a meter).
private struct ListeningPulse: View {
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0 ..< 3, id: \.self) { index in
                Circle()
                    .fill(.tint)
                    .frame(width: 5, height: 5)
                    .opacity(0.4 + 0.6 * pulse(index))
            }
        }
        .onAppear { phase = 1 }
        .animation(
            .easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: phase
        )
        .accessibilityHidden(true)
    }

    private func pulse(_ index: Int) -> Double {
        let shift = Double(index) * 0.2
        return abs((phase + shift).truncatingRemainder(dividingBy: 1))
    }
}
