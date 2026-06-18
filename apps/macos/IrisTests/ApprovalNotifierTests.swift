import Foundation
import UserNotifications
import XCTest
@testable import IrisKit

/// Scriptable approval source; thread-safe via lock (the notifier actor
/// calls from its own executor).
final class FakeApprovalSource: ApprovalSource, @unchecked Sendable {
    private let lock = NSLock()
    private var _pending: [Approval] = []
    private var _decisions: [(id: String, decision: ApprovalDecision)] = []
    var fetchError: Error?
    var decideError: Error?
    private var _fetchCount = 0

    var pending: [Approval] {
        get { lock.withLock { _pending } }
        set { lock.withLock { _pending = newValue } }
    }

    var decisions: [(id: String, decision: ApprovalDecision)] {
        lock.withLock { _decisions }
    }

    var fetchCount: Int {
        lock.withLock { _fetchCount }
    }

    func pendingApprovals() async throws -> [Approval] {
        lock.withLock { _fetchCount += 1 }
        if let fetchError { throw fetchError }
        return pending
    }

    func decideApproval(
        id: String, decision: ApprovalDecision
    ) async throws -> ApprovalDecisionResponse {
        if let decideError { throw decideError }
        lock.withLock { _decisions.append((id, decision)) }
        return ApprovalDecisionResponse(
            ok: true, status: decision == .approve ? "approved" : "denied"
        )
    }
}

final class FakeNotificationPresenter: NotificationPresenting, @unchecked Sendable {
    private let lock = NSLock()
    private var _delivered: [(id: String, body: String)] = []
    private var _removed: [String] = []
    private var _authorizationRequests = 0
    private var _categorySetups = 0

    var delivered: [(id: String, body: String)] {
        lock.withLock { _delivered }
    }

    var removed: [String] {
        lock.withLock { _removed }
    }

    var authorizationRequests: Int {
        lock.withLock { _authorizationRequests }
    }

    var categorySetups: Int {
        lock.withLock { _categorySetups }
    }

    func requestAuthorization() async -> Bool {
        lock.withLock { _authorizationRequests += 1 }
        return true
    }

    func setupApprovalCategory() {
        lock.withLock { _categorySetups += 1 }
    }

    func deliver(id: String, title _: String, body: String) async {
        lock.withLock { _delivered.append((id, body)) }
    }

    func removeDelivered(ids: [String]) {
        lock.withLock { _removed.append(contentsOf: ids) }
    }
}

private func approval(_ id: String, preview: String = "Run a shell command") -> Approval {
    Approval(
        approvalId: id,
        runId: "run-1",
        actionName: "system.run",
        risk: "sensitive",
        status: "pending",
        preview: preview,
        createdAt: "2026-06-12T00:00:00+00:00",
        expiresAt: nil,
        decidedAt: nil
    )
}

extension Approval {
    init(
        approvalId: String, runId: String?, actionName: String, risk: String,
        status: String, preview: String, createdAt: String,
        expiresAt: String?, decidedAt: String?
    ) {
        let json = """
        {"approval_id": "\(approvalId)", "run_id": \(runId.map { "\"\($0)\"" } ?? "null"),
         "action_name": "\(actionName)", "risk": "\(risk)", "status": "\(status)",
         "preview": "\(preview)", "created_at": "\(createdAt)",
         "expires_at": \(expiresAt.map { "\"\($0)\"" } ?? "null"),
         "decided_at": \(decidedAt.map { "\"\($0)\"" } ?? "null")}
        """
        // Decoding through the real decoder keeps the test model honest.
        // swiftlint:disable:next force_try
        self = try! IrisJSON.decoder().decode(Approval.self, from: Data(json.utf8))
    }
}

final class ApprovalNotifierTests: XCTestCase {
    private struct Fixture {
        let notifier: ApprovalNotifier
        let source: FakeApprovalSource
        let presenter: FakeNotificationPresenter
    }

