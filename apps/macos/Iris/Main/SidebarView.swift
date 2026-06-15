import SwiftUI

/// The four destinations of the console. Declared once so the sidebar, the
/// detail switch, and tests all agree on the navigation real estate.
enum MainSection: String, CaseIterable, Identifiable, Hashable {
    case home
    case activity
    case approvals
    case settings

    var id: String { rawValue }

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

/// A collapsible navigation sidebar (in the spirit of Flow / Linear): a narrow
/// icon-only rail that expands to icons + labels via the titlebar toggle.
/// Daemon health already lives in the menu bar, so it isn't duplicated here.
struct SidebarRail: View {
    @Binding var selection: MainSection
    @Binding var expanded: Bool
    /// Pending approvals — badges the Approvals item when nonzero.
    var approvalCount = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let collapsedWidth: CGFloat = 56
    private static let expandedWidth: CGFloat = 210

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            VStack(spacing: 2) {
                ForEach(MainSection.primary) { item($0) }
            }
            .padding(.top, DesignSystem.Spacing.xl)
            Spacer(minLength: DesignSystem.Spacing.sm)
            item(.settings)
        }
        .padding(.horizontal, DesignSystem.Spacing.sm)
        .padding(.top, 58)
        .padding(.bottom, DesignSystem.Spacing.md)
        .frame(width: expanded ? Self.expandedWidth : Self.collapsedWidth)
        .frame(maxHeight: .infinity)
        .background(DesignSystem.Colors.surfaceSecondary)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: expanded)
    }

    private var header: some View {
        HStack(spacing: DesignSystem.Spacing.sm) {
            SiriNewMark()
                .frame(width: 22, height: 22)
            if expanded {
                Text("Iris")
                    .font(.system(size: 25, weight: .semibold))
                    .foregroundStyle(DesignSystem.Colors.textPrimary)
            }
        }
        .frame(height: 38)
        .frame(maxWidth: expanded ? .infinity : 40, alignment: .center)
        .padding(.bottom, DesignSystem.Spacing.xl)
    }

    private func item(_ section: MainSection) -> some View {
        SidebarItem(
            section: section,
            expanded: expanded,
            isSelected: selection == section,
            badge: section == .approvals ? approvalCount : 0
        ) { selection = section }
    }
}

private struct SidebarItem: View {
    let section: MainSection
    let expanded: Bool
    let isSelected: Bool
    var badge: Int = 0
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: DesignSystem.Spacing.sm) {
                Image(systemName: section.systemImage)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 24)
                    .overlay(alignment: .topTrailing) {
                        if badge > 0, !expanded { CountBadge(count: badge).offset(x: 8, y: -6) }
                    }
                if expanded {
                    Text(section.title)
                        .font(DesignSystem.Typography.body)
                    Spacer(minLength: 0)
                    if badge > 0 { CountBadge(count: badge) }
                }
            }
            .foregroundStyle(isSelected
                ? DesignSystem.Colors.textPrimary
                : DesignSystem.Colors.textSecondary)
            .frame(maxWidth: expanded ? .infinity : 40, alignment: expanded ? .leading : .center)
            .frame(height: 34)
            .padding(.horizontal, expanded ? DesignSystem.Spacing.sm : 0)
            .background(
                RoundedRectangle(cornerRadius: DesignSystem.Radius.sm)
                    .fill(isSelected ? DesignSystem.Colors.surface : .clear)
            )
        }
        .buttonStyle(.plain)
        .help(section.title)
        .accessibilityIdentifier("sidebar-\(section.rawValue)")
        .accessibilityLabel(section.title)
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
