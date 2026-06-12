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
