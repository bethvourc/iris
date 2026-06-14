import IrisKit
import SwiftUI

/// The Home stats strip: four at-a-glance numbers from `ActivityStats`. Values
/// use the tabular data font so digits stay aligned and don't jitter as they
/// update.
struct StatsRow: View {
    let stats: ActivityStats

    var body: some View {
        HStack(spacing: DesignSystem.Spacing.md) {
            StatCard(title: "Sessions this week", value: "\(stats.sessionsThisWeek)")
            StatCard(title: "Runs completed", value: "\(stats.runsCompleted)")
            StatCard(
                title: "Runs failed",
                value: "\(stats.runsFailed)",
                valueColor: stats.runsFailed > 0 ? DesignSystem.Colors.rust : nil
            )
            StatCard(title: "Last active", value: Self.lastActive(stats.lastActiveAt))
        }
        .accessibilityElement(children: .contain)
    }

    /// Relative phrasing ("2 hours ago"), or an em dash when never active.
    static func lastActive(_ date: Date?) -> String {
        guard let date else { return "—" }
        return date.formatted(.relative(presentation: .named))
    }
}

/// One stat: a large value over a quiet caption, in a hairline-bordered card.
struct StatCard: View {
    let title: String
    let value: String
    var valueColor: Color?

    var body: some View {
        Card(padding: DesignSystem.Spacing.md) {
            VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                Text(value)
                    .font(DesignSystem.Typography.dataLarge)
                    .foregroundStyle(valueColor ?? DesignSystem.Colors.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                Text(title)
                    .font(DesignSystem.Typography.caption)
                    .foregroundStyle(DesignSystem.Colors.textSecondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title): \(value)")
    }
}
