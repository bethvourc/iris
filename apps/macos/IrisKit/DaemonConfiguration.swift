import Foundation

/// How `DaemonManager` should launch the Python daemon: the executable, its
/// arguments and environment, and the supervision timings. Two factories build
/// the two run modes — `development` (repo checkout via uv) and `bundled` (the
/// embedded runtime shipped inside the app).
public struct DaemonConfiguration: Sendable {
    public var executableURL: URL
    public var arguments: [String]
    public var workingDirectory: URL?
    public var extraEnvironment: [String: String]
    public var port: Int
    public var launchTimeout: Duration
    public var pollInterval: Duration
    public var healthCheckInterval: Duration
    public var minRestartBackoff: Duration
    public var maxRestartBackoff: Duration
    public var crashLoopThreshold: Int
    public var crashLoopWindow: Duration
    public var stopGracePeriod: Duration

    public init(
        executableURL: URL,
        arguments: [String],
        workingDirectory: URL? = nil,
        extraEnvironment: [String: String] = [:],
        port: Int = AppPreferences.defaultGatewayPort,
        launchTimeout: Duration = .seconds(15),
        pollInterval: Duration = .milliseconds(200),
        healthCheckInterval: Duration = .seconds(5),
        minRestartBackoff: Duration = .seconds(1),
        maxRestartBackoff: Duration = .seconds(30),
        crashLoopThreshold: Int = 3,
        crashLoopWindow: Duration = .seconds(60),
        stopGracePeriod: Duration = .seconds(5)
    ) {
        self.executableURL = executableURL
        self.arguments = arguments
        self.workingDirectory = workingDirectory
        self.extraEnvironment = extraEnvironment
        self.port = port
        self.launchTimeout = launchTimeout
        self.pollInterval = pollInterval
        self.healthCheckInterval = healthCheckInterval
        self.minRestartBackoff = minRestartBackoff
        self.maxRestartBackoff = maxRestartBackoff
        self.crashLoopThreshold = crashLoopThreshold
        self.crashLoopWindow = crashLoopWindow
        self.stopGracePeriod = stopGracePeriod
    }

    /// Dev mode: run the gateway from the repo checkout via uv.
    public static func development(
        repoRoot: URL,
        port: Int = AppPreferences.defaultGatewayPort,
        logDirectory: URL
    ) -> DaemonConfiguration {
        DaemonConfiguration(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: [
                "uv", "run", "iris", "serve",
                "--port", String(port),
                "--json-logs",
                "--log-dir", logDirectory.path
            ],
            workingDirectory: repoRoot,
            port: port
        )
    }

    /// Release mode: run the gateway from the embedded Python runtime shipped in
    /// `Iris.app/Contents/Resources/iris-runtime/` (built by
    /// `scripts/package_python.sh`). No system Python or dev tools required.
    public static func bundled(
        resourcesURL: URL,
        port: Int = AppPreferences.defaultGatewayPort,
        logDirectory: URL,
        stateDatabaseURL: URL? = nil
    ) -> DaemonConfiguration {
        let python = resourcesURL
            .appending(path: "iris-runtime/python/bin/python3")
        // A shipped app has no repo checkout, so without this the daemon's state
        // DB + settings.json would fall back to the process cwd (undefined for a
        // Finder-launched app). Pin them under Application Support so reset and
        // uninstall have one known home (docs/desktop/runbook.md).
        var environment: [String: String] = [:]
        if let stateDatabaseURL {
            environment["IRIS_STATE_DB"] = stateDatabaseURL.path
        }
        return DaemonConfiguration(
            // Invoke the interpreter directly with `-m iris` rather than the
            // generated `iris` console script, whose shebang isn't relocatable.
            executableURL: python,
            arguments: [
                "-m", "iris", "serve",
                "--port", String(port),
                "--json-logs",
                "--log-dir", logDirectory.path
            ],
            extraEnvironment: environment,
            port: port
        )
    }

    /// Whether the embedded runtime exists at `resourcesURL` (a packaged app).
    public static func bundledRuntimeExists(resourcesURL: URL) -> Bool {
        let python = resourcesURL
            .appending(path: "iris-runtime/python/bin/python3")
        return FileManager.default.isExecutableFile(atPath: python.path)
    }
}
