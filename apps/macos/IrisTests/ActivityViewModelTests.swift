import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class ActivityViewModelTests: XCTestCase {
    // MARK: - Fixtures

    private func stats() -> ActivityStats {
        ActivityStats(sessionsThisWeek: 1, runsCompleted: 2, runsFailed: 0, lastActiveAt: .now)
    }

    private func item(_ id: String) -> ActivityItem {
        ActivityItem(id: id, kind: .run, time: .now, title: "Item \(id)", preview: nil, status: .done)
    }

    private func day(_ date: String, _ ids: [String], label: String? = nil) -> ActivityDay {
        ActivityDay(date: date, label: label, items: ids.map(item))
    }

    private func feed(_ days: [ActivityDay], next: String? = nil) -> ActivityFeed {
        ActivityFeed(stats: stats(), days: days, nextCursor: next)
    }

    private func detail(_ id: String) -> ActivityDetail {
        ActivityDetail(
            id: id, kind: .voiceSession, time: .now, status: .done,
            title: "Detail \(id)", transcript: nil, run: nil, summary: "Summary \(id)"
        )
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

    // MARK: - Initial load

    func testStartsInLoadingState() {
        let model = ActivityViewModel(client: FakeActivityService())
        XCTAssertEqual(model.phase, .loading)
    }

    func testPopulatedLoad() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a", "b"], label: "Today")], next: "p2"))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()

        XCTAssertEqual(model.phase, .content)
        XCTAssertEqual(model.days.map(\.date), ["2026-06-14"])
        XCTAssertEqual(model.days.first?.items.map(\.id), ["a", "b"])
        XCTAssertFalse(model.reachedEnd)
    }

    func testEmptyLoad() async {
        let model = ActivityViewModel(client: FakeActivityService(feeds: [.success(feed([]))]))

        await model.load()

        XCTAssertEqual(model.phase, .empty)
        XCTAssertTrue(model.reachedEnd)
    }

    func testDaysWithNoItemsAreFilteredOut() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", []), day("2026-06-13", ["a"])]))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()

        XCTAssertEqual(model.days.map(\.date), ["2026-06-13"])
    }

    func testNoCursorMeansEndOfHistory() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a"])], next: nil))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()

        XCTAssertTrue(model.reachedEnd)
    }

    // MARK: - Pagination

    func testLoadMoreAppendsNextPage() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a"])], next: "p2")),
            .success(feed([day("2026-06-12", ["b"])], next: nil))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()
        await model.loadMore()

        XCTAssertEqual(model.days.map(\.date), ["2026-06-14", "2026-06-12"])
        XCTAssertEqual(model.days.flatMap(\.items).map(\.id), ["a", "b"])
        XCTAssertTrue(model.reachedEnd)
    }

    func testLoadMoreMergesDayAcrossPageBoundary() async {
        // The same calendar day straddles the page boundary: its items must
        // coalesce under one header, not produce a duplicate day section.
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a", "b"], label: "Today")], next: "p2")),
            .success(feed([
                day("2026-06-14", ["c"], label: "Today"),
                day("2026-06-13", ["d"], label: "Yesterday")
            ], next: nil))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()
        await model.loadMore()

        XCTAssertEqual(model.days.map(\.date), ["2026-06-14", "2026-06-13"])
        XCTAssertEqual(model.days[0].items.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(model.days[1].items.map(\.id), ["d"])
    }

    func testLoadMoreIsNoOpAtEndOfHistory() async {
        let service = FakeActivityService(feeds: [.success(feed([day("2026-06-14", ["a"])], next: nil))])
        let model = ActivityViewModel(client: service)

        await model.load()
        let callsAfterLoad = await service.feedCallCount
        await model.loadMore()

        let callsAfterLoadMore = await service.feedCallCount
        XCTAssertEqual(callsAfterLoadMore, callsAfterLoad)
    }

    func testLoadMoreFailureKeepsContentAndSurfacesFooterError() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a"])], next: "p2")),
            .failure(IrisAPIError.daemonUnreachable(detail: "blip"))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()
        await model.loadMore()

        XCTAssertEqual(model.phase, .content)
        XCTAssertEqual(model.days.flatMap(\.items).map(\.id), ["a"])
        XCTAssertEqual(model.loadMoreError, "Can't reach Iris right now.")
        XCTAssertFalse(model.reachedEnd)
    }

    // MARK: - Errors

    func testLoadFailureSurfacesErrorScreen() async {
        let service = FakeActivityService(feeds: [.failure(IrisAPIError.daemonUnreachable(detail: "x"))])
        let model = ActivityViewModel(client: service)

        await model.load()

        XCTAssertEqual(model.phase, .failed(message: "Can't reach Iris right now."))
    }

    func testRefreshFailureKeepsExistingContent() async {
        let service = FakeActivityService(feeds: [
            .success(feed([day("2026-06-14", ["a"])])),
            .failure(IrisAPIError.daemonUnreachable(detail: "blip"))
        ])
        let model = ActivityViewModel(client: service)

        await model.load()
        await model.load()

        XCTAssertEqual(model.phase, .content)
        XCTAssertEqual(model.days.flatMap(\.items).map(\.id), ["a"])
    }

    // MARK: - Timezone

    func testTimeZoneIsForwardedToTheClient() async {
        let service = FakeActivityService(feeds: [.success(feed([day("2026-06-14", ["a"])]))])
        let model = ActivityViewModel(client: service, timeZoneIdentifier: "America/New_York")

        await model.load()

        let timeZones = await service.requestedTimeZones
        XCTAssertEqual(timeZones, ["America/New_York"])
    }

    // MARK: - Detail

    func testSelectingItemLoadsDetail() async {
        let service = FakeActivityService(
            feeds: [.success(feed([day("2026-06-14", ["a"])]))],
            detail: .success(detail("a"))
        )
        let model = ActivityViewModel(client: service)
        await model.load()

        model.selection = "a"
        await waitUntil { if case .loaded = model.detail { true } else { false } }

        guard case let .loaded(loaded) = model.detail else {
            return XCTFail("expected detail .loaded, got \(model.detail)")
        }
        XCTAssertEqual(loaded.id, "a")
    }

    func testClearingSelectionResetsDetail() async {
        let service = FakeActivityService(
            feeds: [.success(feed([day("2026-06-14", ["a"])]))],
            detail: .success(detail("a"))
        )
        let model = ActivityViewModel(client: service)
        await model.load()
        model.selection = "a"
        await waitUntil { if case .loaded = model.detail { true } else { false } }

        model.selection = nil

        XCTAssertEqual(model.detail, .none)
    }

    func testDetailNotFoundMapsToFriendlyMessage() async {
        let service = FakeActivityService(
            feeds: [.success(feed([day("2026-06-14", ["a"])]))],
            detail: .failure(IrisAPIError.notFound(message: "gone"))
        )
        let model = ActivityViewModel(client: service)
        await model.load()

        model.selection = "a"
        await waitUntil { if case .failed = model.detail { true } else { false } }

        XCTAssertEqual(model.detail, .failed(message: "This item is no longer available."))
    }

    // MARK: - merge() unit

    func testMergeKeepsDistinctDaysSeparate() {
        let merged = ActivityViewModel.merge(
            [day("2026-06-14", ["a"])], with: [day("2026-06-13", ["b"])]
        )
        XCTAssertEqual(merged.map(\.date), ["2026-06-14", "2026-06-13"])
    }
}

