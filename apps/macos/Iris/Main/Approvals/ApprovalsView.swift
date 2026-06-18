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
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(DesignSystem.Colors.canvas)
            .accessibilityIdentifier("section-approvals")
            .task { await model.start() }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            // Top-aligned so it previews where the cards will land.
            ApprovalsSkeleton()
                .padding(DesignSystem.Spacing.xl)
                .frame(maxWidth: 720, alignment: .leading)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        case let .failed(message):
            ApprovalsError(message: message) { Task { await model.refresh() } }
        case .loaded:
            if model.pending.isEmpty, model.history.isEmpty {
                EmptyStateView(
                    title: "You're all caught up",
                    message: "Nothing needs your approval right now. Actions Iris "
                        + "wants to take will appear here first."
                )
            } else {
                loadedList
            }
        }
    }

    private var loadedList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.xl) {
                if let actionError = model.actionError {
                    ActionErrorBanner(message: actionError) { model.dismissError() }
                }
                if !model.pending.isEmpty { pendingSection }
                if !model.history.isEmpty { historySection }
            }
            .padding(DesignSystem.Spacing.xl)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
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
                    RiskLabel(risk: approval.risk)
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
        HStack(spacing: DesignSystem.Spacing.xs) {
            if let run = approval.runId, !run.isEmpty {
                Text("Run \(run)")
            }
            if let age {
                if approval.runId != nil { Text("·") }
                Text(age)
            }
        }
        .font(DesignSystem.Typography.caption)
        .foregroundStyle(DesignSystem.Colors.textTertiary)
    }

    /// Equal-weight Approve/Deny. Neither is the prominent (filled) style and
    /// neither is tinted, so the layout never pushes the user toward granting.
    private var actionRow: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            Button("Deny", action: onDeny)
                .accessibilityLabel("Deny \(approval.actionName)")
            Button("Approve", action: onApprove)
                .accessibilityLabel("Approve \(approval.actionName)")
            if isDeciding {
                ProgressView().controlSize(.small)
                    .padding(.leading, DesignSystem.Spacing.xs)
            }
            Spacer()
        }
        .buttonStyle(.bordered)
        .disabled(isDeciding)
    }

    /// The destructive confirm step. Deny stays available; approving is the
    /// step that gets the extra friction. The native destructive role supplies
    /// the only color here.
    private var confirmRow: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.sm) {
            Text("This action is high-risk and can't be undone. Approve anyway?")
                .font(DesignSystem.Typography.callout)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: DesignSystem.Spacing.sm) {
                Button("Cancel", action: onCancelConfirm)
                Button("Approve anyway", role: .destructive, action: onConfirm)
                if isDeciding {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
            .buttonStyle(.bordered)
            .disabled(isDeciding)
        }
    }

    private var age: String? {
        ApprovalsViewModel.parseTimestamp(approval.createdAt)
            .map { ApprovalsViewModel.ageText(since: $0) }
    }
}

// MARK: - Risk label

/// Risk shown as a plain uppercase label — no colored capsule. Color is spent
/// only on genuinely high-risk actions; everything else stays muted.
private struct RiskLabel: View {
    let risk: String

    var body: some View {
        Text(label)
            .font(DesignSystem.Typography.sectionLabel)
            .tracking(0.5)
            .foregroundStyle(isHighRisk ? DesignSystem.Colors.rust : DesignSystem.Colors.textTertiary)
            .accessibilityLabel("Risk: \(label)")
    }

    private var isHighRisk: Bool {
        risk.lowercased() == "blocked"
    }

    private var label: String {
        switch risk.lowercased() {
        case "low_risk": "LOW RISK"
        case "sensitive": "SENSITIVE"
        case "blocked": "HIGH RISK"
        default: risk.uppercased()
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
            Text(decisionText.uppercased())
                .font(DesignSystem.Typography.sectionLabel)
                .tracking(0.5)
                .foregroundStyle(DesignSystem.Colors.textTertiary)
        }
        .padding(.horizontal, DesignSystem.Spacing.md)
        .padding(.vertical, DesignSystem.Spacing.md)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(resolved.approval.actionName), \(decisionText)")
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
            Text(message)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
            Spacer()
            Button("Dismiss", action: onDismiss)
                .buttonStyle(.borderless)
                .font(DesignSystem.Typography.callout)
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
                .accessibilityHidden(true)
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
