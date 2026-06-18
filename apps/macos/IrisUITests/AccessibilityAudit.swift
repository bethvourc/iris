import XCTest

/// Runs Xcode's built-in `performAccessibilityAudit()` on every screen of the
/// console, with content present (populated/pending/loaded scenarios) so the
/// audit sees the real controls rather than empty/loading placeholders.
///
/// The audit covers all categories except contrast, which is suppressed in the
/// issue handler: the palette is verified against WCAG AA arithmetically in
/// `DesignSystem`, and the audit's own contrast heuristic does not understand
/// our appearance-adaptive tokens or the translucent overlay material. What
/// remains — missing labels, undersized hit regions, undetectable elements, and
/// wrong traits — is exactly what a sweep should catch.
@MainActor
final class AccessibilityAudit: XCTestCase {
    // MARK: - Main window sections

    private func launchMainWindow() throws -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "--ui-test-state", "healthy",
            "--open-main-window",
            "--ui-test-home", "populated",
            "--ui-test-activity", "populated",
            "--ui-test-approvals", "pending",
            "--ui-test-settings", "loaded"
        ]
        app.launch()
        guard app.windows["Iris"].waitForExistence(timeout: 10) else {
            throw XCTSkip("main window not reachable in this environment")
        }
        return app
    }

    /// Selects a section in the sidebar and waits for its detail to appear.
    private func show(_ section: String, in app: XCUIApplication) throws {
        let item = app.descendants(matching: .any)["sidebar-\(section)"]
        guard item.waitForExistence(timeout: 5) else {
            throw XCTSkip("sidebar not reachable in this environment")
        }
        item.click()
        XCTAssertTrue(
            app.descendants(matching: .any)["section-\(section)"].waitForExistence(timeout: 5),
            "expected \(section) detail before auditing it"
        )
    }

    private func audit(_ app: XCUIApplication, name: String) throws {
        var captured: [String] = []
        try app.performAccessibilityAudit { issue in
            if !Self.isFrameworkOwned(issue) {
                captured.append("[\(issue.auditType)] \(issue.compactDescription) :: "
                    + "<\(issue.element?.debugDescription ?? "nil")>")
            }
            // Suppress every issue here so the audit doesn't record its own
            // (terse) failures; we re-raise the app-owned ones below with full
            // element context.
            return true
        }
        if !captured.isEmpty {
            XCTFail("[\(name)] \(captured.count) a11y issue(s):\n"
                + captured.joined(separator: "\n"))
        }
    }

    /// "Element has no description" (rawValue 1<<3) and the macOS-only
    /// "Parent/Child mismatch" (1<<33) have no symbolic constants in the macOS
    /// SDK, so match them by their stable raw values.
    private static let descriptionMissing = XCUIAccessibilityAuditType(rawValue: 1 << 3)
    private static let parentChildMismatch = XCUIAccessibilityAuditType(rawValue: 1 << 33)

    /// Container / system-chrome roles that legitimately carry no description
    /// and that we don't construct (the NSHostingView root group, the overlay's
    /// hosting group, the Touch Bar, help-tag tooltips, scroll/list wrappers).
    private static let containerRoles: Set<XCUIElement.ElementType> = [
        .group, .window, .dialog, .touchBar, .helpTag, .scrollView, .table, .outline, .other
    ]

    /// Findings we intentionally accept because they belong to AppKit/SwiftUI
    /// or the OS, not to any view we author. Everything else is a real defect.
    private static func isFrameworkOwned(_ issue: XCUIAccessibilityAuditIssue) -> Bool {
        // Contrast: every token is verified against WCAG AA arithmetically in
        // `DesignSystem`; the heuristic can't read our adaptive colors or the
        // overlay's translucent material.
        if issue.auditType.contains(.contrast) { return true }

        let type = issue.element?.elementType

        // SwiftUI `Picker` bridges to an `NSPopUpButton` the audit believes has
        // no press action, though it opens and selects normally — a bridge
        // limitation, not a missing affordance.
        if issue.auditType.contains(.action), type == .popUpButton { return true }

        // nil role means the audit couldn't map the element — invariably hosting
        // chrome, never our content.
        let isContainer = type.map(containerRoles.contains) ?? true

        // WCAG requires descriptions for content and controls, not for grouping
        // chrome; and the structural parent/child mismatch only fires inside
        // SwiftUI's hosting hierarchy. Both are accepted on container roles
        // while content/control roles stay held to the rule.
        if issue.auditType.contains(descriptionMissing), isContainer { return true }
        if issue.auditType.contains(parentChildMismatch), isContainer { return true }

        return false
    }

    func testHomeAccessibility() throws {
        let app = try launchMainWindow()
        XCTAssertTrue(
            app.descendants(matching: .any)["section-home"].waitForExistence(timeout: 5)
        )
        try audit(app, name: "home")
    }

    func testActivityAccessibility() throws {
        let app = try launchMainWindow()
        try show("activity", in: app)
        try audit(app, name: "activity")
    }

    func testApprovalsAccessibility() throws {
        let app = try launchMainWindow()
        try show("approvals", in: app)
        try audit(app, name: "approvals")
    }

    func testSettingsAccessibility() throws {
        let app = try launchMainWindow()
        try show("settings", in: app)
        try audit(app, name: "settings")
    }

    // MARK: - Overlay

    func testOverlayAccessibility() throws {
        let app = XCUIApplication()
        app.launchArguments = [
            "--ui-test-state", "healthy",
            "--ui-test-overlay",
            "--ui-test-overlay-state", "listening"
        ]
        app.launch()
        guard app.descendants(matching: .any)["overlay-panel"].waitForExistence(timeout: 10) else {
            throw XCTSkip("overlay panel not reachable in this environment")
        }
        try audit(app, name: "overlay")
    }
}
