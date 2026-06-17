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
    /// Collapsible sidebar (VS Code style). Starts shown; the toolbar toggle
    /// slides it away to give the content the full window.
    @State private var sidebarVisible = true
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
        // `--ui-test-sidebar collapsed` starts with the sidebar hidden so the
        // collapsed layout is screenshottable.
        let collapsed = ProcessInfo.processInfo.arguments
            .firstIndex(of: "--ui-test-sidebar")
            .map { ProcessInfo.processInfo.arguments[$0 + 1] } == "collapsed"
        _sidebarVisible = State(initialValue: !collapsed)
    }

    var body: some View {
        HStack(spacing: 0) {
            if sidebarVisible {
                SidebarRail(selection: $selection, approvalCount: model.pendingApprovalCount)
                    .transition(.move(edge: .leading))
                Divider().overlay(DesignSystem.Colors.border)
            }
            detail(for: selection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                // With the sidebar hidden, content fills under the floating
                // traffic lights / toggle — inset the top so nothing hides.
                .padding(.top, sidebarVisible ? 0 : SidebarToggle.titleBarHeight)
                .background(DesignSystem.Colors.canvas)
        }
        .animation(.easeInOut(duration: 0.22), value: sidebarVisible)
        // The toggle stays put next to the traffic lights in both states, so
        // it reads as a title-bar control rather than part of the sidebar.
        .overlay(alignment: .topLeading) {
            // Break out of the hidden-titlebar top safe area so the toggle
            // lands on the traffic-lights row, not below it.
            SidebarToggle(isOn: $sidebarVisible)
                .ignoresSafeArea(.container, edges: .top)
        }
        .frame(minWidth: 720, minHeight: 480)
        .tint(DesignSystem.Colors.accent)
        .background(WindowConfigurator { $0.titlebarSeparatorStyle = .none })
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

/// A borderless title-bar control that collapses/expands the sidebar, in the
/// spirit of VS Code's panel toggle: a quiet `sidebar.leading` glyph that only
/// gains a background on hover. Pinned just past the traffic lights so it sits
/// where a title-bar button would, in both sidebar states.
private struct SidebarToggle: View {
    @Binding var isOn: Bool
    @State private var hovering = false

    /// Top inset the content needs when the sidebar is hidden, so it clears
    /// this control and the floating traffic lights.
    static let titleBarHeight: CGFloat = 38

    var body: some View {
        Button {
            isOn.toggle()
        } label: {
            Image(systemName: "sidebar.leading")
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(hovering
                    ? DesignSystem.Colors.textPrimary
                    : DesignSystem.Colors.textSecondary)
                .frame(width: 26, height: 22)
                .background(
                    RoundedRectangle(cornerRadius: DesignSystem.Radius.sm)
                        .fill(hovering ? DesignSystem.Colors.surface : .clear)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(isOn ? "Hide sidebar" : "Show sidebar")
        .accessibilityLabel(isOn ? "Hide sidebar" : "Show sidebar")
        .accessibilityIdentifier("sidebar-toggle")
        // Clear the traffic lights (≈x 13–75) and center on their row.
        .padding(.leading, 80)
        .padding(.top, 4)
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
