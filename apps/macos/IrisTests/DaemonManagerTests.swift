import Foundation
import XCTest
@testable import IrisKit

final class DaemonManagerTests: XCTestCase {
    private var workDir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        workDir = FileManager.default.temporaryDirectory
            .appending(path: "iris-daemon-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: workDir)
        try super.tearDownWithError()
    }

    // MARK: - Configuration factories

    func testBundledConfigurationResolvesEmbeddedRuntime() {
        let resources = URL(fileURLWithPath: "/Apps/Iris.app/Contents/Resources")
        let logs = URL(fileURLWithPath: "/tmp/logs")
        let config = DaemonConfiguration.bundled(
            resourcesURL: resources, port: 8800, logDirectory: logs
        )
        XCTAssertEqual(
            config.executableURL.path,
            "/Apps/Iris.app/Contents/Resources/iris-runtime/python/bin/python3"
        )
        XCTAssertEqual(
            config.arguments,
            ["-m", "iris", "serve", "--port", "8800", "--json-logs",
             "--log-dir", "/tmp/logs"]
        )
        // No working directory: the embedded runtime is self-contained.
        XCTAssertNil(config.workingDirectory)
        XCTAssertEqual(config.port, 8800)
    }

    func testBundledRuntimeExistsDetection() throws {
        // Absent in a bare directory...
        XCTAssertFalse(DaemonConfiguration.bundledRuntimeExists(resourcesURL: workDir))
        // ...present once an executable interpreter is laid down.
        let binDir = workDir.appending(path: "iris-runtime/python/bin")
        try FileManager.default.createDirectory(
            at: binDir, withIntermediateDirectories: true
        )
        let python = binDir.appending(path: "python3")
        try Data("#!/bin/sh\n".utf8).write(to: python)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755], ofItemAtPath: python.path
        )
        XCTAssertTrue(DaemonConfiguration.bundledRuntimeExists(resourcesURL: workDir))
    }

    // MARK: - Fixtures

    private func writeScript(_ body: String) throws -> URL {
        let url = workDir.appending(path: "stub-\(UUID().uuidString).sh")
        try "#!/bin/sh\n\(body)\n".write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755], ofItemAtPath: url.path
        )
        return url
    }

    private func makeConfiguration(executable: URL, marker: URL? = nil) -> DaemonManager.Configuration {
        var environment: [String: String] = [:]
        if let marker {
            environment["MARKER"] = marker.path
        }
        return DaemonManager.Configuration(
            executableURL: executable,
            arguments: [],
            extraEnvironment: environment,
            port: 8765,
            launchTimeout: .seconds(2),
            pollInterval: .milliseconds(30),
            healthCheckInterval: .milliseconds(200),
            minRestartBackoff: .milliseconds(20),
            maxRestartBackoff: .milliseconds(50),
            crashLoopThreshold: 3,
            crashLoopWindow: .seconds(10),
            stopGracePeriod: .seconds(2)
        )
    }

    /// Probe that reports healthy once the stub has created its marker file.
    private static func fileMarkerProbe(_ marker: URL) -> DaemonManager.HealthProbe {
        { _, _ in
            FileManager.default.fileExists(atPath: marker.path) ? .healthy : .unreachable
        }
    }

    private static func constantProbe(_ outcome: ProbeOutcome) -> DaemonManager.HealthProbe {
        { _, _ in outcome }
    }

    /// Subscribes to state changes, runs `action`, and collects states until
    /// `until` matches or the timeout elapses.
    private func collectStates(
        of manager: DaemonManager,
        until predicate: @escaping @Sendable (DaemonState) -> Bool,
        timeout: Duration = .seconds(8),
        action: @escaping @Sendable () async -> Void
    ) async -> [DaemonState] {
        let stream = await manager.states()
        let collector = Task {
            var collected: [DaemonState] = []
            for await state in stream {
                collected.append(state)
                if predicate(state) { break }
            }
            return collected
        }
        let watchdog = Task {
            try? await Task.sleep(for: timeout)
            collector.cancel()
        }
        await action()
        let result = await collector.value
        watchdog.cancel()
        return result
    }

    // MARK: - Tests

    func testSpawnsDaemonWithTokenInEnvironmentAndBecomesHealthy() async throws {
        let marker = workDir.appending(path: "marker")
        let script = try writeScript(#"echo "$IRIS_GATEWAY_TOKEN" > "$MARKER"; exec sleep 600"#)
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script, marker: marker),
            tokenProvider: { "stub-token-value" },
            probe: Self.fileMarkerProbe(marker)
        )

        let states = await collectStates(of: manager, until: { $0 == .healthy(adopted: false) }, action: {
            await manager.start()
        })
        XCTAssertEqual(states, [.stopped, .launching, .healthy(adopted: false)])

        let written = try String(contentsOf: marker, encoding: .utf8)
        XCTAssertEqual(written.trimmingCharacters(in: .whitespacesAndNewlines), "stub-token-value")

        await manager.stop()
        let final = await manager.state
        XCTAssertEqual(final, .stopped)
    }

    func testAdoptsExistingHealthyDaemonWithoutSpawning() async throws {
        let marker = workDir.appending(path: "marker")
        let script = try writeScript(#"touch "$MARKER"; exec sleep 600"#)
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script, marker: marker),
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.healthy)
        )

        let states = await collectStates(of: manager, until: { $0 == .healthy(adopted: true) }, action: {
            await manager.start()
        })
        XCTAssertEqual(states, [.stopped, .launching, .healthy(adopted: true)])
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: marker.path),
            "no process may be spawned when adopting"
        )

        await manager.stop()
        let final = await manager.state
        XCTAssertEqual(final, .stopped)
    }

    func testPortConflictIsTerminal() async throws {
        let script = try writeScript("exit 0")
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script),
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.conflict(reason: "port is owned by something that isn't Iris"))
        )
        let states = await collectStates(of: manager, until: \.isTerminalFailure, action: {
            await manager.start()
        })
        XCTAssertEqual(
            states.last,
            .portConflict(reason: "port is owned by something that isn't Iris")
        )
    }

    func testTokenRejectionSurfacesTokenMismatch() async throws {
        let script = try writeScript("exit 0")
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script),
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.tokenRejected)
        )
        let states = await collectStates(of: manager, until: \.isTerminalFailure, action: {
            await manager.start()
        })
        XCTAssertEqual(states.last, .tokenMismatch)
    }

    func testCrashLoopAfterRepeatedExitsCarriesStderrTail() async throws {
        let script = try writeScript("echo boom >&2; exit 1")
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script),
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.unreachable)
        )

        let states = await collectStates(of: manager, until: \.isTerminalFailure, action: {
            await manager.start()
        })
        XCTAssertEqual(Array(states.prefix(6)), [
            .stopped,
            .launching,
            .restarting(attempt: 1),
            .launching,
            .restarting(attempt: 2),
            .launching
        ])
        guard case let .crashLooping(stderrTail) = states.last else {
            return XCTFail("expected crashLooping, got \(String(describing: states.last))")
        }
        XCTAssertTrue(stderrTail.contains("boom"))
    }

    func testMissingExecutableEndsInCrashLoop() async {
        let manager = DaemonManager(
            configuration: makeConfiguration(
                executable: workDir.appending(path: "does-not-exist")
            ),
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.unreachable)
        )
        let states = await collectStates(of: manager, until: \.isTerminalFailure, action: {
            await manager.start()
        })
        guard case let .crashLooping(stderrTail) = states.last else {
            return XCTFail("expected crashLooping, got \(String(describing: states.last))")
        }
        XCTAssertTrue(stderrTail.contains("launch failed"))
    }

    func testLaunchTimeoutCountsAsCrash() async throws {
        // Hangs without ever becoming healthy.
        let script = try writeScript("exec sleep 600")
        var configuration = makeConfiguration(executable: script)
        configuration.launchTimeout = .milliseconds(200)
        let manager = DaemonManager(
            configuration: configuration,
            tokenProvider: { "stub-token-value" },
            probe: Self.constantProbe(.unreachable)
        )

        let states = await collectStates(
            of: manager,
            until: { $0 == .restarting(attempt: 1) },
            action: {
                await manager.start()
            }
        )
        XCTAssertEqual(states.last, .restarting(attempt: 1))
        await manager.stop()
    }

    func testStopTerminatesSpawnedProcess() async throws {
        let marker = workDir.appending(path: "marker")
        let pidFile = workDir.appending(path: "pid")
        let script = try writeScript(
            #"echo $$ > "\#(pidFile.path)"; touch "$MARKER"; exec sleep 600"#
        )
        let manager = DaemonManager(
            configuration: makeConfiguration(executable: script, marker: marker),
            tokenProvider: { "stub-token-value" },
            probe: Self.fileMarkerProbe(marker)
        )
        _ = await collectStates(of: manager, until: { $0 == .healthy(adopted: false) }, action: {
            await manager.start()
        })
        let pid = try XCTUnwrap(
            Int32(String(contentsOf: pidFile, encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines))
        )

        await manager.stop()

        let final = await manager.state
        XCTAssertEqual(final, .stopped)
        // `exec` replaced the shell, so the recorded pid is the daemon's.
        XCTAssertEqual(kill(pid, 0), -1, "spawned process must be terminated")
        XCTAssertEqual(errno, ESRCH)
    }
}
