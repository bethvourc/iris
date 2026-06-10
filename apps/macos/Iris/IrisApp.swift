import SwiftUI

/// Menu-bar-only shell (LSUIElement). The real menu, daemon supervision,
/// and windows arrive in Phases 2–5; this scaffold establishes the app's
/// permanent posture: quiet presence in the menu bar, no Dock icon.
@main
struct IrisApp: App {
    var body: some Scene {
        MenuBarExtra("Iris", systemImage: "waveform.circle") {
            MenuBarMenu()
        }
    }
}

private struct MenuBarMenu: View {
    var body: some View {
        Text("Iris — scaffold build")
        Divider()
        Button("Quit Iris") {
            NSApp.terminate(nil)
        }
        .keyboardShortcut("q")
    }
}