    private func makeFixture() -> Fixture {
        let source = FakeApprovalSource()
        let presenter = FakeNotificationPresenter()
        return Fixture(
            notifier: ApprovalNotifier(
                source: source, presenter: presenter, pollInterval: .milliseconds(40)
            ),
            source: source,
            presenter: presenter
        )
    }

    func testNotifiesEachPendingApprovalExactlyOnce() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)
        source.pending = [approval("a1"), approval("a2", preview: "Send an email")]

        await notifier.pollOnce()
        await notifier.pollOnce() // same list again

        XCTAssertEqual(presenter.delivered.map(\.id), ["a1", "a2"])
        XCTAssertEqual(presenter.delivered.first?.body, "Run a shell command")
    }

    func testResolvedApprovalsHaveBannersWithdrawn() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)
        source.pending = [approval("a1")]
        await notifier.pollOnce()

        source.pending = [] // resolved elsewhere (voice, dashboard)
        await notifier.pollOnce()

        XCTAssertEqual(presenter.removed, ["a1"])
    }

    func testApproveActionRoutesToAPIAndClearsBanner() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)

        await notifier.handleResponse(
            actionIdentifier: ApprovalNotifier.Action.approve.rawValue,
            approvalId: "a1"
        )

        XCTAssertEqual(source.decisions.map(\.id), ["a1"])
        XCTAssertEqual(source.decisions.first?.decision, .approve)
        XCTAssertEqual(presenter.removed, ["a1"])
    }

    func testDenyActionRoutesToAPI() async {
        let fixture = makeFixture()
        let (notifier, source) = (fixture.notifier, fixture.source)

        await notifier.handleResponse(
            actionIdentifier: ApprovalNotifier.Action.deny.rawValue,
            approvalId: "a2"
        )

        XCTAssertEqual(source.decisions.first?.decision, .deny)
    }

    func testUnknownActionIsIgnored() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)

        await notifier.handleResponse(
            actionIdentifier: UNNotificationDefaultActionIdentifier,
            approvalId: "a1"
        )

        XCTAssertTrue(source.decisions.isEmpty)
        XCTAssertTrue(presenter.removed.isEmpty)
    }

    func testDecisionFailureLeavesBannerAndApprovalPending() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)
        source.decideError = IrisAPIError.daemonUnreachable(detail: "down")

        await notifier.handleResponse(
            actionIdentifier: ApprovalNotifier.Action.approve.rawValue,
            approvalId: "a1"
        )

        XCTAssertTrue(presenter.removed.isEmpty, "banner must stay for retry")
    }

    func testFetchFailureDegradesSilentlyAndRecovers() async {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)
        source.fetchError = IrisAPIError.daemonUnreachable(detail: "down")
        await notifier.pollOnce()
        XCTAssertTrue(presenter.delivered.isEmpty)

        source.fetchError = nil
        source.pending = [approval("a1")]
        await notifier.pollOnce()
        XCTAssertEqual(presenter.delivered.map(\.id), ["a1"])
    }

    func testHealthDrivenLifecycleStartsAndStopsPolling() async throws {
        let fixture = makeFixture()
        let (notifier, source, presenter) = (fixture.notifier, fixture.source, fixture.presenter)
        source.pending = [approval("a1")]

        await notifier.daemonIsHealthy(true)
        try await Task.sleep(for: .milliseconds(120))
        XCTAssertEqual(presenter.delivered.map(\.id), ["a1"])
        XCTAssertGreaterThanOrEqual(source.fetchCount, 1)

        await notifier.daemonIsHealthy(false)
        try await Task.sleep(for: .milliseconds(60))
        let countAfterStop = source.fetchCount
        try await Task.sleep(for: .milliseconds(120))
        XCTAssertEqual(source.fetchCount, countAfterStop, "polling must stop")

        // restart does not re-request authorization
        await notifier.daemonIsHealthy(true)
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(presenter.authorizationRequests, 1)
        XCTAssertEqual(presenter.categorySetups, 1)
        await notifier.stop()
    }
}
