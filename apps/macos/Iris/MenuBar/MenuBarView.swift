import IrisKit
import SwiftUI

/// The permanent home of the app: status first, then actions. Errors render
/// as a status line plus one recovery action — never an alert.
struct MenuBarView: View {
    let model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(model.daemonState.menuDescription)
        if let actionTitle = model.daemonState.recoveryActionTitle {
            Button(actionTitle) {
                model.restartDaemon()
            }
        }
        Divider()
        // Wired to the overlay in Step 4.3.
        Button("Talk to Iris") {}
            .disabled(true)
        Button("Open Iris") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        // Enabled when always-on listening ships (Step 6.1).
        Button("Pause Listening") {}
            .disabled(true)
        Divider()
        Button("Quit Iris") {
            NSApp.terminate(nil) // graceful path via AppDelegate
        }
        .keyboardShortcut("q")
    }
}
