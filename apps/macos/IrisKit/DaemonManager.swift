import Foundation
import os

/// Spawns and supervises the Python daemon (docs/desktop/architecture.md §3).
///
/// Responsibilities: resolve + launch the daemon (see `DaemonConfiguration`)
/// with the Keychain token in its environment, poll `/health` until ready,
/// restart with jittered backoff on unexpected exits, declare crash-loop after
/// repeated failures, detect port conflicts and token mismatches, and adopt
/// (never kill) a daemon someone else started.
public actor DaemonManager {
    public typealias HealthProbe = @Sendable (URL, String?) async -> ProbeOutcome
    public typealias Configuration = DaemonConfiguration

    private let configuration: Configuration
    private let tokenProvider: @Sendable () throws -> String
    private let probe: HealthProbe
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "daemon")

    public private(set) var state: DaemonState = .stopped

    private var process: Process?
    private var adopted = false
    private var stopping = false
    private var stderrLines: [String] = []
    private var crashInstants: [ContinuousClock.Instant] = []
    private var monitorTask: Task<Void, Never>?
    private var restartTask: Task<Void, Never>?
    private var observers: [UUID: AsyncStream<DaemonState>.Continuation] = [:]

    public init(
        configuration: Configuration,
        tokenProvider: @escaping @Sendable () throws -> String,
        probe: HealthProbe? = nil
    ) {
        self.configuration = configuration
        self.tokenProvider = tokenProvider
        if let probe {
            self.probe = probe
        } else {
            let gatewayProbe = GatewayHealthProbe()
            self.probe = { url, token in await gatewayProbe.probe(baseURL: url, token: token) }
        }
    }

    public var baseURL: URL {
        URL(string: "http://127.0.0.1:\(configuration.port)")!
    }

    /// Observe state transitions; yields the current state immediately.
    public func states() -> AsyncStream<DaemonState> {
        AsyncStream { continuation in
            let id = UUID()
            continuation.yield(state)
            observers[id] = continuation
            continuation.onTermination = { [weak self] _ in
                Task { await self?.removeObserver(id) }
            }
        }
    }

    private func removeObserver(_ id: UUID) {
        observers.removeValue(forKey: id)
    }

    private func setState(_ newState: DaemonState) {
        guard newState != state else { return }
        logger.info("daemon state: \(String(describing: newState), privacy: .public)")
        state = newState
        for continuation in observers.values {
            continuation.yield(newState)
        }
    }

    // MARK: - Lifecycle

    public func start() async {
        switch state {
        case .stopped, .crashLooping, .portConflict, .tokenMismatch:
            break
        default:
            return // already launching/running
        }
        stopping = false
        crashInstants = []
        stderrLines = []
        setState(.launching)
        await attemptStartup()
    }

    public func stop() async {
        stopping = true
        restartTask?.cancel()
        restartTask = nil
        monitorTask?.cancel()
        monitorTask = nil
        guard let process, process.isRunning else {
            // Nothing we spawned is running; adopted daemons are left alone.
            process = nil
            setState(.stopped)
            return
        }
        logger.info("stopping daemon pid \(process.processIdentifier)")
        process.terminate() // SIGTERM
        let deadline = ContinuousClock.now + configuration.stopGracePeriod
        while process.isRunning, ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(50))
        }
        if process.isRunning {
            logger.warning("daemon ignored SIGTERM; sending SIGKILL")
            kill(process.processIdentifier, SIGKILL)
        }
        self.process = nil
        setState(.stopped)
    }

    // MARK: - Startup

    private func attemptStartup() async {
        guard let token = try? tokenProvider() else {
            logger.error("token provider failed; cannot start daemon")
            setState(.tokenMismatch)
            return
        }
        switch await probe(baseURL, token) {
        case .healthy:
            adopted = true
            logger.info("adopted an already-running healthy daemon")
            setState(.healthy(adopted: true))
            startMonitor(token: token)
        case .tokenRejected:
            setState(.tokenMismatch)
        case let .conflict(reason):
            setState(.portConflict(reason: reason))
        case .unreachable:
            await spawnAndAwaitHealth(token: token)
        }
    }

    private func spawnAndAwaitHealth(token: String) async {
        adopted = false
        do {
            try launchProcess(token: token)
        } catch {
            logger.error("daemon launch failed: \(error.localizedDescription)")
            appendStderr("launch failed: \(error.localizedDescription)")
            recordCrash()
            return
        }
        let deadline = ContinuousClock.now + configuration.launchTimeout
        while ContinuousClock.now < deadline, !stopping {
            // A crash during launch moves state via the termination handler;
            // stop polling if we are no longer the active launch attempt.
            guard case .launching = state else { return }
            if case .healthy = await probe(baseURL, token) {
                setState(.healthy(adopted: false))
                startMonitor(token: token)
                return
            }
            try? await Task.sleep(for: configuration.pollInterval)
        }
        guard !stopping, case .launching = state else { return }
        logger.error("daemon did not become healthy within launch timeout")
        appendStderr("did not become healthy within launch timeout")
        process?.terminate() // counts as a crash via the termination handler
    }

    private func launchProcess(token: String) throws {
        let process = Process()
        process.executableURL = configuration.executableURL
        process.arguments = configuration.arguments
        if let workingDirectory = configuration.workingDirectory {
            process.currentDirectoryURL = workingDirectory
        }
        var environment = ProcessInfo.processInfo.environment
        environment["IRIS_GATEWAY_TOKEN"] = token
        for (key, value) in configuration.extraEnvironment {
            environment[key] = value
        }
        process.environment = environment
        process.standardOutput = Pipe() // never inherit the app's stdio
        let stderrPipe = Pipe()
        process.standardError = stderrPipe
        stderrPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            let text = String(bytes: data, encoding: .utf8) ?? ""
            Task { await self?.appendStderr(text) }
        }
        process.terminationHandler = { [weak self] finished in
            let code = finished.terminationStatus
            finished.standardError
                .flatMap { $0 as? Pipe }?
                .fileHandleForReading.readabilityHandler = nil
            Task { await self?.processDidTerminate(code: code) }
        }
        try process.run()
        self.process = process
        logger.info("daemon spawned pid \(process.processIdentifier)")
    }

    // MARK: - Crash handling

    private func processDidTerminate(code: Int32) {
        process = nil
        monitorTask?.cancel()
        monitorTask = nil
        if stopping {
            setState(.stopped)
            return
        }
        logger.warning("daemon exited unexpectedly (code \(code))")
        recordCrash()
    }

    private func recordCrash() {
        let now = ContinuousClock.now
        crashInstants.append(now)
        crashInstants.removeAll { now - $0 > configuration.crashLoopWindow }
        if crashInstants.count >= configuration.crashLoopThreshold {
            logger.error("crash loop detected; supervision halted")
            setState(.crashLooping(stderrTail: stderrTail))
            return
        }
        let attempt = crashInstants.count
        setState(.restarting(attempt: attempt))
        let delay = Backoff.duration(
            attempt: attempt,
            minimum: configuration.minRestartBackoff,
            maximum: configuration.maxRestartBackoff
        )
        restartTask = Task {
            try? await Task.sleep(for: delay)
            await self.restartIfStillScheduled()
        }
    }

    private func restartIfStillScheduled() async {
        guard !stopping, case .restarting = state else { return }
        setState(.launching)
        await attemptStartup()
    }

    // MARK: - Health monitoring

    private func startMonitor(token: String) {
        monitorTask?.cancel()
        monitorTask = Task { [interval = configuration.healthCheckInterval] in
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)
                if Task.isCancelled { return }
                let keepGoing = await self.checkHealth(token: token)
                if !keepGoing { return }
            }
        }
    }

    private var consecutiveHealthFailures = 0

    /// Returns false when monitoring should stop (state left healthy family).
    private func checkHealth(token: String) async -> Bool {
        switch state {
        case .healthy, .unhealthy:
            break
        default:
            return false
        }
        switch await probe(baseURL, token) {
        case .healthy:
            consecutiveHealthFailures = 0
            if state == .unhealthy {
                setState(.healthy(adopted: adopted))
            }
            return true
        case .tokenRejected:
            setState(.tokenMismatch)
            return false
        case .unreachable, .conflict:
            consecutiveHealthFailures += 1
            guard consecutiveHealthFailures >= 2 else { return true }
            consecutiveHealthFailures = 0
            if adopted {
                // The adopted daemon went away; take over with our own.
                logger.warning("adopted daemon disappeared; spawning our own")
                adopted = false
                recordCrash()
                return false
            }
            if process?.isRunning == true {
                setState(.unhealthy) // alive but not answering
                return true
            }
            return true // termination handler will drive the restart
        }
    }

    // MARK: - Stderr capture

    private static let maxStderrLines = 50

    private func appendStderr(_ text: String) {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
        stderrLines.append(contentsOf: lines.map(String.init))
        if stderrLines.count > Self.maxStderrLines {
            stderrLines.removeFirst(stderrLines.count - Self.maxStderrLines)
        }
    }

    public var stderrTail: String {
        stderrLines.joined(separator: "\n")
    }
}
