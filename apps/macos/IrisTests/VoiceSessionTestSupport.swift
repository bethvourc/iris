import Foundation
@testable import IrisKit

/// Scriptable voice control for VoiceSessionViewModel tests. Thread-safe:
/// the view-model actor and the test drive it from different executors.
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
