import IrisKit
import SwiftUI

/// The console's main window: a `NavigationSplitView` whose sidebar selects one
/// of four sections. This shell defines all navigation up front; Steps 5.2–5.5
/// replace each placeholder detail with the real Home, Activity, Approvals, and
/// Settings views. The window opens from the menu bar / dock-less activation
/// and closes back to the menu bar — it never quits the app (handled by the
/// menu-bar-only `IrisApp` scene).
struct MainWindowView: View {
    /// Held for the section view models wired up in Steps 5.2–5.5.
    let model: AppModel
    @State private var selection: MainSection = .home
    /// Pin the sidebar open: this is a persistent two-pane console, not a
    /// collapsible inspector, so the rail should never auto-hide.
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SidebarView(selection: $selection)
        } detail: {
            SectionPlaceholder(section: selection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(DesignSystem.Colors.canvas)
        }
        // Keep both columns side by side rather than overlaying the sidebar.
        .navigationSplitViewStyle(.balanced)
        // The section name lives in the titlebar alone — no in-content echo.
        .navigationTitle(selection.title)
        .frame(minWidth: 720, minHeight: 480)
        .tint(DesignSystem.Colors.accent)
    }
}

/// Temporary detail content for a section: a restrained, centered placeholder
/// until the real view lands in a later step. Carries a stable accessibility
/// identifier so navigation tests can assert which section is showing.
private struct SectionPlaceholder: View {
    let section: MainSection

    var body: some View {
        VStack(spacing: DesignSystem.Spacing.md) {
            Image(systemName: section.systemImage)
                .font(.system(size: 24, weight: .light))
                .foregroundStyle(DesignSystem.Colors.textTertiary)

            Text(section.summary)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textSecondary)
                .multilineTextAlignment(.center)

            if section == .home {
                Text("Press ⌥Space to start a conversation")
                    .font(DesignSystem.Typography.caption)
                    .foregroundStyle(DesignSystem.Colors.textTertiary)
                    .padding(.top, DesignSystem.Spacing.xs)
            }
        }
        .frame(maxWidth: 360)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityIdentifier("section-\(section.rawValue)")
    }
}
