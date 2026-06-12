import IrisKit
import SwiftUI

/// Menu bar glyph per daemon state — status is glanceable, never modal.
struct StatusIcon: View {
    let state: DaemonState

    var body: some View {
        Image(systemName: symbolName)
            .accessibilityLabel("Iris: \(state.menuDescription)")
    }

    private var symbolName: String {
        switch state {
        case .healthy:
            "waveform.circle"
        case .launching, .restarting:
            "ellipsis.circle"
        case .stopped:
            "pause.circle"
        case .unhealthy, .crashLooping, .portConflict, .tokenMismatch:
            "exclamationmark.circle"
        }
    }
}

extension DaemonState {
    /// One-line status for the menu's first row.
    var menuDescription: String {
        switch self {
        case .stopped:
            "Iris is stopped"
        case .launching:
            "Starting…"
        case let .healthy(adopted):
            adopted ? "Running (external daemon)" : "Running"
        case .unhealthy:
            "Running, but not responding"
        case let .restarting(attempt):
            "Restarting (attempt \(attempt))…"
        case .crashLooping:
            "Iris keeps crashing"
        case let .portConflict(reason):
            "Port conflict: \(reason)"
        case .tokenMismatch:
            "Connection token rejected"
        }
    }

    /// Label for the recovery action shown on actionable states.
    var recoveryActionTitle: String? {
        switch self {
        case .stopped:
            "Start Iris"
        case .crashLooping, .unhealthy:
            "Restart Iris"
        case .tokenMismatch:
            "Reconnect"
        case .portConflict:
            "Try Again"
        case .launching, .restarting, .healthy:
            nil
        }
    }
}
