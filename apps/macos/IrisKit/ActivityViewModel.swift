import Foundation
import Observation

/// Feed + detail access for the Activity section; `APIClient` conforms. A
/// protocol so the view model is unit-testable with a scripted fake.
public protocol ActivityServing: Sendable {
    func activityFeed(
        days: Int, limit: Int, cursor: String?, timezone: String
    ) async throws -> ActivityFeed
    func activityDetail(id: String) async throws -> ActivityDetail
}

extension APIClient: ActivityServing {}

/// Drives the Activity section: a day-grouped, paged list of everything Iris
/// has done (`GET /activity`) plus a detail pane (`GET /activity/{id}`) for the
/// selected item. The list phase, pagination, and detail load are tracked
/// independently so a page fetch or a detail load never blows away content
/// already on screen.
@MainActor
@Observable
public final class ActivityViewModel {
    /// The list column's top-level state.
    public enum Phase: Equatable, Sendable {
        case loading
        /// Loaded, but there's no history yet.
        case empty
        /// At least one day of items is loaded; `days` holds them.
        case content
        case failed(message: String)
    }

    /// The detail column's state for the current selection.
    public enum DetailState: Equatable, Sendable {
        case none
        case loading
        case loaded(ActivityDetail)
        case failed(message: String)
    }

    /// Items requested per page.
    public static let pageSize = 50
    /// History window each page spans, in days.
    public static let windowDays = 30

    public private(set) var phase: Phase = .loading
    public private(set) var days: [ActivityDay] = []
    public private(set) var isLoadingMore = false
    /// True once the server stops handing back a cursor — the end of history.
    public private(set) var reachedEnd = false
    /// A failed "load more" surfaces here without dropping loaded content, so
    /// the list can offer a retry footer instead of an error screen.
    public private(set) var loadMoreError: String?

    /// The selected item's id, or nil when nothing is selected. Assigning it
    /// kicks off a detail load (and clearing it resets the pane).
    public var selection: String? {
        didSet {
            guard selection != oldValue else { return }
            loadDetailTask?.cancel()
            if let selection {
                loadDetailTask = Task { await loadDetail(id: selection) }
            } else {
                detail = .none
            }
        }
    }

    public private(set) var detail: DetailState = .none

    private let client: ActivityServing
    private let timeZoneIdentifier: String
    private var cursor: String?
    /// First load shows the skeleton; later refreshes keep content on screen.
    private var hasLoaded = false
    private var loadDetailTask: Task<Void, Never>?

    public init(
        client: ActivityServing,
        timeZoneIdentifier: String = TimeZone.current.identifier
    ) {
        self.client = client
        self.timeZoneIdentifier = timeZoneIdentifier
    }

    /// Initial (or refresh) load of the first page.
    public func load() async {
        if !hasLoaded { phase = .loading }
        loadMoreError = nil
        do {
            let feed = try await fetch(cursor: nil)
            days = Self.nonEmpty(feed.days)
            cursor = feed.nextCursor
            reachedEnd = feed.nextCursor == nil
            phase = days.isEmpty ? .empty : .content
            hasLoaded = true
        } catch {
            // Don't clobber good content on a refresh blip; only show the error
            // screen if we've never managed a successful load.
            if !hasLoaded {
                phase = .failed(message: Self.message(for: error))
            }
        }
    }

    /// Fetch and append the next page. No-op unless there's more to load.
    public func loadMore() async {
        guard phase == .content, !isLoadingMore, !reachedEnd, let cursor else { return }
        isLoadingMore = true
        loadMoreError = nil
        defer { isLoadingMore = false }
        do {
            let feed = try await fetch(cursor: cursor)
            days = Self.merge(days, with: Self.nonEmpty(feed.days))
            self.cursor = feed.nextCursor
            reachedEnd = feed.nextCursor == nil
        } catch {
            loadMoreError = Self.message(for: error)
        }
    }

    /// Re-run the detail load for the current selection (the detail Retry).
    public func retryDetail() {
        guard let selection else { return }
        loadDetailTask?.cancel()
        loadDetailTask = Task { await loadDetail(id: selection) }
    }

    private func loadDetail(id: String) async {
        detail = .loading
        do {
            let loaded = try await client.activityDetail(id: id)
            guard !Task.isCancelled, selection == id else { return }
            detail = .loaded(loaded)
        } catch {
            guard !Task.isCancelled, selection == id else { return }
            detail = .failed(message: Self.message(for: error))
        }
    }

    private func fetch(cursor: String?) async throws -> ActivityFeed {
        try await client.activityFeed(
            days: Self.windowDays, limit: Self.pageSize,
            cursor: cursor, timezone: timeZoneIdentifier
        )
    }

    /// Drop days the server returned with no items (defensive — keeps empty
    /// section headers off the list).
    static func nonEmpty(_ days: [ActivityDay]) -> [ActivityDay] {
        days.filter { !$0.items.isEmpty }
    }

    /// Merge a freshly fetched page into the existing list, coalescing a day
    /// that straddles the page boundary so it never renders a second header.
    static func merge(_ existing: [ActivityDay], with new: [ActivityDay]) -> [ActivityDay] {
        guard let last = existing.last, let first = new.first, last.date == first.date else {
            return existing + new
        }
        var merged = existing
        merged[merged.count - 1] = ActivityDay(
            date: last.date, label: last.label, items: last.items + first.items
        )
        return merged + new.dropFirst()
    }

    /// Concise, user-facing copy for the inline error states.
    static func message(for error: Error) -> String {
        switch error {
        case IrisAPIError.daemonUnreachable:
            "Can't reach Iris right now."
        case IrisAPIError.unauthorized:
            "Iris rejected the connection token."
        case IrisAPIError.notFound:
            "This item is no longer available."
        default:
            (error as? LocalizedError)?.errorDescription ?? "Something went wrong."
        }
    }
}
