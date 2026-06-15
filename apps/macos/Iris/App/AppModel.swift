import AppKit
import IrisKit
import Observation
import os

/// Owns the daemon lifecycle and mirrors its state for SwiftUI.
@MainActor
@Observable
final class AppModel {
    private(set) var daemonState: DaemonState = .stopped
    /// Pending approvals awaiting a decision — drives the sidebar and menu-bar
    /// badges. Fed by the always-running `ApprovalNotifier` poll.
    private(set) var pendingApprovalCount = 0

    private(set) var daemonManager: DaemonManager?
    private(set) var approvalNotifier: ApprovalNotifier?
    private(set) var hotkey: HotkeyController?
    let preferences: AppPreferences
    let apiClient: APIClient
    let voiceModel: VoiceSessionViewModel
    /// Bridge so non-View code (recovery actions) can open SwiftUI windows;
    /// set once from the menu bar label's view context.
    @ObservationIgnored var openWindowAction: ((String) -> Void)?
    private var observationTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "app")
    @ObservationIgnored
    private lazy var overlay: OverlayController = {
        let model = voiceModel
        return OverlayController(
            onDismiss: { [weak self] in
                self?.voiceModel.end()
                self?.hotkey?.cancel()
            },
            rootView: { OverlayRootView(model: model) }
        )
    }()

    /// `--ui-test-overlay` shows the overlay at launch for screenshots/tests.
    var shouldShowOverlayAtLaunch: Bool {
        ProcessInfo.processInfo.arguments.contains("--ui-test-overlay")
    }

    /// At launch, pin a forced overlay state for screenshots/UI tests so no
    /// daemon is needed; otherwise begin a real session.
    func showOverlay() {
        if let forced = Self.forcedOverlayState() {
            voiceModel.forceState(
                forced.state, userText: forced.userText,
                assistantText: forced.assistantText, statusLine: forced.statusLine
            )
        } else {
            voiceModel.begin()
        }
        overlay.show()
    }

    func hideOverlay() {
        voiceModel.end()
        overlay.hide()
    }

    init() {
        // UI-test launches are hermetic: ephemeral defaults and a fixed
        // fake token, so tests never touch the real preferences or trigger
        // keychain ACL prompts.
        let tokenProvider: @Sendable () -> String?
        if Self.isUITestRun {
            preferences = AppPreferences(
                defaults: UserDefaults(suiteName: "iris-ui-tests-\(UUID().uuidString)")!
            )
            tokenProvider = { "ui-test-token" }
        } else {
            preferences = AppPreferences()
            tokenProvider = {
                guard let token = try? TokenStore().load() else { return nil }
                return token
            }
        }
        let baseURL = preferences.gatewayBaseURL
        apiClient = APIClient(baseURL: baseURL, token: tokenProvider)
        let eventsURL = baseURL.appending(path: "voice/events")
        let sse = SSEClient(url: eventsURL, token: tokenProvider)
        // Hermetic UI-test runs assume mic access; real runs check TCC.
        let micCheck: @Sendable () -> Bool = if Self.isUITestRun {
            { true }
        } else {
            { SystemPermissionChecker.microphoneIsAuthorized() }
        }
        voiceModel = VoiceSessionViewModel(
            voiceControl: apiClient,
            micAuthorized: micCheck,
            eventStream: { sse.events() }
        )
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

    /// Build the Home screen's view model. `--ui-test-home <scenario>`
    /// (loading/empty/populated/error) swaps in a scripted fetcher so each
    /// state is screenshottable without a daemon.
    func makeHomeModel() -> HomeViewModel {
        if let scenario = Self.argumentValue(after: "--ui-test-home") {
            return HomeViewModel(
                client: ScriptedActivityFetcher(scenario: scenario),
                greetingName: "Bethvour"
            )
        }
        return HomeViewModel(client: apiClient)
    }

    /// Build the Activity section's view model. `--ui-test-activity <scenario>`
    /// (loading/empty/populated/error) swaps in a scripted service so the list,
    /// pagination, and a populated detail are screenshottable without a daemon.
    func makeActivityModel() -> ActivityViewModel {
        guard let scenario = Self.argumentValue(after: "--ui-test-activity") else {
            return ActivityViewModel(client: apiClient)
        }
        let model = ActivityViewModel(client: ScriptedActivityService(scenario: scenario))
        // The detail pane is a collapsible inspector that's closed by default;
        // the "detail" scenario preselects an item so the open pane is
        // screenshottable.
        if scenario == "detail" {
            model.selection = "1"
        }
        return model
    }

    /// Build the Approvals section's view model. `--ui-test-approvals <scenario>`
    /// (pending/empty/error) swaps in a scripted source so each state — and the
    /// badge — is screenshottable without a daemon.
    func makeApprovalsModel() -> ApprovalsViewModel {
        guard let scenario = Self.argumentValue(after: "--ui-test-approvals") else {
            return ApprovalsViewModel(source: apiClient)
        }
        if scenario != "empty", scenario != "error" {
            pendingApprovalCount = ScriptedApprovalSource.pendingCount
        }
        let model = ApprovalsViewModel(source: ScriptedApprovalSource(scenario: scenario))
        if scenario == "confirm" {
            // Arm the destructive confirm step once the list has loaded, so the
            // confirm UI is screenshottable.
            Task { @MainActor in
                for _ in 0 ..< 60 {
                    if let blocked = model.pending.first(where: ApprovalsViewModel.isDestructive) {
                        await model.approve(blocked)
                        return
                    }
                    try? await Task.sleep(for: .milliseconds(50))
                }
            }
        }
        return model
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
            presenter: SystemNotificationPresenter(),
            onPendingCount: { [weak self] count in
                Task { @MainActor in self?.pendingApprovalCount = count }
            }
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
        hotkey = HotkeyController(
            onActivate: { [weak self] in self?.voiceActivationRequested() },
            onDeactivate: { [weak self] in self?.voiceDeactivationRequested() }
        )
        // Session truth from the SSE stream reconciles the activation toggle;
        // a session ending on its own dismisses the overlay.
        voiceModel.onSessionActiveChanged = { [weak self] active in
            self?.hotkey?.sessionStateChanged(isActive: active)
        }
        voiceModel.onShouldDismiss = { [weak self] in
            self?.overlay.hide()
            self?.hotkey?.cancel()
        }
        voiceModel.onActivationFailed = { [weak self] in
            self?.hotkey?.activationFailed()
        }
        voiceModel.onRequestDaemonRestart = { [weak self] in self?.restartDaemon() }
        voiceModel.onOpenDiagnostics = { [weak self] in self?.openDiagnostics() }
        voiceModel.onOpenMicrophoneSettings = {
            SystemPermissionChecker().openSystemSettings(.microphone)
        }
        Task { await manager.start() }
    }

    private func voiceActivationRequested() {
        logger.info("voice activation: starting session")
        overlay.show()
        // A terminal daemon state can't be fixed by a start attempt; each
        // maps to its own recovery (crash-loop → diagnostics, port/token →
        // re-probe). Otherwise begin a real session.
        if let error = VoiceSessionViewModel.OverlayError.forDaemonState(daemonState) {
            voiceModel.presentError(error)
        } else {
            voiceModel.begin()
        }
    }

    private func voiceDeactivationRequested() {
        logger.info("voice deactivation: ending session")
        voiceModel.end()
        overlay.hide()
    }

    private func openDiagnostics() {
        // Real Advanced/diagnostics pane lands in Step 5.5; for now, surface
        // the main window.
        openWindowAction?("main")
        NSApp.activate(ignoringOtherApps: true)
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

    /// `--ui-test-overlay-state <name>` pins a rendered overlay state with
    /// sample content, so each state is screenshottable without a daemon.
    private struct ForcedOverlay {
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

    private static func forcedOverlayState() -> ForcedOverlay? {
        guard let name = argumentValue(after: "--ui-test-overlay-state") else { return nil }
        return forcedOverlayStates[name]
    }
}
