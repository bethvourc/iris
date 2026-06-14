import Foundation
import Observation

/// The activity data the Home screen needs; `APIClient` conforms. A protocol
/// so the view model is unit-testable with a scripted fake.
public protocol ActivityFetching: Sendable {
    func activityFeed(
        days: Int, limit: Int, cursor: String?, timezone: String
    ) async throws -> ActivityFeed
}

extension APIClient: ActivityFetching {}

/// Drives the Home screen: loads `GET /activity`, then exposes a greeting, the
/// stats block, and a short recent-activity preview. Models every screen state
/// the view must render — loading, empty (first run), populated, and error —
/// so the view is a pure function of `state`.
@MainActor
@Observable
public final class HomeViewModel {
    public enum State: Equatable, Sendable {
        case loading
        /// Loaded, but Iris hasn't done anything yet — the first-run screen.
        case empty(ActivityStats)
        case loaded(stats: ActivityStats, recent: [ActivityItem])
        case failed(message: String)
    }

    /// How many recent items the Home preview shows before "See all".
    public static let recentLimit = 5

    public private(set) var state: State = .loading

    private let client: ActivityFetching
    private let greetingName: String?
    /// First load shows the skeleton; later refreshes keep the current content
    /// on screen (and survive a transient failure) instead of flashing.
    private var hasLoaded = false

    public init(
        client: ActivityFetching,
        greetingName: String? = HomeViewModel.systemGreetingName()
    ) {
        self.client = client
        self.greetingName = greetingName
    }

    /// "Welcome back, <first name>", or a plain greeting when no name is known.
    public var greeting: String {
        if let name = greetingName, !name.isEmpty {
            "Welcome back, \(name)"
        } else {
            "Welcome back"
        }
    }

    /// The signed-in macOS account's first name, or nil if unavailable.
    public nonisolated static func systemGreetingName() -> String? {
        let full = NSFullUserName().trimmingCharacters(in: .whitespaces)
        guard !full.isEmpty else { return nil }
        let first = full.split(separator: " ").first.map(String.init)
        return (first?.isEmpty == false) ? first : nil
    }

    public func load() async {
        if !hasLoaded { state = .loading }
        do {
            let feed = try await client.activityFeed(
                days: 7,
                limit: 50,
                cursor: nil,
                timezone: TimeZone.current.identifier
            )
            let recent = Array(feed.days.flatMap(\.items).prefix(Self.recentLimit))
            state = recent.isEmpty
                ? .empty(feed.stats)
                : .loaded(stats: feed.stats, recent: recent)
            hasLoaded = true
        } catch {
            // Don't clobber good content on a refresh blip; only surface the
            // error screen if we've never managed a successful load.
            if !hasLoaded {
                state = .failed(message: Self.message(for: error))
            }
        }
    }

    /// Concise, user-facing copy for the inline error state.
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
