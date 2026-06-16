import AppKit
import IrisKit
import SwiftUI

/// The overlay surface: flat material, hairline border, one accent, no
/// gradients or glows. Content is driven by the voice session view model;
/// the pill is compact and grows with the transcript.
struct OverlayRootView: View {
    let model: VoiceSessionViewModel
    /// The last phrase we spoke, so an unchanged phase (e.g. listening →
    /// userSpeaking) doesn't re-announce.
    @State private var lastAnnouncement: String?

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
            .overlay(alignment: .topTrailing) {
                if model.connectionState == .reconnecting, model.displayState.isActiveSession {
                    ReconnectingChip()
                        .padding(8)
                        .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.15), value: model.displayState)
            .animation(.easeOut(duration: 0.15), value: model.connectionState)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Iris voice session")
            .accessibilityIdentifier("overlay-panel")
            .onAppear { announce(model.displayState.accessibilityAnnouncement) }
            .onChange(of: model.displayState) { _, state in
                announce(state.accessibilityAnnouncement)
            }
    }

    /// Speak a session-phase change through VoiceOver. The panel is a
    /// non-activating popover, so the announcement (rather than a focus move)
    /// is the only way assistive tech learns the session advanced.
    private func announce(_ phrase: String?) {
        guard let phrase, phrase != lastAnnouncement else { return }
        lastAnnouncement = phrase
        NSAccessibility.post(
            element: NSApp as Any,
            notification: .announcementRequested,
            userInfo: [
                .announcement: phrase,
                .priority: NSAccessibilityPriorityLevel.high.rawValue,
            ]
        )
    }
}
