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

    /// One-line description shown in each section's placeholder until the real
    /// view (Steps 5.2–5.5) replaces it.
    var summary: String {
        switch self {
        case .home: "Your greeting, recent activity, and stats."
        case .activity: "Everything Iris has done, grouped by day."
        case .approvals: "Actions waiting on your decision."
        case .settings: "Voice, account, and advanced preferences."
        }
    }
}

/// The persistent left rail. Selection drives the detail column. Daemon health
/// already lives in the menu bar, so it deliberately isn't duplicated here.
struct SidebarView: View {
    @Binding var selection: MainSection

    var body: some View {
        List(MainSection.allCases, selection: $selection) { section in
            Label(section.title, systemImage: section.systemImage)
                .tag(section)
                .accessibilityIdentifier("sidebar-\(section.rawValue)")
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
    }
}
