import IrisKit
import SwiftUI

/// The Activity section: a day-grouped master list on the left and a detail
/// pane on the right, both driven by one `ActivityViewModel`. This is the "see
/// everything Iris did" surface, so it leans on the same restrained tokens as
/// the rest of the console — no chrome the data doesn't earn.
struct ActivityView: View {
    @State private var model: ActivityViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(model: ActivityViewModel) {
        _model = State(initialValue: model)
    }

    /// The detail pane is a collapsible inspector: the list owns the full width
    /// until an item is selected, then the detail slides in alongside it.
    private var isDetailShown: Bool { model.selection != nil }

    var body: some View {
        HStack(spacing: 0) {
            ActivityListView(model: model)
                .frame(maxWidth: isDetailShown ? 360 : .infinity)
            if isDetailShown {
                Divider().overlay(DesignSystem.Colors.border)
                ActivityDetailView(model: model)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: isDetailShown)
        .background(DesignSystem.Colors.canvas)
        .accessibilityIdentifier("section-activity")
        .task { await model.load() }
    }
}

// MARK: - List

struct ActivityListView: View {
    @Bindable var model: ActivityViewModel

    var body: some View {
        Group {
            switch model.phase {
            case .loading:
                ActivityListSkeleton()
            case .empty:
                EmptyStateView(
                    title: "No activity yet",
                    message: "Everything Iris does for you will be logged here, "
                        + "newest first.",
                    hint: "Press ⌥Space to start a conversation"
                )
            case .content:
                list
            case let .failed(message):
                ActivityListError(message: message) {
                    Task { await model.load() }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(DesignSystem.Colors.surfaceSecondary)
    }

    private var list: some View {
        List(selection: $model.selection) {
            ForEach(model.days, id: \.date) { day in
                Section {
                    ForEach(day.items) { item in
                        ActivityRow(item: item)
                            .tag(item.id)
                            .listRowSeparator(.hidden)
                    }
                } header: {
                    SectionLabel(dayTitle(day))
                        .padding(.top, DesignSystem.Spacing.xs)
                }
            }

            ActivityListFooter(model: model)
                .listRowSeparator(.hidden)
        }
        .listStyle(.inset)
        .scrollContentBackground(.hidden)
        .environment(\.defaultMinListRowHeight, 1)
    }

    /// Prefer the server's friendly label ("Today"), falling back to the date.
    private func dayTitle(_ day: ActivityDay) -> String {
        if let label = day.label, !label.isEmpty { return label }
        return day.date
    }
}

// MARK: - Row

private struct ActivityRow: View {
    let item: ActivityItem

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            Image(systemName: item.kind.symbol)
                .font(.system(size: 13))
                .foregroundStyle(DesignSystem.Colors.textSecondary)
                .frame(width: 18)

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
        .padding(.vertical, DesignSystem.Spacing.xs)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.title), \(item.status.rawValue)")
    }
}

// MARK: - Footer (pagination)

/// The list's trailing row: it both reflects pagination state and drives
/// infinite scroll — appearing on screen triggers the next page fetch.
private struct ActivityListFooter: View {
    let model: ActivityViewModel

    var body: some View {
        Group {
            if let error = model.loadMoreError {
                VStack(spacing: DesignSystem.Spacing.sm) {
                    Text(error)
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                    Button("Try again") { Task { await model.loadMore() } }
                        .controlSize(.small)
                }
            } else if model.isLoadingMore {
                ProgressView().controlSize(.small)
            } else if model.reachedEnd {
                Text("Beginning of history")
                    .font(DesignSystem.Typography.caption)
                    .foregroundStyle(DesignSystem.Colors.textTertiary)
            } else {
                // Invisible probe: when this scrolls into view, load the next
                // page. Color.clear has zero intrinsic height, so it sits flush.
                Color.clear.frame(height: 1)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, DesignSystem.Spacing.md)
        .onAppear { Task { await model.loadMore() } }
    }
}

// MARK: - Loading / error

private struct ActivityListSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.sm) {
            ForEach(0 ..< 8, id: \.self) { _ in
                RoundedRectangle(cornerRadius: DesignSystem.Radius.sm)
                    .fill(DesignSystem.Colors.surface)
                    .frame(height: 40)
            }
            Spacer()
        }
        .padding(DesignSystem.Spacing.md)
        .accessibilityLabel("Loading")
    }
}

private struct ActivityListError: View {
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
        .frame(maxWidth: 240)
        .padding(DesignSystem.Spacing.lg)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}
