import IrisKit
import SwiftUI

/// The Home screen: a greeting, the stats strip, and a short recent-activity
/// preview — a pure render of `HomeViewModel.state`, covering loading, empty
/// (first run), populated, and error.
struct HomeView: View {
    @State private var model: HomeViewModel
    /// Jump to the Activity section (the "See all" affordance).
    private let onSeeAll: () -> Void

    init(model: HomeViewModel, onSeeAll: @escaping () -> Void) {
        _model = State(initialValue: model)
        self.onSeeAll = onSeeAll
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.xl) {
                Text(model.greeting)
                    .font(DesignSystem.Typography.title)
                    .foregroundStyle(DesignSystem.Colors.textPrimary)

                content
            }
            .padding(DesignSystem.Spacing.xl)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(DesignSystem.Colors.canvas)
        .accessibilityIdentifier("section-home")
        .task { await model.load() }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            HomeSkeleton()
        case .empty:
            EmptyStateView(
                title: "Nothing here yet",
                message: "When Iris helps you, your sessions and runs will show up here.",
                hint: "Press ⌥Space to start a conversation"
            )
            .frame(minHeight: 280)
        case let .loaded(stats, recent):
            StatsRow(stats: stats)
            RecentActivitySection(items: recent, onSeeAll: onSeeAll)
        case let .failed(message):
            HomeErrorView(message: message) {
                Task { await model.load() }
            }
        }
    }
}

// MARK: - Recent activity

private struct RecentActivitySection: View {
    let items: [ActivityItem]
    let onSeeAll: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
            HStack {
                SectionLabel("Recent activity")
                Spacer()
                Button("See all", action: onSeeAll)
                    .buttonStyle(.link)
                    .font(DesignSystem.Typography.callout)
            }

            Card(padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        if index > 0 {
                            Divider().overlay(DesignSystem.Colors.border)
                        }
                        ActivityPreviewRow(item: item)
                    }
                }
            }
        }
    }
}

private struct ActivityPreviewRow: View {
    let item: ActivityItem

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.md) {
            Image(systemName: item.kind.symbol)
                .font(.system(size: 14))
                .foregroundStyle(DesignSystem.Colors.textSecondary)
                .frame(width: 20)

            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(DesignSystem.Typography.body)
                    .foregroundStyle(DesignSystem.Colors.textPrimary)
                    .lineLimit(1)
                if let preview = item.preview, !preview.isEmpty {
                    Text(preview)
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: DesignSystem.Spacing.sm)

            Text(item.time.formatted(date: .omitted, time: .shortened))
                .font(DesignSystem.Typography.data)
                .foregroundStyle(DesignSystem.Colors.textTertiary)
            StatusDot(kind: item.status.dotKind, diameter: 7)
        }
        .padding(.horizontal, DesignSystem.Spacing.md)
        .padding(.vertical, DesignSystem.Spacing.md)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.title), \(item.status.rawValue)")
    }
}

// MARK: - Error

private struct HomeErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.md) {
                HStack(spacing: DesignSystem.Spacing.sm) {
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundStyle(DesignSystem.Colors.amber)
                    Text(message)
                        .font(DesignSystem.Typography.body)
                        .foregroundStyle(DesignSystem.Colors.textPrimary)
                }
                Button("Retry", action: onRetry)
                    .controlSize(.regular)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}

// MARK: - Loading skeleton

private struct HomeSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.xl) {
            HStack(spacing: DesignSystem.Spacing.md) {
                ForEach(0 ..< 4, id: \.self) { _ in
                    SkeletonBlock(height: 72)
                }
            }
            VStack(spacing: DesignSystem.Spacing.sm) {
                ForEach(0 ..< 4, id: \.self) { _ in
                    SkeletonBlock(height: 44)
                }
            }
        }
        .accessibilityLabel("Loading")
    }
}

private struct SkeletonBlock: View {
    let height: CGFloat

    var body: some View {
        RoundedRectangle(cornerRadius: DesignSystem.Radius.md)
            .fill(DesignSystem.Colors.surfaceSecondary)
            .frame(maxWidth: .infinity)
            .frame(height: height)
    }
}

// MARK: - Activity presentation helpers

extension ActivityKind {
    /// SF Symbol per activity kind, used in list rows.
    var symbol: String {
        switch self {
        case .voiceSession: "waveform"
        case .run: "bolt"
        case .meeting: "person.2"
        case .message: "text.bubble"
        case .unknown: "circle"
        }
    }
}

extension ActivityStatus {
    /// Maps a run/session status to a status-dot color.
    var dotKind: StatusDot.Kind {
        switch self {
        case .done: .good
        case .failed: .bad
        case .blocked: .warning
        case .cancelled, .running, .unknown: .neutral
        }
    }
}
