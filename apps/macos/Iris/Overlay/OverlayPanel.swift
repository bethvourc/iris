import AppKit

/// The Siri-style overlay surface: a non-activating floating panel.
///
/// It never becomes key or main, so whatever app was frontmost keeps its
/// focus and the user can keep typing there while Iris listens. It floats
/// above full-screen apps and rides along to every Space.
final class OverlayPanel: NSPanel {
    init(contentRect: NSRect) {
        super.init(
            contentRect: contentRect,
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [
            .canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle
        ]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        hidesOnDeactivate = false
        isMovableByWindowBackground = false
        // We animate visibility ourselves (see OverlayController).
        animationBehavior = .none
    }

    /// The focus contract: never steal key/main from the frontmost app.
    override var canBecomeKey: Bool {
        false
    }

    override var canBecomeMain: Bool {
        false
    }
}
