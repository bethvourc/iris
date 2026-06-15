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

    /// `--ui-test-section <name>` selects the section the main window opens to,
    /// so screenshots can land directly on Activity/Approvals/Settings.
    static var launchSection: MainSection? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--ui-test-section"),
              arguments.indices.contains(index + 1) else { return nil }
        return MainSection(rawValue: arguments[index + 1])
    }
}

/// The persistent left rail. Selection drives the detail column. Daemon health
/// already lives in the menu bar, so it deliberately isn't duplicated here.
struct SidebarView: View {
    @Binding var selection: MainSection
    /// Pending approvals — badges the Approvals row when nonzero.
    var approvalCount = 0

    var body: some View {
        List(MainSection.allCases, selection: $selection) { section in
            Label(section.title, systemImage: section.systemImage)
                .tag(section)
                .badge(badge(for: section))
                .accessibilityIdentifier("sidebar-\(section.rawValue)")
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
    }

    /// Only Approvals carries a badge, and only when something is pending.
    private func badge(for section: MainSection) -> Int {
        section == .approvals ? approvalCount : 0
    }
}
