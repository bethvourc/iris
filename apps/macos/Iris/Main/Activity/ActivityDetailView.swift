import AppKit
import IrisKit
import SwiftUI

/// The Activity detail pane: a readable render of the selected item, shown as a
/// collapsible inspector. A thin toolbar carries the copy and close affordances;
/// transcripts render as a conversation (never raw JSON), and runs surface their
/// summary and linked run id.
struct ActivityDetailView: View {
    @Bindable var model: ActivityViewModel

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider().overlay(DesignSystem.Colors.border)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(DesignSystem.Colors.canvas)
    }

    private var toolbar: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            Spacer()
            if case let .loaded(detail) = model.detail {
                CopyButton(detail: detail)
            }
            Button {
                model.selection = nil
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.borderless)
            .keyboardShortcut(.cancelAction)
            .help("Close")
            .accessibilityLabel("Close details")
        }
        .padding(.horizontal, DesignSystem.Spacing.lg)
        .frame(height: 38)
    }

    @ViewBuilder
    private var content: some View {
        switch model.detail {
        case .none:
            DetailPlaceholder()
        case .loading:
            ProgressView()
                .controlSize(.small)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case let .loaded(detail):
            DetailContent(detail: detail)
        case let .failed(message):
            DetailError(message: message) { model.retryDetail() }
        }
    }
}

// MARK: - Copy button

/// Copies the selected item's transcript to the clipboard as plain text, with a
/// brief "Copied" confirmation.
private struct CopyButton: View {
    let detail: ActivityDetail
    @State private var didCopy = false

    var body: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(plainText, forType: .string)
            didCopy = true
            Task {
                try? await Task.sleep(for: .seconds(2))
                didCopy = false
            }
        } label: {
            Label(didCopy ? "Copied" : "Copy", systemImage: didCopy ? "checkmark" : "doc.on.doc")
        }
        .controlSize(.small)
        .disabled(plainText.isEmpty)
        .accessibilityLabel("Copy transcript to clipboard")
    }

    /// Flatten the detail into a plain-text record suitable for pasting.
    private var plainText: String {
        var lines = [detail.title]
        if let summary = detail.summary, !summary.isEmpty { lines.append(summary) }
        for entry in detail.transcript ?? [] {
            lines.append("\(TranscriptTurn.roleLabel(entry.role)): \(entry.text)")
        }
        return lines.joined(separator: "\n\n")
    }
}

// MARK: - Loaded content

private struct DetailContent: View {
    let detail: ActivityDetail

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.lg) {
                header

                if let summary = detail.summary, !summary.isEmpty {
                    Text(summary)
                        .font(DesignSystem.Typography.body)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                }

                if let transcript = detail.transcript, !transcript.isEmpty {
                    SectionLabel("Transcript")
                    VStack(alignment: .leading, spacing: DesignSystem.Spacing.lg) {
                        ForEach(Array(transcript.enumerated()), id: \.offset) { _, entry in
                            TranscriptTurn(entry: entry)
                        }
                    }
                } else if detail.summary == nil {
                    Text("No transcript for this item.")
                        .font(DesignSystem.Typography.body)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }

                if let run = detail.run {
                    SectionLabel("Run")
                    Text("Linked run \(run.runId)")
                        .font(DesignSystem.Typography.data)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                        .textSelection(.enabled)
                }
            }
            .padding(.horizontal, DesignSystem.Spacing.xl)
            .padding(.bottom, DesignSystem.Spacing.xl)
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.sm) {
            Text(detail.title)
                .font(DesignSystem.Typography.heading)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
            HStack(spacing: DesignSystem.Spacing.md) {
                StatusBadge(status: detail.status)
                if let time = detail.time {
                    Text(time.formatted(date: .abbreviated, time: .shortened))
                        .font(DesignSystem.Typography.data)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
            }
        }
        .padding(.top, DesignSystem.Spacing.sm)
    }
}

// MARK: - Transcript

private struct TranscriptTurn: View {
    let entry: TranscriptEntry

    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
            HStack(spacing: DesignSystem.Spacing.sm) {
                Text(Self.roleLabel(entry.role))
                    .font(DesignSystem.Typography.callout)
                    .foregroundStyle(isAssistant
                        ? DesignSystem.Colors.accent
                        : DesignSystem.Colors.textPrimary)
                if let time = entry.time {
                    Text(time.formatted(date: .omitted, time: .shortened))
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
            }
            Text(entry.text)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(Self.roleLabel(entry.role)): \(entry.text)")
    }

    private var isAssistant: Bool {
        entry.role.lowercased() == "assistant"
    }

    /// Human-facing speaker label for a transcript role.
    static func roleLabel(_ role: String) -> String {
        switch role.lowercased() {
        case "user": "You"
        case "assistant": "Iris"
        case "tool", "function": "Tool"
        case "system": "System"
        default: role.capitalized
        }
    }
}

// MARK: - Status badge

private struct StatusBadge: View {
    let status: ActivityStatus

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.xs) {
            StatusDot(kind: status.dotKind, diameter: 7)
            Text(label)
                .font(DesignSystem.Typography.caption)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
        }
        .padding(.vertical, 3)
        .padding(.horizontal, DesignSystem.Spacing.sm)
        .background(DesignSystem.Colors.surfaceSecondary)
        .clipShape(Capsule())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Status: \(label)")
    }

    private var label: String {
        status == .unknown ? "Unknown" : status.rawValue.capitalized
    }
}

// MARK: - Placeholder / error

private struct DetailPlaceholder: View {
    var body: some View {
        VStack(spacing: DesignSystem.Spacing.md) {
            Image(systemName: "text.bubble")
                .font(.system(size: 24, weight: .light))
                .foregroundStyle(DesignSystem.Colors.textTertiary)
            Text("Select an item to see the details.")
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityIdentifier("activity-detail-empty")
    }
}

private struct DetailError: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: DesignSystem.Spacing.md) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 24, weight: .light))
                .foregroundStyle(DesignSystem.Colors.amber)
            Text(message)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
                .multilineTextAlignment(.center)
            Button("Retry", action: onRetry)
        }
        .frame(maxWidth: 280)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}
