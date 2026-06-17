import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class WakeWordWatcherTests: XCTestCase {
    private func makeStream() -> (
        @Sendable () -> AsyncStream<SSEClientEvent>,
        AsyncStream<SSEClientEvent>.Continuation
    ) {
        let (stream, continuation) = AsyncStream.makeStream(of: SSEClientEvent.self)
        return ({ stream }, continuation)
    }

    private func event(_ type: String, _ json: String) -> SSEClientEvent {
        .event(SSEEvent(type: type, data: json))
    }

    private func waitUntil(
        timeout: Duration = .seconds(2), _ predicate: @escaping () -> Bool
    ) async {
        let deadline = ContinuousClock.now + timeout
        while ContinuousClock.now < deadline {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    func testWakeDetectedTriggersCallback() async {
        let (factory, continuation) = makeStream()
        var wakes = 0
        let watcher = WakeWordWatcher(eventStream: factory, onWake: { wakes += 1 })
        watcher.start()

        continuation.yield(event("wake_detected", #"{"model":"hey_iris","score":0.92}"#))
        await waitUntil { wakes == 1 }
        XCTAssertEqual(wakes, 1)
        watcher.stop()
    }

    func testStateSnapshotTracksListeningFlag() async {
        let (factory, continuation) = makeStream()
        var listening: [Bool] = []
        let watcher = WakeWordWatcher(
            eventStream: factory,
            onWake: {},
            onListeningChanged: { listening.append($0) }
        )
        watcher.start()

        continuation.yield(event("state", #"{"state":"idle","wake_listening":true}"#))
        await waitUntil { listening.last == true }

        // Repeats of the same value don't re-notify.
        continuation.yield(event("state", #"{"state":"idle","wake_listening":true}"#))
        continuation.yield(event("state", #"{"state":"idle","wake_listening":false}"#))
        await waitUntil { listening.last == false }

        XCTAssertEqual(listening, [true, false])
        watcher.stop()
    }

    func testDisconnectReportsListeningOff() async {
        let (factory, continuation) = makeStream()
        var listening: [Bool] = []
        let watcher = WakeWordWatcher(
            eventStream: factory,
            onWake: {},
            onListeningChanged: { listening.append($0) }
        )
        watcher.start()

        continuation.yield(event("state", #"{"state":"idle","wake_listening":true}"#))
        await waitUntil { listening.last == true }
        continuation.yield(.disconnected(reason: "drop"))
        await waitUntil { listening.last == false }

        XCTAssertEqual(listening, [true, false])
        watcher.stop()
    }

    func testMissingFlagIsTreatedAsNotListening() async {
        let (factory, continuation) = makeStream()
        var listening: [Bool] = []
        let watcher = WakeWordWatcher(
            eventStream: factory,
            onWake: {},
            onListeningChanged: { listening.append($0) }
        )
        watcher.start()
        // A pre-wake-word daemon omits the flag entirely → stays off (no
        // notification, since false is the initial value).
        continuation.yield(event("state", #"{"state":"idle"}"#))
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(listening, [])
        watcher.stop()
    }
}
