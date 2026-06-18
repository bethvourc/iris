import Foundation
import IrisKit

/// Deterministic `ApprovalSource` for screenshots and UI tests, selected via
/// `--ui-test-approvals <scenario>`. It decodes canned JSON through the real
/// contract decoder, so each state is reproducible without a daemon. The app
/// target can't use the models' internal initializers, hence the JSON.
struct ScriptedApprovalSource: ApprovalSource {
    let scenario: String

    func pendingApprovals() async throws -> [Approval] {
        switch scenario {
        case "error":
            throw IrisAPIError.daemonUnreachable(detail: "no daemon")
        case "empty":
            return []
        default:
            return try Self.decode(Self.pendingJSON(), as: [Approval].self)
        }
    }

    @discardableResult
    func decideApproval(id _: String, decision: ApprovalDecision) async throws -> ApprovalDecisionResponse {
        let status = decision == .approve ? "approved" : "denied"
        return try Self.decode(#"{"ok":true,"status":"\#(status)"}"#, as: ApprovalDecisionResponse.self)
    }

    /// How many pending approvals the default scenario shows (used to seed the
    /// sidebar/menu-bar badge for screenshots).
    static let pendingCount = 3

    private static func decode<T: Decodable>(_ json: String, as _: T.Type) throws -> T {
        try IrisJSON.decoder().decode(T.self, from: Data(json.utf8))
    }

    private static func iso(minutesAgo: Int) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: Date().addingTimeInterval(Double(-minutesAgo * 60)))
    }

    private static func pendingJSON() -> String {
        """
        [
          {"approval_id": "ap-1", "run_id": "run-8a3", "action_name": "Send email to Sam",
           "risk": "sensitive", "status": "pending",
           "preview": "Send the follow-up email to sam@example.com with the Q2 numbers.",
           "created_at": "\(iso(minutesAgo: 2))", "expires_at": null, "decided_at": null},
          {"approval_id": "ap-2", "run_id": "run-8a3", "action_name": "Delete 14 calendar events",
           "risk": "blocked", "status": "pending",
           "preview": "Remove every event tagged \\"old-standup\\" from your calendar.",
           "created_at": "\(iso(minutesAgo: 9))", "expires_at": null, "decided_at": null},
          {"approval_id": "ap-3", "run_id": "run-7c1", "action_name": "Create a reminder",
           "risk": "low_risk", "status": "pending",
           "preview": "Remind you to call the dentist at 3 p.m.",
           "created_at": "\(iso(minutesAgo: 41))", "expires_at": null, "decided_at": null}
        ]
        """
    }
}