/// Scripted `ActivityServing` fake. An actor so it's safely `Sendable` across
/// the concurrency boundary; it records the cursors and timezones it was asked
/// for so pagination and tz handling can be asserted.
private actor FakeActivityService: ActivityServing {
    enum FeedOutcome {
        case success(ActivityFeed)
        case failure(Error)
    }

    private var feeds: [FeedOutcome]
    private let detailResult: Result<ActivityDetail, Error>?
    private(set) var requestedCursors: [String?] = []
    private(set) var requestedTimeZones: [String] = []
    private(set) var feedCallCount = 0

    init(feeds: [FeedOutcome] = [], detail: Result<ActivityDetail, Error>? = nil) {
        self.feeds = feeds
        detailResult = detail
    }

    func activityFeed(
        days _: Int, limit _: Int, cursor: String?, timezone: String
    ) async throws -> ActivityFeed {
        feedCallCount += 1
        requestedCursors.append(cursor)
        requestedTimeZones.append(timezone)
        let outcome = feeds.count > 1 ? feeds.removeFirst() : (feeds.first ?? .success(
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

    func activityDetail(id: String) async throws -> ActivityDetail {
        switch detailResult {
        case let .success(detail): return detail
        case let .failure(error): throw error
        case nil:
            throw IrisAPIError.notFound(message: "no scripted detail for \(id)")
        }
    }
}
