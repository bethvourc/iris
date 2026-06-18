import IrisKit
import SwiftUI

/// Launch-time resolution and deterministic UI-test overrides, split out of
/// `AppModel` to keep the core type focused. Daemon run-mode resolution
/// (dev checkout vs. embedded runtime) lives here alongside the `--ui-test-*`
/// hooks that pin daemon and overlay states for screenshots and XCUITests.
extension AppModel {
    // MARK: - Runtime resolution

    static func daemonConfiguration() -> DaemonConfiguration? {
        let port = AppPreferences().gatewayPort
        let logDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Logs/Iris")
        // Dev checkout wins: run the daemon from the repo via uv so source edits
        // take effect without repackaging.
        if let repoRoot = devRepoRoot() {
            return .development(repoRoot: repoRoot, port: port, logDirectory: logDirectory)
        }
        // Shipped app: run the embedded Python runtime from the bundle.
        let resources = Bundle.main.resourceURL
        if let resources, DaemonConfiguration.bundledRuntimeExists(resourcesURL: resources) {
            return .bundled(resourcesURL: resources, port: port, logDirectory: logDirectory)
        }
        return nil
    }

    /// Dev builds resolve the repo from IRIS_REPO_ROOT or from this source
    /// file's compile-time location (apps/macos/Iris/App/AppModel+Launch.swift).
    private static func devRepoRoot() -> URL? {
        if let override = ProcessInfo.processInfo.environment["IRIS_REPO_ROOT"] {
            return URL(fileURLWithPath: override)
        }
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0 ..< 5 {
            url.deleteLastPathComponent()
        }
        let marker = url.appending(path: "pyproject.toml")
        return FileManager.default.fileExists(atPath: marker.path) ? url : nil
    }

    // MARK: - UI-test support

    /// `--ui-test-state <name>` pins a daemon state and skips real
    /// supervision, so UI tests and screenshots are deterministic.
    private static let pinnableStates: [String: DaemonState] = [
        "healthy": .healthy(adopted: false),
        "adopted": .healthy(adopted: true),
        "launching": .launching,
        "restarting": .restarting(attempt: 2),
        "unhealthy": .unhealthy,
        "crashLooping": .crashLooping(stderrTail: "stub stderr tail"),
        "portConflict": .portConflict(reason: "port 8765 is owned by something else"),
        "tokenMismatch": .tokenMismatch,
        "stopped": .stopped
    ]

    static func uiTestState() -> DaemonState? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flagIndex = arguments.firstIndex(of: "--ui-test-state"),
              arguments.indices.contains(flagIndex + 1) else { return nil }
        return pinnableStates[arguments[flagIndex + 1]]
    }

    /// `--ui-test-overlay-state <name>` pins a rendered overlay state with
    /// sample content, so each state is screenshottable without a daemon.
    struct ForcedOverlay {
        let state: VoiceSessionViewModel.DisplayState
        var userText = ""
        var assistantText = ""
        var statusLine: String?
    }

    private static let forcedOverlayStates: [String: ForcedOverlay] = [
        "connecting": ForcedOverlay(state: .connecting),
        "listening": ForcedOverlay(state: .listening, userText: "What's on my calendar today?"),
        "thinking": ForcedOverlay(state: .thinking, statusLine: "Checking your calendar…"),
        "speaking": ForcedOverlay(
            state: .speaking,
            userText: "What's on my calendar today?",
            assistantText: "You have three things: a 10 a.m. design review, "
                + "lunch with Sam at noon, and a 3 p.m. one-on-one."
        ),
        "ended": ForcedOverlay(state: .ended(summary: "Set a reminder for 3 p.m.")),
        "error-daemon": ForcedOverlay(state: .error(.daemonNotRunning)),
        "error-crash": ForcedOverlay(state: .error(.daemonFailing)),
        "error-port": ForcedOverlay(state: .error(.portConflict)),
        "error-token": ForcedOverlay(state: .error(.tokenMismatch)),
        "error-mic": ForcedOverlay(state: .error(.microphoneOff)),
        "error": ForcedOverlay(state: .error(.generic(message: "Couldn't reach OpenAI.")))
    ]

    static func forcedOverlayState() -> ForcedOverlay? {
        guard let name = argumentValue(after: "--ui-test-overlay-state") else { return nil }
        return forcedOverlayStates[name]
    }
}
