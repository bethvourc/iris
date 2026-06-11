import Foundation

/// Typed wrapper over UserDefaults for non-secret app preferences.
/// Secrets never belong here — the gateway token lives in TokenStore.
///
/// `@unchecked Sendable`: UserDefaults is documented thread-safe.
public final class AppPreferences: @unchecked Sendable {
    public static let defaultGatewayPort = 8765

    private enum Key {
        static let gatewayPort = "gatewayPort"
        static let onboardingComplete = "onboardingComplete"
    }

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// Loopback port the daemon is supervised on. The token and port are a
    /// pair: changing the port goes through Settings → Advanced.
    public var gatewayPort: Int {
        get {
            let value = defaults.integer(forKey: Key.gatewayPort)
            return value > 0 ? value : Self.defaultGatewayPort
        }
        set { defaults.set(newValue, forKey: Key.gatewayPort) }
    }

    public var onboardingComplete: Bool {
        get { defaults.bool(forKey: Key.onboardingComplete) }
        set { defaults.set(newValue, forKey: Key.onboardingComplete) }
    }

    /// Loopback only, by architecture (docs/desktop/architecture.md §4).
    public var gatewayBaseURL: URL {
        URL(string: "http://127.0.0.1:\(gatewayPort)")!
    }
}
