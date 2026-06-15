import IrisKit
import SwiftUI

/// The Home stats strip: four at-a-glance numbers from `ActivityStats`,
/// presented as a clean, borderless row (value over a quiet label) divided by
/// hairlines — no boxed "dashboard" cards. Values use tabular digits so they
/// don't jitter as they update.
struct StatsRow: View {
    let stats: ActivityStats

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            stat("\(stats.sessionsThisWeek)", "Sessions this week")
            divider
            stat("\(stats.runsCompleted)", "Runs completed")
            divider
            stat(
                "\(stats.runsFailed)", "Runs failed",
                color: stats.runsFailed > 0 ? DesignSystem.Colors.rust : nil
            )
            divider
            stat(Self.lastActive(stats.lastActiveAt), "Last active")
        }
        .accessibilityElement(children: .contain)
    }

    private func stat(_ value: String, _ label: String, color: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(.system(size: 22, weight: .semibold).monospacedDigit())
                .foregroundStyle(color ?? DesignSystem.Colors.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(label)
                .font(DesignSystem.Typography.caption)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(value)")
    }

    private var divider: some View {
        Rectangle()
            .fill(DesignSystem.Colors.border)
            .frame(width: DesignSystem.hairline, height: 30)
            .padding(.horizontal, DesignSystem.Spacing.lg)
    }

    /// Relative phrasing ("2 hours ago"), or an em dash when never active.
    static func lastActive(_ date: Date?) -> String {
        guard let date else { return "—" }
        return date.formatted(.relative(presentation: .named))
    }
}
