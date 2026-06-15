import Foundation
import os
import UserNotifications

/// What the notifier needs from approvals — `APIClient` conforms; tests
/// substitute a scripted source.
public protocol ApprovalSource: Sendable {
    func pendingApprovals() async throws -> [Approval]
    @discardableResult
    func decideApproval(
        id: String, decision: ApprovalDecision
    ) async throws -> ApprovalDecisionResponse
}

extension APIClient: ApprovalSource {
    public func pendingApprovals() async throws -> [Approval] {
        try await approvals()
    }
}

/// Seam over UNUserNotificationCenter so notifier logic is unit-testable.
public protocol NotificationPresenting: Sendable {
    func requestAuthorization() async -> Bool
    func setupApprovalCategory()
    func deliver(id: String, title: String, body: String) async
    func removeDelivered(ids: [String])
}

/// Watches for pending approvals while the daemon is healthy and surfaces
/// them as actionable notifications. Approvals are Iris's safety model: a
/// blocked run the user never sees feels like a broken product.
///
/// Failure posture: every error degrades silently — the in-app Approvals
/// view (Step 5.4) is the durable console, and an undecided approval stays
/// safely pending.
public actor ApprovalNotifier {
    public enum Action: String, Sendable {
        case approve = "IRIS_APPROVE"
        case deny = "IRIS_DENY"
    }

    public static let categoryIdentifier = "IRIS_APPROVAL"

    private let source: any ApprovalSource
    private let presenter: any NotificationPresenting
    private let pollInterval: Duration
    /// Reports the current pending count after each poll so the app can badge
    /// the sidebar and menu bar icon.
    private let onPendingCount: (@Sendable (Int) -> Void)?
    private var pollTask: Task<Void, Never>?
    private var notifiedIds: Set<String> = []
    private var presenterConfigured = false
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "approvals")

    public init(
        source: any ApprovalSource,
        presenter: any NotificationPresenting,
        pollInterval: Duration = .seconds(10),
        onPendingCount: (@Sendable (Int) -> Void)? = nil
    ) {
        self.source = source
        self.presenter = presenter
        self.pollInterval = pollInterval
        self.onPendingCount = onPendingCount
    }

    /// Drive from the daemon state stream: polls only while healthy.
    public func daemonIsHealthy(_ healthy: Bool) async {
        if healthy {
            await start()
        } else {
            stop()
        }
    }

    public func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    private func start() async {
        guard pollTask == nil else { return }
        if !presenterConfigured {
            presenterConfigured = true
            presenter.setupApprovalCategory()
            _ = await presenter.requestAuthorization()
        }
        pollTask = Task { [pollInterval] in
            while !Task.isCancelled {
                await self.pollOnce()
                try? await Task.sleep(for: pollInterval)
            }
        }
    }

    func pollOnce() async {
        let pending: [Approval]
        do {
            pending = try await source.pendingApprovals()
        } catch {
            return // daemon hiccup; never block or alarm
        }
        onPendingCount?(pending.count)
        for approval in pending where !notifiedIds.contains(approval.approvalId) {
            notifiedIds.insert(approval.approvalId)
            logger.info("approval pending: \(approval.approvalId, privacy: .public)")
            await presenter.deliver(
                id: approval.approvalId,
                title: "Iris needs approval",
                body: approval.preview
            )
        }
        // Approvals resolved elsewhere (voice, dashboard) leave the pending
        // list; pull their banners down so the user never acts on stale ones.
        let pendingIds = Set(pending.map(\.approvalId))
        let resolved = notifiedIds.subtracting(pendingIds)
        if !resolved.isEmpty {
            presenter.removeDelivered(ids: Array(resolved))
            notifiedIds.subtract(resolved)
        }
    }

    /// Routes a notification action. Unknown identifiers (e.g. clicking the
    /// banner body) are ignored until the main window exists (5.4).
    public func handleResponse(actionIdentifier: String, approvalId: String) async {
        let decision: ApprovalDecision
        switch actionIdentifier {
        case Action.approve.rawValue:
            decision = .approve
        case Action.deny.rawValue:
            decision = .deny
        default:
            return
        }
        do {
            _ = try await source.decideApproval(id: approvalId, decision: decision)
            presenter.removeDelivered(ids: [approvalId])
            logger.info(
                "approval \(approvalId, privacy: .public): \(decision.rawValue, privacy: .public)"
            )
        } catch {
            // Leave the banner and the approval pending — the safe default.
            logger.error("approval decision failed: \(error.localizedDescription)")
        }
    }
}

/// Real UNUserNotificationCenter-backed presenter.
/// `@unchecked Sendable`: UNUserNotificationCenter is thread-safe.
public final class SystemNotificationPresenter: NotificationPresenting, @unchecked Sendable {
    private let center = UNUserNotificationCenter.current()

    public init() {}

    public func requestAuthorization() async -> Bool {
        await (try? center.requestAuthorization(options: [.alert, .sound])) ?? false
    }

    public func setupApprovalCategory() {
        let approve = UNNotificationAction(
            identifier: ApprovalNotifier.Action.approve.rawValue,
            title: "Approve"
        )
        let deny = UNNotificationAction(
            identifier: ApprovalNotifier.Action.deny.rawValue,
            title: "Deny"
        )
        let category = UNNotificationCategory(
            identifier: ApprovalNotifier.categoryIdentifier,
            actions: [approve, deny],
            intentIdentifiers: []
        )
        center.setNotificationCategories([category])
    }

    public func deliver(id: String, title: String, body: String) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.categoryIdentifier = ApprovalNotifier.categoryIdentifier
        content.sound = .default
        let request = UNNotificationRequest(identifier: id, content: content, trigger: nil)
        try? await center.add(request)
    }

    public func removeDelivered(ids: [String]) {
        center.removeDeliveredNotifications(withIdentifiers: ids)
    }
}
