import Foundation

/// Every failure the UI can encounter at the daemon boundary, normalized.
/// The taxonomy is product design: each case has a distinct recovery UX
/// (docs/desktop/architecture.md §8).
public enum IrisAPIError: Error, Equatable, Sendable {
    /// Token missing/invalid — the token-mismatch recovery flow (F4),
    /// never a retry.
    case unauthorized
    /// Could not reach the daemon at all (F1) — offer restart.
    case daemonUnreachable(detail: String)
    /// 409 state conflict. For `voice_already_running` the live session
    /// descriptor is attached so the caller can adopt it instead of failing.
    case conflict(code: String, message: String, session: VoiceSessionDescriptor?)
    /// 400 — the client sent something the contract forbids.
    case invalidRequest(code: String, message: String)
    case notFound(message: String)
    /// 5xx from the daemon, e.g. `voice_unavailable`.
    case server(code: String, message: String, status: Int)
    /// The response did not match the contract — a contract drift bug.
    case decoding(detail: String)
}

extension IrisAPIError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .unauthorized:
            "Iris rejected the connection token."
        case let .daemonUnreachable(detail):
            "Iris isn't running (\(detail))."
        case let .conflict(_, message, _),
             let .invalidRequest(_, message),
             let .notFound(message),
             let .server(_, message, _):
            message
        case let .decoding(detail):
            "Unexpected response from Iris (\(detail))."
        }
    }
}
