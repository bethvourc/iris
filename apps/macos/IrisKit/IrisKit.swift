import Foundation

/// IrisKit is the app's only boundary to the Iris daemon: typed models,
/// APIClient, SSEClient, TokenStore, and DaemonManager land here in
/// Steps 2.2–2.5 (see docs/desktop/architecture.md §2).
public enum IrisKitInfo {
    /// The gateway contract version this client is written against.
    /// Must match `CONTRACT_VERSION` in `src/iris/gateway.py`; the client
    /// refuses to operate against a higher major version.
    public static let contractVersion = 1
}
