import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class VoiceSessionViewModelTests: XCTestCase {
    /// A stream the test feeds events into via its continuation.
    private func makeStream() -> (
        @Sendable () -> AsyncStream<SSEClientEvent>, AsyncStream<SSEClientEvent>.Continuation
    ) {
        let (stream, continuation) = AsyncStream.makeStream(of: SSEClientEvent.self)
        return ({ stream }, continuation)
    }

    private func event(_ type: String, _ json: String) -> SSEClientEvent {
        .event(SSEEvent(type: type, data: json))
    }

    /// Polls until `predicate` holds or the timeout elapses.
    private func waitUntil(
        timeout: Duration = .seconds(2),
        _ predicate: @escaping () -> Bool
    ) async {
        let deadline = ContinuousClock.now + timeout
        while ContinuousClock.now < deadline {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    private func makeModel(
        control: FakeVoiceControl = FakeVoiceControl(),
        stream: @escaping @Sendable () -> AsyncStream<SSEClientEvent>
    ) -> VoiceSessionViewModel {
        VoiceSessionViewModel(
            voiceControl: control,
            endedDismissDelay: .milliseconds(40),
            eventStream: stream
        )
    }

    // MARK: - Happy path

    func testStateEventsDriveDisplayState() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()
        XCTAssertEqual(model.displayState, .connecting)

        continuation.yield(event("state", #"{"state":"listening"}"#))
        await waitUntil { model.displayState == .listening }

        continuation.yield(event("state", #"{"state":"thinking"}"#))
        await waitUntil { model.displayState == .thinking }

        continuation.yield(event("state", #"{"state":"speaking"}"#))
        await waitUntil { model.displayState == .speaking }
    }

    func testTranscriptAndDeltasAccumulate() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("user_transcript", #"{"text":"hello iris","final":true}"#))
        await waitUntil { model.userText == "hello iris" }

        continuation.yield(event("assistant_delta", #"{"text":"Hi "}"#))
        continuation.yield(event("assistant_delta", #"{"text":"there."}"#))
        await waitUntil { model.assistantText == "Hi there." }
        XCTAssertEqual(model.displayState, .speaking)

        continuation.yield(event("assistant_done", #"{"text":"Hi there!"}"#))
        await waitUntil { model.assistantText == "Hi there!" }
    }

    func testNewUserTurnClearsPriorAssistantText() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("assistant_delta", #"{"text":"First answer."}"#))
        await waitUntil { model.assistantText == "First answer." }

        continuation.yield(event("user_transcript", #"{"text":"another question","final":true}"#))
        await waitUntil { model.userText == "another question" }
        XCTAssertEqual(model.assistantText, "")
    }

    func testInterruptedEventRendersThenStateOverrides() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("state", #"{"state":"speaking"}"#))
        await waitUntil { model.displayState == .speaking }
        continuation.yield(event("interrupted", #"{"at_ms":1200}"#))
        await waitUntil { model.displayState == .interrupted }
        // The daemon always follows barge-in with a state event.
        continuation.yield(event("state", #"{"state":"user_speaking"}"#))
        await waitUntil { model.displayState == .userSpeaking }
    }

    func testSessionEndedShowsSummaryAndAutoDismisses() async {
        let (factory, continuation) = makeStream()
        var dismissed = false
        let model = makeModel(stream: factory)
        model.onShouldDismiss = { dismissed = true }
        model.begin()

        continuation.yield(event("assistant_done", #"{"text":"All done."}"#))
        await waitUntil { model.assistantText == "All done." }
        continuation.yield(event("session_ended", #"{"reason":"stopped","duration_seconds":12}"#))
        await waitUntil { model.displayState == .ended(summary: "All done.") }
        await waitUntil { dismissed }
        XCTAssertTrue(dismissed)
    }

    // MARK: - Session-active reconciliation

    func testSessionActiveTogglesOnFirstStateAndOnEnd() async {
        let (factory, continuation) = makeStream()
        var actives: [Bool] = []
        let model = makeModel(stream: factory)
        model.onSessionActiveChanged = { actives.append($0) }
        model.begin()

        continuation.yield(event("state", #"{"state":"connecting"}"#))
        await waitUntil { actives == [true] }
        continuation.yield(event("state", #"{"state":"listening"}"#))
        continuation.yield(event("session_ended", #"{"reason":"stopped"}"#))
        await waitUntil { actives == [true, false] }
    }

    // MARK: - Errors

    func testTerminalErrorEventShowsGenericError() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event(
            "error", #"{"code":"realtime","message":"OpenAI is down","terminal":true}"#
        ))
        await waitUntil { model.displayState == .error(.generic(message: "OpenAI is down")) }
    }

    func testNonTerminalErrorDoesNotChangeState() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("state", #"{"state":"listening"}"#))
        await waitUntil { model.displayState == .listening }
        continuation.yield(event(
            "error", #"{"code":"audio_warning","message":"buffer xrun","terminal":false}"#
        ))
        // Give it a beat; state must remain listening.
        try? await Task.sleep(for: .milliseconds(40))
        XCTAssertEqual(model.displayState, .listening)
    }

    func testDaemonUnreachableStartFailureMapsToDaemonNotRunning() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        control.startError = .daemonUnreachable(detail: "refused")
        let model = makeModel(control: control, stream: factory)
        model.begin()

        await waitUntil { model.displayState == .error(.daemonNotRunning) }
    }

    func testGenericStartFailureMapsToGenericError() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        control.startError = .server(code: "boom", message: "kaboom", status: 500)
        let model = makeModel(control: control, stream: factory)
        model.begin()

        await waitUntil {
            if case .error(.generic) = model.displayState { return true }
            return false
        }
    }

    func testMicrophoneDeniedSkipsStartAndShowsMicError() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        let model = VoiceSessionViewModel(
            voiceControl: control,
            endedDismissDelay: .milliseconds(40),
            micAuthorized: { false },
            eventStream: factory
        )
        model.begin()

        XCTAssertEqual(model.displayState, .error(.microphoneOff))
        // Give any (incorrect) start task a beat — none should have fired.
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(control.starts, 0)
    }

    func testReconnectingChipReflectsConnectionState() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("state", #"{"state":"speaking"}"#))
        await waitUntil { model.displayState == .speaking }
        XCTAssertEqual(model.connectionState, .live)

        continuation.yield(.disconnected(reason: "dropped"))
        await waitUntil { model.connectionState == .reconnecting }
        // The session state is preserved while reconnecting.
        XCTAssertEqual(model.displayState, .speaking)
        XCTAssertTrue(model.displayState.isActiveSession)

        continuation.yield(.connected)
        await waitUntil { model.connectionState == .live }
    }

    func testOverlayErrorForDaemonStateMapping() {
        typealias Mapped = VoiceSessionViewModel.OverlayError
        XCTAssertEqual(Mapped.forDaemonState(.crashLooping(stderrTail: "x")), .daemonFailing)
        XCTAssertEqual(Mapped.forDaemonState(.portConflict(reason: "x")), .portConflict)
        XCTAssertEqual(Mapped.forDaemonState(.tokenMismatch), .tokenMismatch)
        XCTAssertNil(Mapped.forDaemonState(.healthy(adopted: false)))
        XCTAssertNil(Mapped.forDaemonState(.stopped))
        XCTAssertNil(Mapped.forDaemonState(.launching))
    }

    func testPresentErrorCancelsPendingAutoDismiss() async {
        let (factory, continuation) = makeStream()
        var dismissed = false
        // Long dismiss delay so the cancel-before-fire is deterministic.
        let model = VoiceSessionViewModel(
            voiceControl: FakeVoiceControl(),
            endedDismissDelay: .milliseconds(300),
            eventStream: factory
        )
        model.onShouldDismiss = { dismissed = true }
        model.begin()

        continuation.yield(event("session_ended", #"{"reason":"stopped"}"#))
        await waitUntil {
            if case .ended = model.displayState { return true }
            return false
        }
        // Re-activation revokes-then-errors before the auto-dismiss fires.
        model.presentError(.microphoneOff)
        XCTAssertEqual(model.displayState, .error(.microphoneOff))

        // Past the original dismiss delay: it must not have fired.
        try? await Task.sleep(for: .milliseconds(400))
        XCTAssertFalse(dismissed)
        XCTAssertEqual(model.displayState, .error(.microphoneOff))
    }

    func testPresentErrorResetsToggleAndShowsError() {
        let (factory, _) = makeStream()
        var activationFailed = false
        let model = makeModel(stream: factory)
        model.onActivationFailed = { activationFailed = true }

        model.presentError(.daemonFailing)

        XCTAssertEqual(model.displayState, .error(.daemonFailing))
        XCTAssertTrue(activationFailed)
    }

    func testStartFailureResetsActivationToggle() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        control.startError = .daemonUnreachable(detail: "refused")
        var activationFailed = false
        let model = makeModel(control: control, stream: factory)
        model.onActivationFailed = { activationFailed = true }
        model.begin()

        await waitUntil { activationFailed }
        guard case .error = model.displayState else {
            return XCTFail("expected error state")
        }
    }

    func testCancelBeforeStartCompletesTearsDownTheLateSession() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        control.blockStart = true
        let model = makeModel(control: control, stream: factory)

        model.begin()
        await waitUntil { control.starts == 1 } // start is now in flight, blocked

        model.end() // user cancels before the start returns
        await waitUntil { control.stops == 1 } // end()'s stop

        control.releaseStart() // the late start finally creates a session
        // …which must be torn down because the user no longer wants it.
        await waitUntil { control.stops == 2 }
        XCTAssertEqual(control.stops, 2)
    }

    func testConflictOnStartIsAdoptedNotErrored() async {
        let (factory, continuation) = makeStream()
        let control = FakeVoiceControl()
        control.startError = .conflict(code: "voice_already_running", message: "x", session: nil)
        let model = makeModel(control: control, stream: factory)
        model.begin()

        // No error surfaced; the snapshot syncs us into the live session.
        continuation.yield(event("state", #"{"state":"speaking"}"#))
        await waitUntil { model.displayState == .speaking }
    }

    // MARK: - Reconnect

    func testReconnectSnapshotResyncsState() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event("state", #"{"state":"speaking"}"#))
        await waitUntil { model.displayState == .speaking }
        // Stream drops, then reconnects and replays the snapshot.
        continuation.yield(.disconnected(reason: "dropped"))
        continuation.yield(.connected)
        continuation.yield(event("state", #"{"state":"listening"}"#))
        await waitUntil { model.displayState == .listening }
    }

    // MARK: - Control wiring

    func testEndStopsTheSession() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        let model = makeModel(control: control, stream: factory)
        model.begin()
        await waitUntil { control.starts == 1 }

        model.end()
        await waitUntil { control.stops == 1 }
    }

    func testInterruptCallsControl() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        let model = makeModel(control: control, stream: factory)
        model.interrupt()
        await waitUntil { control.interrupts == 1 }
    }

    func testUnknownEventTypeIsIgnored() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()
        continuation.yield(event("state", #"{"state":"listening"}"#))
        await waitUntil { model.displayState == .listening }
        continuation.yield(event("brand_new_event", #"{"whatever":1}"#))
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(model.displayState, .listening)
    }
}
