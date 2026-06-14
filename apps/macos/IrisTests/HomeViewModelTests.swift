import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class HomeViewModelTests: XCTestCase {
    // MARK: - Fixtures

    private func stats(
        sessions: Int = 3, completed: Int = 5, failed: Int = 1, lastActive: Date? = .now
    ) -> ActivityStats {
        ActivityStats(
            sessionsThisWeek: sessions,
            runsCompleted: completed,
            runsFailed: failed,
            lastActiveAt: lastActive
        )
    }

    private func item(_ id: String) -> ActivityItem {
        ActivityItem(
            id: id, kind: .voiceSession, time: .now,
            title: "Item \(id)", preview: nil, status: .done
        )
    }

    private func feed(days: [ActivityDay], stats: ActivityStats? = nil) -> ActivityFeed {
        ActivityFeed(stats: stats ?? self.stats(), days: days, nextCursor: nil)
    }

    private func day(_ ids: [String]) -> ActivityDay {
        ActivityDay(date: "2026-06-14", label: "Today", items: ids.map(item))
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

    // MARK: - Greeting

    func testGreetingUsesProvidedName() {
        let model = HomeViewModel(client: FakeActivityFetcher([]), greetingName: "Bethvour")
        XCTAssertEqual(model.greeting, "Welcome back, Bethvour")
    }

    func testGreetingFallsBackWhenNameMissing() {
        XCTAssertEqual(
            HomeViewModel(client: FakeActivityFetcher([]), greetingName: nil).greeting,
            "Welcome back"
        )
        XCTAssertEqual(
            HomeViewModel(client: FakeActivityFetcher([]), greetingName: "").greeting,
            "Welcome back"
        )
    }

    // MARK: - Load states

    func testStartsInLoadingState() {
        let model = HomeViewModel(client: FakeActivityFetcher([]), greetingName: nil)
        XCTAssertEqual(model.state, .loading)
    }

    func testPopulatedLoad() async {
        let expected = stats()
        let fetcher = FakeActivityFetcher([.success(feed(days: [day(["a", "b"])], stats: expected))])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()

        guard case let .loaded(loadedStats, recent) = model.state else {
            return XCTFail("expected .loaded, got \(model.state)")
        }
        XCTAssertEqual(loadedStats, expected)
        XCTAssertEqual(recent.map(\.id), ["a", "b"])
    }

    func testRecentPreviewCapsAtFiveInOrder() async {
        let across = feed(days: [day(["a", "b", "c"]), day(["d", "e", "f", "g"])])
        let model = HomeViewModel(client: FakeActivityFetcher([.success(across)]), greetingName: nil)

        await model.load()

        guard case let .loaded(_, recent) = model.state else {
            return XCTFail("expected .loaded, got \(model.state)")
        }
        XCTAssertEqual(recent.map(\.id), ["a", "b", "c", "d", "e"])
    }

    func testEmptyLoad() async {
        let expected = stats(sessions: 0, completed: 0, failed: 0, lastActive: nil)
        let fetcher = FakeActivityFetcher([.success(feed(days: [], stats: expected))])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()

        XCTAssertEqual(model.state, .empty(expected))
    }

    func testDaysWithNoItemsTreatedAsEmpty() async {
        let fetcher = FakeActivityFetcher([.success(feed(days: [day([])]))])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()

        guard case .empty = model.state else {
            return XCTFail("expected .empty, got \(model.state)")
        }
    }

    // MARK: - Errors

    func testLoadFailureSurfacesInlineError() async {
        let fetcher = FakeActivityFetcher([.failure(IrisAPIError.daemonUnreachable(detail: "x"))])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()

        XCTAssertEqual(model.state, .failed(message: "Can't reach Iris right now."))
    }

    func testUnauthorizedMapsToTokenMessage() async {
        let fetcher = FakeActivityFetcher([.failure(IrisAPIError.unauthorized)])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()

        XCTAssertEqual(model.state, .failed(message: "Iris rejected the connection token."))
    }

    func testRefreshFailureKeepsExistingContent() async {
        let fetcher = FakeActivityFetcher([
            .success(feed(days: [day(["a"])])),
            .failure(IrisAPIError.daemonUnreachable(detail: "blip"))
        ])
        let model = HomeViewModel(client: fetcher, greetingName: nil)

        await model.load()
        guard case .loaded = model.state else {
            return XCTFail("expected .loaded after first load")
        }

        await model.load() // second call fails
        guard case let .loaded(_, recent) = model.state else {
            return XCTFail("expected content preserved on refresh failure, got \(model.state)")
        }
        XCTAssertEqual(recent.map(\.id), ["a"])
    }
}

/// Scripted `ActivityFetching` fake. An actor so it's safely `Sendable` while
/// the view model calls it across the concurrency boundary.
private actor FakeActivityFetcher: ActivityFetching {
    enum Outcome {
        case success(ActivityFeed)
        case failure(Error)
    }

    private var outcomes: [Outcome]

    init(_ outcomes: [Outcome]) {
        self.outcomes = outcomes
    }

    func activityFeed(
        days _: Int, limit _: Int, cursor _: String?, timezone _: String
    ) async throws -> ActivityFeed {
        // Replay outcomes in order; repeat the last once exhausted.
        let outcome = outcomes.count > 1 ? outcomes.removeFirst() : (outcomes.first ?? .success(
            ActivityFeed(
                stats: ActivityStats(
                    sessionsThisWeek: 0, runsCompleted: 0, runsFailed: 0, lastActiveAt: nil
                ),
                days: [], nextCursor: nil
            )
        ))
        switch outcome {
        case let .success(feed): return feed
        case let .failure(error): throw error
        }
    }
}
