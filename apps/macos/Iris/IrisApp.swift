import AppKit
import SwiftUI

/// Menu-bar-only shell (LSUIElement): quiet presence in the menu bar, no
/// Dock icon. The menu is the always-available escape hatch when other
/// surfaces fail (docs/desktop/architecture.md §2).
@main
struct IrisApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var model: AppModel

    init() {
        let model = AppModel()
        _model = State(initialValue: model)
        model.bootstrap()
        AppDelegate.shutdown = { await model.shutdownForQuit() }
    }

    var body: some Scene {
        // MenuBarExtra first: it is the primary scene, so the window below
        // does not auto-open at launch.
        MenuBarExtra {
            MenuBarView(model: model)
        } label: {
            StatusIcon(state: model.daemonState)
        }

        Window("Iris", id: "main") {
            MainWindowPlaceholder()
        }
    }
}

/// Ensures every quit path (menu, Cmd-Q, logout) stops the daemon first;
/// the quit path must leave no orphan Python processes.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static var shutdown: (@MainActor () async -> Void)?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let shutdown = Self.shutdown else { return .terminateNow }
        Task { @MainActor in
            await shutdown()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}
