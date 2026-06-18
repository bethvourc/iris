import Foundation
import Observation

/// Drives the Approvals section: the durable safety console. Notifications
/// (Step 3.3) are transient; this view is where blocked actions wait for an
/// explicit decision and where the user's recent decisions linger as history.
///
/// Safety posture mirrors the daemon's: every failure leaves the approval
/// **pending** (the safe default), and an approval resolved elsewhere (voice,
/// dashboard, a notification) reconciles quietly — never an error flash.
@MainActor
@Observable
public final class ApprovalsViewModel {
    public enum State: Equatable, Sendable {
        case loading
        /// Loaded successfully; `pending`/`history` hold the content (either may
        /// be empty — the view shows the caught-up state when both are).
        case loaded
        case failed(message: String)
    }

    /// A decision the user made in this session, kept for the history section.
    public struct Resolved: Equatable, Sendable, Identifiable {
        public let approval: Approval
        public let decision: ApprovalDecision
        public var id: String {
            approval.approvalId
        }

        public init(approval: Approval, decision: ApprovalDecision) {
            self.approval = approval
            self.decision = decision
        }
    }

    public private(set) var state: State = .loading
    public private(set) var pending: [Approval] = []
    public private(set) var history: [Resolved] = []
    /// Approvals with an in-flight decision (buttons disabled, spinner shown).
    public private(set) var decidingIds: Set<String> = []
    /// The approval awaiting a destructive-action confirm step, if any.
    public private(set) var confirming: Approval?
    /// Non-fatal: a failed decision surfaces here without dropping content.
    public private(set) var actionError: String?

    public var pendingCount: Int {
        pending.count
    }

    private let source: any ApprovalSource
    private let pollInterval: Duration
    /// First load shows the skeleton; later refreshes keep content on screen.
    private var hasLoaded = false

    public init(source: any ApprovalSource, pollInterval: Duration = .seconds(5)) {
        self.source = source
        self.pollInterval = pollInterval
    }

    /// Initial load, then keep polling so approvals resolved elsewhere fall off
    /// the list on their own. Cancelled automatically when the view disappears.
    public func start() async {
        await refresh()
        while !Task.isCancelled {
            try? await Task.sleep(for: pollInterval)
            if Task.isCancelled { break }
            await refresh()
        }
    }

    public func refresh() async {
        if !hasLoaded { state = .loading }
        do {
            let latest = try await source.pendingApprovals()
            reconcile(with: latest)
            state = .loaded
            hasLoaded = true
        } catch {
            // Keep content on a refresh blip; only show the error screen if we
            // have never loaded.
            if !hasLoaded { state = .failed(message: Self.message(for: error)) }
        }
    }

    // MARK: - Decisions

    /// Approve. Destructive (blocked) actions route through a confirm step
    /// first; everything else decides immediately.
    public func approve(_ approval: Approval) async {
        if Self.isDestructive(approval) {
            confirming = approval
            return
        }
        await decide(approval, .approve)
    }

    /// Confirm the pending destructive approval (the second step).
    public func confirmApprove() async {
        guard let approval = confirming else { return }
        confirming = nil
        await decide(approval, .approve)
    }

    public func cancelConfirm() {
        confirming = nil
    }

    public func dismissError() {
        actionError = nil
    }

    /// Deny is always one tap — denying is the safe direction, so it never gets
    /// a confirm step and is never made less reachable than Approve.
    public func deny(_ approval: Approval) async {
        await decide(approval, .deny)
    }

    private func decide(_ approval: Approval, _ decision: ApprovalDecision) async {
        let id = approval.approvalId
        guard !decidingIds.contains(id) else { return }
        decidingIds.insert(id)
        actionError = nil
        defer { decidingIds.remove(id) }
        do {
            _ = try await source.decideApproval(id: id, decision: decision)
            resolveLocally(approval, decision)
        } catch {
            if Self.isAlreadyResolved(error) {
                // It was decided elsewhere between render and tap. Reconcile
                // quietly: drop it from pending, no error flash.
                pending.removeAll { $0.approvalId == id }
                confirming = confirming?.approvalId == id ? nil : confirming
            } else {
                // Transient failure: leave it pending (safe) and explain.
                actionError = Self.message(for: error)
            }
        }
    }

    private func resolveLocally(_ approval: Approval, _ decision: ApprovalDecision) {
        pending.removeAll { $0.approvalId == approval.approvalId }
        history.removeAll { $0.approval.approvalId == approval.approvalId }
        history.insert(Resolved(approval: approval, decision: decision), at: 0)
    }

    /// Replace the pending list with the server's truth, preserving any
    /// confirm/in-flight state that still applies.
    private func reconcile(with latest: [Approval]) {
        let ids = Set(latest.map(\.approvalId))
        if let confirming, !ids.contains(confirming.approvalId) {
            self.confirming = nil
        }
        decidingIds.formIntersection(ids)
        pending = latest
    }

    // MARK: - Helpers

    /// Destructive ("blocked") actions are the highest-risk tier and earn the
    /// confirm step before they can be approved.
    public static func isDestructive(_ approval: Approval) -> Bool {
        approval.risk.lowercased() == "blocked"
    }

    /// A decision error that means the approval is simply gone — already
    /// approved/denied/expired — so it should reconcile silently.
    static func isAlreadyResolved(_ error: Error) -> Bool {
        switch error {
        case IrisAPIError.notFound, IrisAPIError.conflict, IrisAPIError.invalidRequest:
            true
        default:
            false
        }
    }

    /// Parse the daemon's raw `created_at` (Python isoformat — may carry a
    /// `+00:00` offset and microseconds, which the shared decoder doesn't
    /// guarantee). Falls back across a few strategies.
    public static func parseTimestamp(_ raw: String) -> Date? {
        if let date = try? Date(raw, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)) {
            return date
        }
        if let date = try? Date(raw, strategy: .iso8601) {
            return date
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: raw) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: raw)
    }

    /// Compact relative age ("just now", "5m ago", "2h ago", "3d ago").
    public static func ageText(since date: Date, now: Date = .now) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        return switch seconds {
        case ..<60: "just now"
        case ..<3600: "\(Int(seconds / 60))m ago"
        case ..<86400: "\(Int(seconds / 3600))h ago"
        default: "\(Int(seconds / 86400))d ago"
        }
    }

    static func message(for error: Error) -> String {
        switch error {
        case IrisAPIError.daemonUnreachable:
            "Can't reach Iris right now."
        case IrisAPIError.unauthorized:
            "Iris rejected the connection token."
        default:
            (error as? LocalizedError)?.errorDescription ?? "Something went wrong."
        }
    }
}
