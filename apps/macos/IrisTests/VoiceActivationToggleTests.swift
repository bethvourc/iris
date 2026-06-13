import XCTest
@testable import IrisKit

@MainActor
final class VoiceActivationToggleTests: XCTestCase {
    func testPressFromIdleActivatesAndGoesPending() {
        let toggle = VoiceActivationToggle()
        XCTAssertEqual(toggle.press(), .activate)
        XCTAssertEqual(toggle.phase, .pending)
    }

    func testSecondPressBeforeConfirmationCancels() {
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        XCTAssertEqual(toggle.press(), .deactivate)
        XCTAssertEqual(toggle.phase, .idle)
    }

    func testSessionConfirmationMovesPendingToActive() {
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        toggle.update(sessionActive: true)
        XCTAssertEqual(toggle.phase, .active)
        XCTAssertEqual(toggle.press(), .deactivate)
    }

    func testSessionEndReturnsActiveToIdle() {
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        toggle.update(sessionActive: true)
        toggle.update(sessionActive: false) // ended by voice/daemon
        XCTAssertEqual(toggle.phase, .idle)
        XCTAssertEqual(toggle.press(), .activate)
    }

    func testInactiveUpdateWhilePendingKeepsPending() {
        // Start is in flight; a stale "inactive" snapshot must not clear it.
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        toggle.update(sessionActive: false)
        XCTAssertEqual(toggle.phase, .pending)
    }

    func testActivationFailureClearsPending() {
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        toggle.activationFailed()
        XCTAssertEqual(toggle.phase, .idle)
        XCTAssertEqual(toggle.press(), .activate)
    }

    func testFailedStopIsReconciledBySessionTruth() {
        let toggle = VoiceActivationToggle()
        _ = toggle.press()
        toggle.update(sessionActive: true)
        _ = toggle.press() // optimistic deactivate…
        toggle.update(sessionActive: true) // …but the session is still live
        XCTAssertEqual(toggle.phase, .active)
        XCTAssertEqual(toggle.press(), .deactivate)
    }
}
