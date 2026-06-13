import IrisKit
import SwiftUI

/// The overlay surface: flat material, hairline border, one accent, no
/// gradients or glows. Content is driven by the voice session view model;
/// the pill is compact and grows with the transcript.
struct OverlayRootView: View {
    let model: VoiceSessionViewModel

    var body: some View {
        OverlayContent(model: model)
            .padding(.horizontal, 18)
            .padding(.vertical, 14)
            .frame(width: 380, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
            .overlay(
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(.separator, lineWidth: 1)
            )
            .animation(.easeOut(duration: 0.15), value: model.displayState)
            .accessibilityIdentifier("overlay-panel")
    }
}
