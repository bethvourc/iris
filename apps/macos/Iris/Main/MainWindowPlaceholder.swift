import SwiftUI

/// Stand-in until the real main window (Phase 5).
struct MainWindowPlaceholder: View {
    var body: some View {
        VStack(spacing: 8) {
            SiriNewMark()
                .frame(width: 44, height: 44)
                .foregroundStyle(.secondary)
            Text("Iris")
                .font(.title2)
            Text("The console arrives in Phase 5.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(minWidth: 480, minHeight: 320)
    }
}
