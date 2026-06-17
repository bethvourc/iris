import IrisKit
import SwiftUI

/// Menu bar glyph per daemon state — the SiriNew brand mark, full strength
/// when healthy, dimmed while not running, dot-badged when something needs
/// attention. Status is glanceable, never modal.
struct StatusIcon: View {
    let state: DaemonState
    /// Pending approvals — shown as a small count badge on the glyph.
    var approvalCount = 0
    /// The on-device wake-word loop is armed — shown as a small accent dot, so
    /// "Iris is listening" is always honest at a glance (never silent).
    var listening = false

    var body: some View {
        Image(nsImage: SiriNewIcon.image(variant))
            .overlay(alignment: .topTrailing) {
                if approvalCount > 0 {
                    Text(approvalCount > 9 ? "9+" : "\(approvalCount)")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 3)
                        .frame(minWidth: 12, minHeight: 12)
                        .background(Capsule().fill(DesignSystem.Colors.accent))
                        .offset(x: 5, y: -4)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if listening, approvalCount == 0 {
                    Circle()
                        .fill(DesignSystem.Colors.accent)
                        .frame(width: 5, height: 5)
                        .offset(x: 3, y: 3)
                }
            }
            .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        var label = "Iris: \(state.menuDescription)"
        if listening { label += ", listening for wake word" }
        if approvalCount > 0 { label += ", \(approvalCount) approvals pending" }
        return label
    }

    private var variant: SiriNewIcon.Variant {
        switch state {
        case .healthy:
            .normal
        case .launching, .restarting, .stopped:
            .dimmed
        case .unhealthy, .crashLooping, .portConflict, .tokenMismatch:
            .badged
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
