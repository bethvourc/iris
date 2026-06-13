import AppKit
import IrisKit
import SwiftUI

/// Owns the overlay panel's lifecycle: show/hide with a brief fade+slide
/// (skipped under Reduce Motion), bottom-center positioning on the screen
/// with the mouse, content sizing, and Esc-to-dismiss.
///
/// Primary dismissal is the activation hotkey (Step 4.1); Esc is a
/// best-effort convenience via a global monitor and needs Accessibility,
/// which Iris requests in onboarding.
@MainActor
final class OverlayController {
    private let rootView: AnyView
    private let onDismiss: () -> Void
    private var panel: OverlayPanel?
    private var escMonitors: [Any] = []
    private let bottomMargin: CGFloat = 140
    private let animationDuration: TimeInterval = 0.18

    private(set) var isVisible = false

    init(onDismiss: @escaping () -> Void, @ViewBuilder rootView: () -> some View) {
        self.onDismiss = onDismiss
        self.rootView = AnyView(rootView())
    }

    func show() {
        let panel = panel ?? makePanel()
        self.panel = panel
        guard !isVisible else { return }
        isVisible = true

        let size = panel.contentView?.fittingSize ?? panel.frame.size
        panel.setContentSize(size)
        let origin = OverlayGeometry.origin(
            screenFrame: screenWithMouse().visibleFrame,
            size: panel.frame.size,
            bottomMargin: bottomMargin
        )
        panel.setFrameOrigin(origin)
        installEscMonitors()

        if reduceMotion {
            panel.alphaValue = 1
            panel.orderFrontRegardless()
            return
        }
        panel.alphaValue = 0
        panel.setFrameOrigin(NSPoint(x: origin.x, y: origin.y - 12))
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = animationDuration
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().alphaValue = 1
            panel.animator().setFrameOrigin(origin)
        }
    }

    func hide() {
        guard isVisible, let panel else { return }
        isVisible = false
        removeEscMonitors()

        guard !reduceMotion else {
            panel.orderOut(nil)
            return
        }
        let origin = panel.frame.origin
        NSAnimationContext.runAnimationGroup { context in
            context.duration = animationDuration
            context.timingFunction = CAMediaTimingFunction(name: .easeIn)
            panel.animator().alphaValue = 0
            panel.animator().setFrameOrigin(NSPoint(x: origin.x, y: origin.y - 12))
        } completionHandler: { [weak panel] in
            panel?.orderOut(nil)
        }
    }

    // MARK: - Internals

    private func makePanel() -> OverlayPanel {
        let panel = OverlayPanel(contentRect: NSRect(x: 0, y: 0, width: 360, height: 72))
        let hosting = NSHostingView(rootView: rootView)
        hosting.sizingOptions = [.intrinsicContentSize]
        panel.contentView = hosting
        return panel
    }

    private var reduceMotion: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    }

    private func screenWithMouse() -> NSScreen {
        let mouse = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main
            ?? NSScreen.screens[0]
    }

    private func installEscMonitors() {
        let handler: (NSEvent) -> Void = { [weak self] event in
            guard event.keyCode == 53 else { return } // Esc
            Task { @MainActor in self?.dismiss() }
        }
        let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { handler($0) }
        let local = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            handler(event)
            return event
        }
        escMonitors = [global, local].compactMap(\.self)
    }

    private func removeEscMonitors() {
        escMonitors.forEach(NSEvent.removeMonitor)
        escMonitors.removeAll()
    }

    private func dismiss() {
        hide()
        onDismiss()
    }
}
