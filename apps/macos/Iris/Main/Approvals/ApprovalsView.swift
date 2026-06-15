import IrisKit
import SwiftUI

/// The Approvals section: the durable safety console. Pending actions wait here
/// for an explicit decision; the user's recent decisions collapse into history.
/// Deny is always at least as reachable as Approve, and Approve is never the
/// visually "primary" choice — the UI must never nudge toward granting.
struct ApprovalsView: View {
    @State private var model: ApprovalsViewModel
    @State private var showHistory = false

    init(model: ApprovalsViewModel) {
        _model = State(initialValue: model)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.xl) {
                content
            }
            .padding(DesignSystem.Spacing.xl)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(DesignSystem.Colors.canvas)
        .accessibilityIdentifier("section-approvals")
        .task { await model.start() }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            ApprovalsSkeleton()
        case let .failed(message):
            ApprovalsError(message: message) { Task { await model.refresh() } }
        case .loaded:
            if let actionError = model.actionError {
                ActionErrorBanner(message: actionError) { model.dismissError() }
            }
            if model.pending.isEmpty, model.history.isEmpty {
                EmptyStateView(
                    title: "You're all caught up",
                    message: "Nothing needs your approval right now. Actions Iris "
                        + "wants to take will appear here first.",
                    systemImage: "checkmark.shield"
                )
                .frame(minHeight: 300)
            } else {
                if !model.pending.isEmpty { pendingSection }
                if !model.history.isEmpty { historySection }
            }
        }
    }

    private var pendingSection: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
            SectionLabel("Pending — \(model.pending.count)")
            ForEach(model.pending) { approval in
                PendingApprovalCard(
                    approval: approval,
                    isConfirming: model.confirming?.approvalId == approval.approvalId,
                    isDeciding: model.decidingIds.contains(approval.approvalId),
                    onApprove: { Task { await model.approve(approval) } },
                    onDeny: { Task { await model.deny(approval) } },
                    onConfirm: { Task { await model.confirmApprove() } },
                    onCancelConfirm: { model.cancelConfirm() }
                )
            }
        }
    }

    private var historySection: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { showHistory.toggle() }
            } label: {
                HStack(spacing: DesignSystem.Spacing.xs) {
                    Image(systemName: showHistory ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                    SectionLabel("Recent decisions — \(model.history.count)")
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Recent decisions, \(model.history.count)")

            if showHistory {
                Card(padding: 0) {
                    VStack(spacing: 0) {
                        ForEach(Array(model.history.enumerated()), id: \.element.id) { index, resolved in
                            if index > 0 { Divider().overlay(DesignSystem.Colors.border) }
                            HistoryRow(resolved: resolved)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Pending card

private struct PendingApprovalCard: View {
    let approval: Approval
    let isConfirming: Bool
    let isDeciding: Bool
    let onApprove: () -> Void
    let onDeny: () -> Void
    let onConfirm: () -> Void
    let onCancelConfirm: () -> Void

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
                HStack(alignment: .firstTextBaseline) {
                    Text(approval.actionName)
                        .font(DesignSystem.Typography.heading)
                        .foregroundStyle(DesignSystem.Colors.textPrimary)
                    Spacer()
                    RiskPill(risk: approval.risk)
                }

                if !approval.preview.isEmpty, approval.preview != approval.actionName {
                    Text(approval.preview)
                        .font(DesignSystem.Typography.body)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                metaRow

                Divider().overlay(DesignSystem.Colors.border)

                if isConfirming {
                    confirmRow
                } else {
                    actionRow
                }
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var metaRow: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            if let run = approval.runId, !run.isEmpty {
                Label("Run \(run)", systemImage: "bolt")
                    .labelStyle(.titleAndIcon)
            }
            if let age = age {
                if approval.runId != nil { Text("·").foregroundStyle(DesignSystem.Colors.textTertiary) }
                Text(age)
            }
        }
        .font(DesignSystem.Typography.data)
        .foregroundStyle(DesignSystem.Colors.textTertiary)
    }

    /// Equal-weight Approve/Deny. Neither uses the prominent (filled) style, so
    /// the layout never pushes the user toward granting.
    private var actionRow: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            Button("Deny", action: onDeny)
                .tint(DesignSystem.Colors.rust)
            Button("Approve", action: onApprove)
            if isDeciding {
                ProgressView().controlSize(.small)
                    .padding(.leading, DesignSystem.Spacing.xs)
            }
            Spacer()
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
        .disabled(isDeciding)
    }

    /// The destructive confirm step. Deny stays available; approving is the
    /// step that gets the extra friction.
    private var confirmRow: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.sm) {
            Label("This is a high-risk action.", systemImage: "exclamationmark.triangle")
                .font(DesignSystem.Typography.callout)
                .foregroundStyle(DesignSystem.Colors.rust)
            HStack(spacing: DesignSystem.Spacing.sm) {
                Button("Cancel", action: onCancelConfirm)
                    .buttonStyle(.bordered)
                Button("Approve anyway", action: onConfirm)
                    .buttonStyle(.borderedProminent)
                    .tint(DesignSystem.Colors.rust)
                if isDeciding {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
            .controlSize(.large)
            .disabled(isDeciding)
        }
    }

    private var age: String? {
        ApprovalsViewModel.parseTimestamp(approval.createdAt)
            .map { ApprovalsViewModel.ageText(since: $0) }
    }
}

// MARK: - Risk pill

private struct RiskPill: View {
    let risk: String

    var body: some View {
        Text(label)
            .font(DesignSystem.Typography.sectionLabel)
            .tracking(0.5)
            .foregroundStyle(tint)
            .padding(.vertical, 2)
            .padding(.horizontal, DesignSystem.Spacing.sm)
            .background(tint.opacity(0.14))
            .clipShape(Capsule())
            .accessibilityLabel("Risk: \(label)")
    }

    private var label: String {
        switch risk.lowercased() {
        case "low_risk": "LOW RISK"
        case "sensitive": "SENSITIVE"
        case "blocked": "HIGH RISK"
        default: risk.uppercased()
        }
    }

    private var tint: Color {
        switch risk.lowercased() {
        case "blocked": DesignSystem.Colors.rust
        case "sensitive": DesignSystem.Colors.amber
        default: DesignSystem.Colors.textTertiary
        }
    }
}

// MARK: - History row

private struct HistoryRow: View {
    let resolved: ApprovalsViewModel.Resolved

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.md) {
            VStack(alignment: .leading, spacing: 2) {
                Text(resolved.approval.actionName)
                    .font(DesignSystem.Typography.body)
                    .foregroundStyle(DesignSystem.Colors.textPrimary)
                    .lineLimit(1)
                if let run = resolved.approval.runId, !run.isEmpty {
                    Text("Run \(run)")
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
            }
            Spacer(minLength: DesignSystem.Spacing.sm)
            decisionBadge
        }
        .padding(.horizontal, DesignSystem.Spacing.md)
        .padding(.vertical, DesignSystem.Spacing.md)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(resolved.approval.actionName), \(decisionText)")
    }

    private var decisionBadge: some View {
        HStack(spacing: DesignSystem.Spacing.xs) {
            StatusDot(kind: resolved.decision == .approve ? .good : .bad, diameter: 7)
            Text(decisionText)
                .font(DesignSystem.Typography.caption)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
        }
    }

    private var decisionText: String {
        resolved.decision == .approve ? "Approved" : "Denied"
    }
}

// MARK: - Error banner / states

private struct ActionErrorBanner: View {
    let message: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(DesignSystem.Colors.amber)
            Text(message)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
            Spacer()
            Button {
                onDismiss()
            } label: {
                Image(systemName: "xmark").font(.system(size: 11, weight: .medium))
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("Dismiss")
        }
        .padding(DesignSystem.Spacing.md)
        .background(DesignSystem.Colors.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: DesignSystem.Radius.sm))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}

private struct ApprovalsError: View {
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
        .frame(maxWidth: .infinity, minHeight: 300)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}

private struct ApprovalsSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
            ForEach(0 ..< 3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: DesignSystem.Radius.md)
                    .fill(DesignSystem.Colors.surfaceSecondary)
                    .frame(height: 120)
            }
        }
        .accessibilityLabel("Loading")
    }
}
