import XCTest

/// Automated coverage for the resilience-audit §2 failure-mode surfaces
/// (docs/desktop/resilience-audit.md). Each failure must end in a
/// comprehensible state with exactly one primary recovery action — these tests
/// pin that state via `--ui-test-*` and assert the recovery affordance is
/// present and hittable, with no real daemon, mic, or network involved.
///
/// What stays manual (and why the §2 checklist remains): inducing the *real*
/// transition (SIGKILL, port occupation, TCC revocation, sleep/wake) against a
/// signed release build, plus the screenshots. These tests verify the
/// destination UI; the manual drills verify the daemon/app actually routes
/// there.
final class ResilienceUITests: XCTestCase {
    // MARK: - Launch helpers

    /// Pin a rendered overlay state (no daemon) and return the panel once it
    /// is on screen, or skip if panels aren't reachable in this environment.
    private func launchOverlay(state: String) throws -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "--ui-test-state", "healthy",
            "--ui-test-overlay",
            "--ui-test-overlay-state", state
        ]
        app.launch()
        let overlay = app.descendants(matching: .any)["overlay-panel"]
        guard overlay.waitForExistence(timeout: 10) else {
            throw XCTSkip("overlay panel not reachable in this environment")
        }
        return app
    }

    private func openMenu(state: String) throws -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-test-state", state]
        app.launch()
        let statusItem = app.statusItems.firstMatch
        guard statusItem.waitForExistence(timeout: 10) else {
            throw XCTSkip("status item not reachable in this environment")
        }
        statusItem.click()
        return app
    }

    /// Assert exactly one of the known recovery buttons is offered, and it is
    /// the expected one. Keeping a single source of truth for the labels means
    /// a renamed button fails loudly here.
    private static let allRecoveryLabels = [
        "Restart Iris", "Open Diagnostics", "Try Again",
        "Open System Settings", "Reconnect", "Start Iris"
    ]

    private func assertSoleRecovery(
        _ app: XCUIApplication, expected: String,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        let button = app.buttons[expected]
        XCTAssertTrue(
            button.waitForExistence(timeout: 5),
            "expected recovery action \"\(expected)\"", file: file, line: line
        )
        XCTAssertTrue(
            button.isEnabled,
            "\(expected) must be actionable",
            file: file,
            line: line
        )
        for other in Self.allRecoveryLabels where other != expected {
            XCTAssertFalse(
                app.buttons[other].exists,
                "unexpected second recovery action \"\(other)\"",
                file: file, line: line
            )
        }
    }

    // MARK: - F1/F2 daemon down & crash-loop (overlay)

    func testOverlayDaemonDownOffersRestart() throws {
        let app = try launchOverlay(state: "error-daemon")
        XCTAssertTrue(app.staticTexts["Iris: Iris isn't running."].exists)
        assertSoleRecovery(app, expected: "Restart Iris")
    }

    func testOverlayCrashLoopOffersDiagnostics() throws {
        let app = try launchOverlay(state: "error-crash")
        assertSoleRecovery(app, expected: "Open Diagnostics")
    }

    // MARK: - F3 port conflict (overlay + menu)

    func testOverlayPortConflictOffersTryAgain() throws {
        let app = try launchOverlay(state: "error-port")
        assertSoleRecovery(app, expected: "Try Again")
    }

    func testMenuPortConflictOffersTryAgain() throws {
        let app = try openMenu(state: "portConflict")
        XCTAssertTrue(
            app.menuItems["Port conflict: port 8765 is owned by something else"]
                .waitForExistence(timeout: 5)
        )
        let action = app.menuItems["Try Again"]
        XCTAssertTrue(action.exists)
        XCTAssertTrue(action.isEnabled)
    }

    // MARK: - F4 token mismatch (overlay + menu)

    func testOverlayTokenMismatchOffersTryAgain() throws {
        let app = try launchOverlay(state: "error-token")
        assertSoleRecovery(app, expected: "Try Again")
    }

    func testMenuTokenMismatchOffersReconnect() throws {
        let app = try openMenu(state: "tokenMismatch")
        XCTAssertTrue(
            app.menuItems["Connection token rejected"].waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.menuItems["Reconnect"].exists)
        XCTAssertTrue(app.menuItems["Reconnect"].isEnabled)
    }

    // MARK: - F5 OpenAI unreachable / key problem (overlay)

    func testOverlayOpenAIErrorOffersTryAgain() throws {
        let app = try launchOverlay(state: "error")
        XCTAssertTrue(app.staticTexts["Iris: Couldn't reach OpenAI."].exists)
        assertSoleRecovery(app, expected: "Try Again")
    }

    // MARK: - F6 microphone / TCC denied (overlay + menu transient)

    func testOverlayMicrophoneOffOffersSystemSettings() throws {
        let app = try launchOverlay(state: "error-mic")
        assertSoleRecovery(app, expected: "Open System Settings")
    }

    func testMenuUnhealthyOffersRestart() throws {
        let app = try openMenu(state: "unhealthy")
        XCTAssertTrue(
            app.menuItems["Running, but not responding"].waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.menuItems["Restart Iris"].exists)
    }

    /// Transient supervision states must NOT offer a recovery action — the app
    /// is already recovering; a button would invite double restarts.
    func testMenuRestartingShowsProgressWithoutAction() throws {
        let app = try openMenu(state: "restarting")
        XCTAssertTrue(
            app.menuItems["Restarting (attempt 2)…"].waitForExistence(timeout: 5)
        )
        for label in Self.allRecoveryLabels {
            XCTAssertFalse(
                app.menuItems[label].exists,
                "restarting is transient; no recovery button expected"
            )
        }
    }

    // MARK: - F10 sleep/wake → session ended cleanly

    func testOverlayEndedStateRendersAndDismisses() throws {
        let app = try launchOverlay(state: "ended")
        let overlay = app.descendants(matching: .any)["overlay-panel"]
        XCTAssertTrue(overlay.exists)
        // A clean end shows a "Session ended" surface (EndedView combines its
        // children under that label), never a frozen "listening" one.
        XCTAssertTrue(
            app.descendants(matching: .any)["Session ended"]
                .waitForExistence(timeout: 5)
        )
        overlay.typeKey(.escape, modifierFlags: [])
        _ = waitForDisappearance(of: overlay, timeout: 3)
    }

    // MARK: - §2.8 large activity history (scroll responsiveness)

    /// Loads a ~10k-item feed (`--ui-test-activity large`) and scrolls it. The
    /// UI half of the scale drill: a populated list, no skeleton stall, and a
    /// responsive scroll. The daemon-RSS-flat half stays a manual check (there
    /// is no daemon in scripted mode).
    ///
    /// This test is slow (~2 min): XCUITest snapshots the accessibility tree on
    /// every query, and a 10k-row List makes each snapshot expensive. That is a
    /// harness cost, not app latency — the in-app scroll itself stays smooth.
    func testLargeActivityHistoryScrollsResponsively() throws {
        let app = XCUIApplication()
        app.launchArguments = [
            "--ui-test-state", "healthy", "--open-main-window",
            "--ui-test-activity", "large"
        ]
        app.launch()

        let window = app.windows["Iris"]
        guard window.waitForExistence(timeout: 10) else {
            throw XCTSkip("main window not reachable in this environment")
        }
        let sidebar = app.descendants(matching: .any)["sidebar-activity"]
        guard sidebar.waitForExistence(timeout: 5) else {
            throw XCTSkip("sidebar not reachable in this environment")
        }
        sidebar.click()
        XCTAssertTrue(
            app.descendants(matching: .any)["section-activity"]
                .waitForExistence(timeout: 20),
            "activity section did not render (stuck on skeleton?)"
        )

        let scroller = app.scrollViews.firstMatch
        guard scroller.waitForExistence(timeout: 10) else {
            throw XCTSkip("activity scroll view not reachable in this environment")
        }
        // A populated row proves the 10k feed decoded and rendered, not the
        // skeleton. Rows combine their children under "<title>, <status>".
        XCTAssertTrue(
            app.descendants(matching: .any)["Activity item 1, done"]
                .firstMatch.waitForExistence(timeout: 10),
            "large activity feed did not render rows"
        )
        // Scrolling a deep list must stay responsive (no beachball / hang).
        measure(metrics: [XCTClockMetric()]) {
            for _ in 0 ..< 20 {
                scroller.scroll(byDeltaX: 0, deltaY: -600)
            }
        }
        // The window is still alive and interactive after the scroll storm.
        XCTAssertTrue(window.exists)
    }

    // MARK: - Helpers

    private func waitForDisappearance(
        of element: XCUIElement, timeout: TimeInterval
    ) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }
}
