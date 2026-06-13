import Foundation

/// Toggle semantics for the activation gesture: one key to remember —
/// pressing it starts a session when idle and stops/interrupts when
/// anything is in flight. Session truth is fed back by the voice view
/// model (Step 4.3); `pending` covers the gap between requesting a start
/// and the daemon confirming it, so a rapid second press cancels cleanly.
@MainActor
public final class VoiceActivationToggle {
    public enum Phase: Equatable, Sendable {
        case idle
        /// Activation requested; session not yet confirmed by the daemon.
        case pending
        case active
    }

    public enum Effect: Equatable, Sendable {
        case activate
        case deactivate
    }

    public private(set) var phase: Phase = .idle

    public init() {}

    /// The hotkey was pressed; returns what the caller should do.
    public func press() -> Effect {
        switch phase {
        case .idle:
            phase = .pending
            return .activate
        case .pending, .active:
            // Optimistically idle; `update` restores .active if the stop
            // request fails and the session is still live.
            phase = .idle
            return .deactivate
        }
    }

    /// Reconcile with session truth (from the SSE state stream).
    public func update(sessionActive: Bool) {
        if sessionActive {
            phase = .active
        } else if phase == .active {
            phase = .idle
        }
        // A `pending` phase stays pending while the start is in flight;
        // only session truth, a second press, or activationFailed move it.
    }

    /// The start request failed outright (daemon down, 500, …).
    public func activationFailed() {
        if phase == .pending {
            phase = .idle
        }
    }

    /// Explicit teardown (Esc, programmatic dismiss): forget any session
    /// from any phase. Distinct from `update(sessionActive: false)`, which
    /// is SSE reconciliation and deliberately preserves `.pending`.
    public func cancel() {
        phase = .idle
    }
}
