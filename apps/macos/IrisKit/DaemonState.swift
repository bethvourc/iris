import Foundation

/// Daemon supervision state machine (docs/desktop/architecture.md §3).
/// `crashLooping`, `portConflict`, and `tokenMismatch` are terminal: the
/// manager stops retrying and the UI must surface a recovery action.
public enum DaemonState: Equatable, Sendable {
    case stopped
    case launching
    /// `adopted: true` means a daemon someone else started (e.g. a dev
    /// terminal) was found healthy; we supervise but never kill it.
    case healthy(adopted: Bool)
    /// Process alive but health checks failing (e.g. hung).
    case unhealthy
    case restarting(attempt: Int)
    /// ≥ crashLoopThreshold crashes inside crashLoopWindow.
    case crashLooping(stderrTail: String)
    /// Something that isn't a compatible Iris daemon owns the port (F3).
    case portConflict(reason: String)
    /// The daemon is healthy but rejects our token (F4) — rotation flow.
    case tokenMismatch

    public var isTerminalFailure: Bool {
        switch self {
        case .crashLooping, .portConflict, .tokenMismatch:
            true
        default:
            false
        }
    }
}

/// Result of probing the gateway port.
public enum ProbeOutcome: Equatable, Sendable {
    case healthy
    case unreachable
    case tokenRejected
    case conflict(reason: String)
}

/// Default probe: `/health` proves it's a compatible Iris daemon, then an
/// authenticated `/voice/status` call proves the token is accepted.
public struct GatewayHealthProbe: Sendable {
    private let session: URLSession

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3
        session = URLSession(configuration: configuration)
    }

    public func probe(baseURL: URL, token: String?) async -> ProbeOutcome {
        let client = APIClient(baseURL: baseURL, token: { token }, session: session)
        do {
            let health = try await client.health()
            guard health.contractVersion <= IrisKitInfo.contractVersion else {
                return .conflict(
                    reason: "daemon speaks contract v\(health.contractVersion); "
                        + "this app only knows v\(IrisKitInfo.contractVersion)"
                )
            }
        } catch IrisAPIError.daemonUnreachable {
            return .unreachable
        } catch {
            return .conflict(reason: "port is owned by something that isn't Iris")
        }
        do {
            _ = try await client.voiceStatus()
            return .healthy
        } catch IrisAPIError.unauthorized {
            return .tokenRejected
        } catch {
            return .unreachable
        }
    }
}
