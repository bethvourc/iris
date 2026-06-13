import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class VoiceSessionViewModelTests: XCTestCase {
    // MARK: - Fakes

    final class FakeVoiceControl: VoiceControlling, @unchecked Sendable {
        nonisolated(unsafe) var startError: IrisAPIError?
        nonisolated(unsafe) var starts = 0
        nonisolated(unsafe) var stops = 0
        nonisolated(unsafe) var interrupts = 0
        /// When set, startVoice blocks until `releaseStart()` — simulates an
        /// in-flight POST so the cancel race can be tested deterministically.
        nonisolated(unsafe) var blockStart = false
        private nonisolated(unsafe) var startGate: CheckedContinuation<Void, Never>?
        private let lock = NSLock()

        func releaseStart() {
            lock.withLock { startGate }?.resume()
            lock.withLock { startGate = nil }
        }

        func startVoice(mode _: String) async throws -> VoiceSessionDescriptor {
            lock.withLock { starts += 1 }
            if let startError { throw startError }
            if blockStart {
                await withCheckedContinuation { continuation in
                    lock.withLock { startGate = continuation }
                }
            }
            return try IrisJSON.decoder().decode(
                VoiceSessionDescriptor.self,
                from: Data(#"{"id":"v1","mode":"conversation","started_at":"2026-06-13T00:00:00Z"}"#.utf8)
            )
        }

        func stopVoice() async throws -> VoiceStopResponse {
            lock.withLock { stops += 1 }
            return VoiceStopResponse(ok: true, wasRunning: true)
        }

        func interruptVoice() async throws -> Bool {
            lock.withLock { interrupts += 1 }
            return true
        }
    }

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

    func testTerminalErrorEventShowsRetryableError() async {
        let (factory, continuation) = makeStream()
        let model = makeModel(stream: factory)
        model.begin()

        continuation.yield(event(
            "error", #"{"code":"realtime","message":"OpenAI is down","terminal":true}"#
        ))
        await waitUntil { model.displayState == .error(message: "OpenAI is down", retryable: true) }
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

    func testStartFailureSurfacesErrorState() async {
        let (factory, _) = makeStream()
        let control = FakeVoiceControl()
        control.startError = .daemonUnreachable(detail: "refused")
        let model = makeModel(control: control, stream: factory)
        model.begin()

        await waitUntil {
            if case .error = model.displayState { return true }
            return false
        }
        guard case let .error(_, retryable) = model.displayState else {
            return XCTFail("expected error state")
        }
        XCTAssertTrue(retryable)
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
