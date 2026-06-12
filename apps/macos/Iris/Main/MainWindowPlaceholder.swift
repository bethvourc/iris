import SwiftUI

/// Stand-in until the real main window (Phase 5).
struct MainWindowPlaceholder: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "waveform.circle")
                .font(.system(size: 40, weight: .regular))
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
