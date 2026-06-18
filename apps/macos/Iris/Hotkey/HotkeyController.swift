import IrisKit
import KeyboardShortcuts
import os

extension KeyboardShortcuts.Name {
    /// The activation gesture. Default ⌥Space; the recorder UI for
    /// changing it arrives with Settings (Step 5.5).
    static let talkToIris = Self("talkToIris", default: .init(.space, modifiers: [.option]))
}

/// Glues the global shortcut to the activation toggle. The overlay's voice
/// view model (Step 4.3) owns what activate/deactivate actually do and
/// feeds session truth back via `sessionStateChanged`.
@MainActor
final class HotkeyController {
    private let toggle = VoiceActivationToggle()
    private let onActivate: @MainActor () -> Void
    private let onDeactivate: @MainActor () -> Void
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "hotkey")

    init(
        onActivate: @escaping @MainActor () -> Void,
        onDeactivate: @escaping @MainActor () -> Void
    ) {
        self.onActivate = onActivate
        self.onDeactivate = onDeactivate
        KeyboardShortcuts.onKeyDown(for: .talkToIris) { [weak self] in
            self?.pressed()
        }
        let description = shortcutDescription
        logger.info("global hotkey registered: \(description, privacy: .public)")
    }

    var shortcutDescription: String {
        KeyboardShortcuts.getShortcut(for: .talkToIris)?.description ?? "none"
    }

    func sessionStateChanged(isActive: Bool) {
        toggle.update(sessionActive: isActive)
    }

    func activationFailed() {
        toggle.activationFailed()
    }

    /// Explicit dismissal (Esc, programmatic): reset to idle so the next
    /// press activates again rather than being treated as a deactivate.
    func cancel() {
        toggle.cancel()
    }

    private func pressed() {
        switch toggle.press() {
        case .activate:
            logger.info("hotkey: activate")
            onActivate()
        case .deactivate:
            logger.info("hotkey: deactivate")
            onDeactivate()
        }
    }
}
