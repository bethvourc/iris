import SwiftUI

/// The four destinations of the console. Declared once so the sidebar, the
/// detail switch, and tests all agree on the navigation real estate.
enum MainSection: String, CaseIterable, Identifiable, Hashable {
    case home
    case activity
    case approvals
    case settings

    var id: String {
        rawValue
    }

    var title: String {
        switch self {
        case .home: "Home"
        case .activity: "Activity"
        case .approvals: "Approvals"
        case .settings: "Settings"
        }
    }

    var systemImage: String {
        switch self {
        case .home: "house"
        case .activity: "list.bullet.rectangle"
        case .approvals: "checkmark.shield"
        case .settings: "gearshape"
        }
    }

    /// The day-to-day destinations, shown at the top of the rail. Settings is
    /// pinned to the bottom (the conventional utility slot).
    static let primary: [MainSection] = [.home, .activity, .approvals]

    /// `--ui-test-section <name>` selects the section the main window opens to,
    /// so screenshots can land directly on Activity/Approvals/Settings.
    static var launchSection: MainSection? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--ui-test-section"),
              arguments.indices.contains(index + 1) else { return nil }
        return MainSection(rawValue: arguments[index + 1])
    }
}

/// The navigation sidebar (in the spirit of Flow / Linear): a brand header and
/// labeled destinations, the selected one highlighted. Daemon health already
/// lives in the menu bar, so it isn't duplicated here.
struct SidebarRail: View {
    @Binding var selection: MainSection
    /// Pending approvals — badges the Approvals item when nonzero.
    var approvalCount = 0

    private static let width: CGFloat = 210

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            VStack(spacing: 2) {
                ForEach(MainSection.primary) { item($0) }
            }
            .padding(.top, DesignSystem.Spacing.lg)
            Spacer(minLength: DesignSystem.Spacing.sm)
            item(.settings)
        }
        .padding(.horizontal, DesignSystem.Spacing.sm)
        // Clear the traffic lights that float over the top with the hidden
        // titlebar.
        .padding(.top, 44)
        .padding(.bottom, DesignSystem.Spacing.md)
        .frame(width: Self.width)
        .frame(maxHeight: .infinity)
        .background(DesignSystem.Colors.surfaceSecondary)
    }

    private var header: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            SiriNewMark()
                .frame(width: 20, height: 20)
                .accessibilityHidden(true)
            Text("Iris")
                .font(.system(size: 19, weight: .semibold))
                .foregroundStyle(DesignSystem.Colors.textPrimary)
            Spacer(minLength: 0)
        }
        .frame(height: 28)
        .padding(.leading, DesignSystem.Spacing.xs)
    }

    private func item(_ section: MainSection) -> some View {
        SidebarItem(
            section: section,
            isSelected: selection == section,
            badge: section == .approvals ? approvalCount : 0
        ) { selection = section }
    }
}

private struct SidebarItem: View {
    let section: MainSection
    let isSelected: Bool
    var badge: Int = 0
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: DesignSystem.Spacing.sm) {
                Image(systemName: section.systemImage)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 24)
                Text(section.title)
                    .font(DesignSystem.Typography.body)
                Spacer(minLength: 0)
                // Decorative: the count is folded into the button's
                // accessibilityValue so VoiceOver reads "Approvals, 3 pending"
                // as one element instead of a stray, unparented badge.
                if badge > 0 { CountBadge(count: badge).accessibilityHidden(true) }
            }
            .foregroundStyle(isSelected
                ? DesignSystem.Colors.textPrimary
                : DesignSystem.Colors.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: 34)
            .padding(.horizontal, DesignSystem.Spacing.sm)
            .background(
                RoundedRectangle(cornerRadius: DesignSystem.Radius.sm)
                    .fill(isSelected ? DesignSystem.Colors.surface : .clear)
            )
        }
        .buttonStyle(.plain)
        .help(section.title)
        .accessibilityIdentifier("sidebar-\(section.rawValue)")
        .accessibilityLabel(section.title)
        .accessibilityValue(badge > 0 ? "\(badge) pending" : "")
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }
}

private struct CountBadge: View {
    let count: Int

    var body: some View {
        Text(count > 9 ? "9+" : "\(count)")
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 3)
            .frame(minWidth: 14, minHeight: 14)
            .background(Capsule().fill(DesignSystem.Colors.accent))
    }
}
