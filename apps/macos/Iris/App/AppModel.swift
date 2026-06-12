import AppKit
import IrisKit
import Observation
import os

/// Owns the daemon lifecycle and mirrors its state for SwiftUI.
@MainActor
@Observable
final class AppModel {
    private(set) var daemonState: DaemonState = .stopped

    private(set) var daemonManager: DaemonManager?
    private var observationTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "app")

    /// Call once at app launch.
    func bootstrap() {
        if let pinned = Self.uiTestState() {
            daemonState = pinned
            return
        }
        guard let configuration = Self.daemonConfiguration() else {
            // No runtime found: dev builds need the repo checkout (or
            // IRIS_REPO_ROOT); the bundled runtime arrives in Step 7.1.
            logger.error("no daemon runtime found")
            daemonState = .crashLooping(
                stderrTail: "No Iris runtime found. Set IRIS_REPO_ROOT to the repo checkout."
            )
            return
        }
        let manager = DaemonManager(
            configuration: configuration,
            tokenProvider: { try TokenStore().loadOrCreate() }
        )
        daemonManager = manager
        observationTask = Task { [weak self] in
            for await state in await manager.states() {
                self?.daemonState = state
            }
        }
        Task { await manager.start() }
    }

    func restartDaemon() {
        guard let daemonManager else { return }
        Task {
            await daemonManager.stop()
            await daemonManager.start()
        }
    }

    /// Graceful shutdown for every quit path (menu item, Cmd-Q, logout):
    /// SIGTERM via DaemonManager.stop with bounded grace, then SIGKILL.
    func shutdownForQuit() async {
        observationTask?.cancel()
        await daemonManager?.stop()
    }

    // MARK: - Runtime resolution

    private static func daemonConfiguration() -> DaemonConfiguration? {
        guard let repoRoot = devRepoRoot() else { return nil }
        let logDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Logs/Iris")
        return .development(
            repoRoot: repoRoot,
            port: AppPreferences().gatewayPort,
            logDirectory: logDirectory
        )
    }

    /// Dev builds resolve the repo from IRIS_REPO_ROOT or from this source
    /// file's compile-time location (apps/macos/Iris/App/AppModel.swift).
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

    private static func uiTestState() -> DaemonState? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flagIndex = arguments.firstIndex(of: "--ui-test-state"),
              arguments.indices.contains(flagIndex + 1) else { return nil }
        return pinnableStates[arguments[flagIndex + 1]]
    }
}
