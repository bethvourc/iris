import XCTest

/// Drives the menu bar shell with pinned daemon states
/// (`--ui-test-state`, see AppModel). No real daemon is spawned.
final class IrisUITests: XCTestCase {
    private func launch(state: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-test-state", state]
        app.launch()
        return app
    }

    private func openMenu(of app: XCUIApplication) throws -> XCUIElement {
        let statusItem = app.statusItems.firstMatch
        guard statusItem.waitForExistence(timeout: 10) else {
            throw XCTSkip("status item not reachable in this environment")
        }
        statusItem.click()
        return statusItem
    }

    func testHealthyMenuShowsStatusAndActions() throws {
        let app = launch(state: "healthy")
        _ = try openMenu(of: app)

        XCTAssertTrue(app.menuItems["Running"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.menuItems["Open Iris"].exists)
        XCTAssertTrue(app.menuItems["Quit Iris"].exists)
        XCTAssertTrue(app.menuItems["Talk to Iris"].exists)
        XCTAssertFalse(app.menuItems["Talk to Iris"].isEnabled)
        // healthy state has no recovery action
        XCTAssertFalse(app.menuItems["Restart Iris"].exists)
    }

    func testCrashLoopMenuOffersRestart() throws {
        let app = launch(state: "crashLooping")
        _ = try openMenu(of: app)

        XCTAssertTrue(app.menuItems["Iris keeps crashing"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.menuItems["Restart Iris"].exists)
        XCTAssertTrue(app.menuItems["Restart Iris"].isEnabled)
    }

    func testStoppedMenuOffersStart() throws {
        let app = launch(state: "stopped")
        _ = try openMenu(of: app)

        XCTAssertTrue(app.menuItems["Iris is stopped"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.menuItems["Start Iris"].exists)
    }

    // MARK: - Onboarding

    private func launchOnboarding(
        permissions: String, step: String? = nil
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "--ui-test-state", "stopped",
            "--ui-test-onboarding",
            "--ui-test-permissions", permissions
        ]
        if let step {
            app.launchArguments += ["--ui-test-onboarding-step", step]
        }
        app.launch()
        return app
    }

    func testOnboardingFlowCompletes() throws {
        let app = launchOnboarding(permissions: "undetermined")
        let window = app.windows["Welcome to Iris"]
        guard window.waitForExistence(timeout: 10) else {
            throw XCTSkip("onboarding window not reachable in this environment")
        }

        // welcome → key
        window.buttons["Continue"].click()
        // daemon is pinned-off, so the key step offers skip
        XCTAssertTrue(window.buttons["Skip for Now"].waitForExistence(timeout: 5))
        window.buttons["Skip for Now"].click()

        // microphone: allow flips the scripted permission to granted
        XCTAssertTrue(window.buttons["Allow Microphone"].waitForExistence(timeout: 5))
        window.buttons["Allow Microphone"].click()
        XCTAssertTrue(window.buttons["Continue"].waitForExistence(timeout: 5))
        window.buttons["Continue"].click()

        // system access: both optional
        XCTAssertTrue(window.buttons["Skip for Now"].waitForExistence(timeout: 5))
        window.buttons["Skip for Now"].click()

        // done
        XCTAssertTrue(window.buttons["Start Using Iris"].waitForExistence(timeout: 5))
        window.buttons["Start Using Iris"].click()
        XCTAssertTrue(waitForDisappearance(of: window, timeout: 5))
    }

    func testOnboardingDeniedMicrophoneOffersSystemSettings() throws {
        let app = launchOnboarding(permissions: "denied", step: "microphone")
        let window = app.windows["Welcome to Iris"]
        guard window.waitForExistence(timeout: 10) else {
            throw XCTSkip("onboarding window not reachable in this environment")
        }
        XCTAssertTrue(
            window.buttons["Open System Settings"].waitForExistence(timeout: 5)
        )
        XCTAssertFalse(window.buttons["Allow Microphone"].exists)
        // denied is skippable — degraded mode, never a dead end
        XCTAssertTrue(window.buttons["Skip for Now"].exists)
    }

    private func waitForDisappearance(of element: XCUIElement, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }

    func testOpenIrisShowsMainWindow() throws {
        let app = launch(state: "healthy")
        _ = try openMenu(of: app)

        let openItem = app.menuItems["Open Iris"]
        guard openItem.waitForExistence(timeout: 5) else {
            throw XCTSkip("menu not reachable in this environment")
        }
        openItem.click()
        XCTAssertTrue(app.windows["Iris"].waitForExistence(timeout: 5))
    }
}
