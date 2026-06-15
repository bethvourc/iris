import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class ApprovalsViewModelTests: XCTestCase {
    // MARK: - Fixtures

    private func approval(
        _ id: String, risk: String = "sensitive", run: String? = "run-1",
        action: String = "Send email", created: String = "2026-06-14T16:00:00Z"
    ) -> Approval {
        Approval(
            approvalId: id, runId: run, actionName: action, risk: risk,
            status: "pending", preview: "Approve \(action)?",
            createdAt: created, expiresAt: nil, decidedAt: nil
        )
    }

    private func model(_ outcomes: [ScriptedApprovalFake.FeedOutcome] = [],
                       decision: Result<Void, Error> = .success(())) -> (ApprovalsViewModel, ScriptedApprovalFake) {
        let source = ScriptedApprovalFake(feeds: outcomes, decision: decision)
        return (ApprovalsViewModel(source: source), source)
    }

    private func waitUntil(
        timeout: Duration = .seconds(2), _ predicate: @escaping () -> Bool
    ) async {
        let deadline = ContinuousClock.now + timeout
        while ContinuousClock.now < deadline {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    // MARK: - Load

    func testStartsInLoadingState() {
        let (vm, _) = model()
        XCTAssertEqual(vm.state, .loading)
    }

    func testRefreshLoadsPending() async {
        let (vm, _) = model([.success([approval("a"), approval("b")])])
        await vm.refresh()
        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.pending.map(\.approvalId), ["a", "b"])
        XCTAssertEqual(vm.pendingCount, 2)
    }

    func testEmptyLoad() async {
        let (vm, _) = model([.success([])])
        await vm.refresh()
        XCTAssertEqual(vm.state, .loaded)
        XCTAssertTrue(vm.pending.isEmpty)
        XCTAssertTrue(vm.history.isEmpty)
    }

    func testLoadFailureSurfacesError() async {
        let (vm, _) = model([.failure(IrisAPIError.daemonUnreachable(detail: "x"))])
        await vm.refresh()
        XCTAssertEqual(vm.state, .failed(message: "Can't reach Iris right now."))
    }

    func testRefreshFailureKeepsContent() async {
        let (vm, _) = model([
            .success([approval("a")]),
            .failure(IrisAPIError.daemonUnreachable(detail: "blip"))
        ])
        await vm.refresh()
        await vm.refresh()
        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.pending.map(\.approvalId), ["a"])
    }

    // MARK: - Decisions

    func testApproveLowRiskResolvesImmediately() async {
        let (vm, source) = model([.success([approval("a", risk: "sensitive")])])
        await vm.refresh()

        await vm.approve(vm.pending[0])

        XCTAssertTrue(vm.pending.isEmpty)
        XCTAssertEqual(vm.history.map(\.id), ["a"])
        XCTAssertEqual(vm.history.first?.decision, .approve)
        let decisions = await source.decisions
        XCTAssertEqual(decisions.map(\.0), ["a"])
    }

    func testDenyResolvesImmediately() async {
        let (vm, source) = model([.success([approval("a")])])
        await vm.refresh()

        await vm.deny(vm.pending[0])

        XCTAssertTrue(vm.pending.isEmpty)
        XCTAssertEqual(vm.history.first?.decision, .deny)
        let decisions = await source.decisions
        XCTAssertEqual(decisions.first?.1, .deny)
    }

    func testDestructiveApproveRequiresConfirm() async {
        let (vm, source) = model([.success([approval("a", risk: "blocked")])])
        await vm.refresh()

        await vm.approve(vm.pending[0])

        // First tap only arms the confirm step; nothing decided yet.
        XCTAssertEqual(vm.confirming?.approvalId, "a")
        XCTAssertEqual(vm.pending.map(\.approvalId), ["a"])
        let beforeConfirm = await source.decisions
        XCTAssertTrue(beforeConfirm.isEmpty)

        await vm.confirmApprove()

        XCTAssertNil(vm.confirming)
        XCTAssertTrue(vm.pending.isEmpty)
        XCTAssertEqual(vm.history.first?.decision, .approve)
        let afterConfirm = await source.decisions
        XCTAssertEqual(afterConfirm.map(\.0), ["a"])
    }

    func testCancelConfirmLeavesApprovalPending() async {
        let (vm, source) = model([.success([approval("a", risk: "blocked")])])
        await vm.refresh()
        await vm.approve(vm.pending[0])

        vm.cancelConfirm()

        XCTAssertNil(vm.confirming)
        XCTAssertEqual(vm.pending.map(\.approvalId), ["a"])
        let decisions = await source.decisions
        XCTAssertTrue(decisions.isEmpty)
    }

    func testDenyIsNeverDestructive() {
        // Deny never routes through the confirm step, even for blocked actions.
        XCTAssertTrue(ApprovalsViewModel.isDestructive(approval("a", risk: "blocked")))
        XCTAssertFalse(ApprovalsViewModel.isDestructive(approval("b", risk: "sensitive")))
    }

    // MARK: - Failure posture

    func testTransientDecisionFailureKeepsPending() async {
        let (vm, _) = model(
            [.success([approval("a")])],
            decision: .failure(IrisAPIError.server(code: "x", message: "boom", status: 500))
        )
        await vm.refresh()

        await vm.deny(vm.pending[0])

        XCTAssertEqual(vm.pending.map(\.approvalId), ["a"], "stays pending on failure")
        XCTAssertEqual(vm.actionError, "boom")
        XCTAssertTrue(vm.history.isEmpty)
    }

    func testResolvedElsewhereReconcilesWithoutErrorFlash() async {
        let (vm, _) = model(
            [.success([approval("a")])],
            decision: .failure(IrisAPIError.notFound(message: "gone"))
        )
        await vm.refresh()

        await vm.approve(vm.pending[0])

        XCTAssertTrue(vm.pending.isEmpty, "reconciled away")
        XCTAssertNil(vm.actionError, "no error flash for an already-resolved approval")
    }

    func testRefreshDropsApprovalsResolvedElsewhere() async {
        let (vm, _) = model([
            .success([approval("a"), approval("b")]),
            .success([approval("b")]) // 'a' resolved elsewhere
        ])
        await vm.refresh()
        await vm.refresh()
        XCTAssertEqual(vm.pending.map(\.approvalId), ["b"])
    }

    func testRefreshClearsConfirmIfTargetVanishes() async {
        let (vm, _) = model([
            .success([approval("a", risk: "blocked")]),
            .success([]) // 'a' resolved elsewhere while confirm armed
        ])
        await vm.refresh()
        await vm.approve(vm.pending[0])
        XCTAssertNotNil(vm.confirming)

        await vm.refresh()

        XCTAssertNil(vm.confirming)
    }

    // MARK: - Age formatting

    func testAgeText() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertEqual(ApprovalsViewModel.ageText(since: now.addingTimeInterval(-30), now: now), "just now")
        XCTAssertEqual(ApprovalsViewModel.ageText(since: now.addingTimeInterval(-120), now: now), "2m ago")
        XCTAssertEqual(ApprovalsViewModel.ageText(since: now.addingTimeInterval(-7200), now: now), "2h ago")
        XCTAssertEqual(ApprovalsViewModel.ageText(since: now.addingTimeInterval(-172_800), now: now), "2d ago")
    }

    func testParseTimestampHandlesOffsetAndFractional() {
        XCTAssertNotNil(ApprovalsViewModel.parseTimestamp("2026-06-14T16:00:00Z"))
        XCTAssertNotNil(ApprovalsViewModel.parseTimestamp("2026-06-14T16:00:00+00:00"))
        XCTAssertNotNil(ApprovalsViewModel.parseTimestamp("2026-06-14T16:00:00.123456+00:00"))
        XCTAssertNil(ApprovalsViewModel.parseTimestamp("not a date"))
    }
}

/// Scripted `ApprovalSource`. An actor so it's `Sendable` across the boundary;
/// records decisions and replays queued pending lists.
private actor ScriptedApprovalFake: ApprovalSource {
    enum FeedOutcome {
        case success([Approval])
        case failure(Error)
    }

    private var feeds: [FeedOutcome]
    private let decisionResult: Result<Void, Error>
    private(set) var decisions: [(String, ApprovalDecision)] = []

    init(feeds: [FeedOutcome], decision: Result<Void, Error>) {
        self.feeds = feeds
        decisionResult = decision
    }

    func pendingApprovals() async throws -> [Approval] {
        let outcome = feeds.count > 1 ? feeds.removeFirst() : (feeds.first ?? .success([]))
        switch outcome {
        case let .success(list): return list
        case let .failure(error): throw error
        }
    }

    @discardableResult
    func decideApproval(id: String, decision: ApprovalDecision) async throws -> ApprovalDecisionResponse {
        decisions.append((id, decision))
        switch decisionResult {
        case .success:
            return ApprovalDecisionResponse(ok: true, status: decision == .approve ? "approved" : "denied")
        case let .failure(error):
            throw error
        }
    }
}
