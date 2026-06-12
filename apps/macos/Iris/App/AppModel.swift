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
    private(set) var approvalNotifier: ApprovalNotifier?
    let preferences: AppPreferences
    let apiClient: APIClient
    private var observationTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "app")

    init() {
        // UI-test launches are hermetic: ephemeral defaults and a fixed
        // fake token, so tests never touch the real preferences or trigger
        // keychain ACL prompts.
        if Self.isUITestRun {
            preferences = AppPreferences(
                defaults: UserDefaults(suiteName: "iris-ui-tests-\(UUID().uuidString)")!
            )
            apiClient = APIClient(
                baseURL: preferences.gatewayBaseURL,
                token: { "ui-test-token" }
            )
        } else {
            preferences = AppPreferences()
            apiClient = APIClient(
                baseURL: preferences.gatewayBaseURL,
                token: {
                    guard let token = try? TokenStore().load() else { return nil }
                    return token
                }
            )
        }
    }

    /// Whether the onboarding window should open at launch.
    var shouldShowOnboarding: Bool {
        if ProcessInfo.processInfo.arguments.contains("--ui-test-onboarding") {
            return true
        }
        return !preferences.onboardingComplete && Self.uiTestState() == nil
    }

    func makeOnboardingModel() -> OnboardingModel {
        let permissions: PermissionChecking
        if let scripted = Self.argumentValue(after: "--ui-test-permissions") {
            let initial: PermissionState = switch scripted {
            case "granted": .granted
            case "denied": .denied
            default: .undetermined
            }
            permissions = ScriptedPermissionChecker(initial: initial)
        } else {
            permissions = SystemPermissionChecker()
        }
        let startStep: OnboardingModel.Step =
            switch Self.argumentValue(after: "--ui-test-onboarding-step") {
            case "openAIKey": .openAIKey
            case "microphone": .microphone
            case "systemAccess": .systemAccess
            case "done": .done
            default: .welcome
            }
        return OnboardingModel(
            client: apiClient,
            permissions: permissions,
            preferences: preferences,
            startStep: startStep
        )
    }

    private static func argumentValue(after flag: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: flag),
              arguments.indices.contains(index + 1) else { return nil }
        return arguments[index + 1]
    }

    private static var isUITestRun: Bool {
        let arguments = ProcessInfo.processInfo.arguments
        return arguments.contains("--ui-test-onboarding")
            || arguments.contains("--ui-test-state")
    }

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
        let notifier = ApprovalNotifier(
            source: apiClient,
            presenter: SystemNotificationPresenter()
        )
        approvalNotifier = notifier
        AppDelegate.approvalResponseHandler = { action, approvalId in
            await notifier.handleResponse(
                actionIdentifier: action, approvalId: approvalId
            )
        }
        observationTask = Task { [weak self] in
            for await state in await manager.states() {
                self?.daemonState = state
                if case .healthy = state {
                    await notifier.daemonIsHealthy(true)
                } else {
                    await notifier.daemonIsHealthy(false)
                }
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
