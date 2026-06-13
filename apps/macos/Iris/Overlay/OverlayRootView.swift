import SwiftUI

/// Scaffold overlay content for Step 4.2 — establishes the surface (flat
/// material, hairline border, one accent, no gradients or glows) and the
/// compact pill shape. Step 4.3 replaces the body with the live voice
/// session state machine driven by the SSE stream.
struct OverlayRootView: View {
    var body: some View {
        HStack(spacing: 12) {
            SiriNewMark()
                .frame(width: 22, height: 22)
                .foregroundStyle(.tint)
            Text("Listening…")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.primary)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
        .frame(width: 360, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
        .overlay(
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(.separator, lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("overlay-panel")
        .accessibilityLabel("Iris overlay: listening")
    }
}

#Preview {
    OverlayRootView()
        .padding(40)
}
