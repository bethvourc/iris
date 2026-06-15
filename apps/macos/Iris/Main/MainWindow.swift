import AppKit
import IrisKit
import SwiftUI

/// The console's main window: a `NavigationSplitView` whose sidebar selects one
/// of four sections. This shell defines all navigation up front; Steps 5.2–5.5
/// replace each placeholder detail with the real Home, Activity, Approvals, and
/// Settings views. The window opens from the menu bar / dock-less activation
/// and closes back to the menu bar — it never quits the app (handled by the
/// menu-bar-only `IrisApp` scene).
struct MainWindowView: View {
    /// Held for the section view models wired up in Steps 5.3–5.5.
    let model: AppModel
    @State private var selection: MainSection
    /// Sidebar starts expanded (labels visible); the toggle collapses it to a
    /// narrow icon rail.
    @State private var sidebarExpanded = true
    /// Created once so navigating away and back doesn't drop loaded Home data.
    @State private var home: HomeViewModel
    /// Likewise persisted so list scroll position and selection survive section
    /// switches.
    @State private var activity: ActivityViewModel
    @State private var approvals: ApprovalsViewModel

    @MainActor
    init(model: AppModel) {
        self.model = model
        _home = State(initialValue: model.makeHomeModel())
        _activity = State(initialValue: model.makeActivityModel())
        _approvals = State(initialValue: model.makeApprovalsModel())
        // `--ui-test-section <name>` opens straight to a section for screenshots.
        _selection = State(initialValue: MainSection.launchSection ?? .home)
    }

    var body: some View {
        HStack(spacing: 0) {
            SidebarRail(
                selection: $selection,
                expanded: $sidebarExpanded,
                approvalCount: model.pendingApprovalCount
            )
            Divider().overlay(DesignSystem.Colors.border)
            detail(for: selection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(DesignSystem.Colors.canvas)
        }
        .frame(minWidth: 720, minHeight: 480)
        .tint(DesignSystem.Colors.accent)
        // The sidebar toggle lives in the titlebar next to the traffic lights,
        // matching a Flow/Linear-style collapsible sidebar.
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button {
                    sidebarExpanded.toggle()
                } label: {
                    Image(systemName: "sidebar.left")
                }
                .help(sidebarExpanded ? "Collapse sidebar" : "Expand sidebar")
                .accessibilityLabel(sidebarExpanded ? "Collapse sidebar" : "Expand sidebar")
            }
        }
        // Drop the titlebar hairline and the redundant window title (the brand
        // lives in the sidebar) so the rail and content read as one surface.
        .background(WindowConfigurator {
            $0.titlebarSeparatorStyle = .none
            $0.titleVisibility = .hidden
        })
    }

    @ViewBuilder
    private func detail(for section: MainSection) -> some View {
        switch section {
        case .home:
            HomeView(model: home) { selection = .activity }
        case .activity:
            ActivityView(model: activity)
        case .approvals:
            ApprovalsView(model: approvals)
        case .settings:
            SettingsView(model: model)
        }
    }
}

/// Reaches the hosting `NSWindow` to apply window-level tweaks SwiftUI doesn't
/// expose (here: the titlebar separator style). Renders nothing.
private struct WindowConfigurator: NSViewRepresentable {
    let configure: (NSWindow) -> Void

    func makeNSView(context _: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { if let window = view.window { configure(window) } }
        return view
    }

    func updateNSView(_ nsView: NSView, context _: Context) {
        DispatchQueue.main.async { if let window = nsView.window { configure(window) } }
    }
}
