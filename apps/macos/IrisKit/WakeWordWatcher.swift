import Foundation

/// An always-on subscriber to `/voice/events` that reacts to the on-device wake
/// word while the app is otherwise idle.
///
/// The session view model only subscribes during a live session, so something
/// else has to be listening when nothing is happening — that's this. On a
/// `wake_detected` event it asks the app to open the overlay and start a session
/// (the same path the hotkey uses); from `state` snapshots it tracks whether the
/// daemon's mic loop is currently armed, so the menu bar can show it honestly.
///
/// It owns no audio and makes no detection decisions — all of that is on-device
/// in the daemon. This is purely the app-side reaction.
@MainActor
public final class WakeWordWatcher {
    private let eventStream: @Sendable () -> AsyncStream<SSEClientEvent>
    private let onWake: @MainActor () -> Void
    private let onListeningChanged: @MainActor (Bool) -> Void
    private var task: Task<Void, Never>?
    private var listening = false

    public init(
        eventStream: @escaping @Sendable () -> AsyncStream<SSEClientEvent>,
        onWake: @escaping @MainActor () -> Void,
        onListeningChanged: @escaping @MainActor (Bool) -> Void = { _ in }
    ) {
        self.eventStream = eventStream
        self.onWake = onWake
        self.onListeningChanged = onListeningChanged
    }

    /// Begin watching. Idempotent; the stream reconnects itself on drop.
    public func start() {
        guard task == nil else { return }
        task = Task { [weak self] in await self?.consume() }
    }

    /// Stop watching and report listening as off (the subscription is gone).
    public func stop() {
        task?.cancel()
        task = nil
        setListening(false)
    }

    private func consume() async {
        for await clientEvent in eventStream() {
            if Task.isCancelled { return }
            switch clientEvent {
            case let .event(event):
                handle(event)
            case .disconnected:
                // Can't know the mic state while disconnected; report off.
                setListening(false)
            case .connected, .heartbeat:
                break
            }
        }
    }

    private func handle(_ event: SSEEvent) {
        switch event.type {
        case "wake_detected":
            onWake()
        case "state":
            if let payload = try? event.decode(WakeStatePayload.self) {
                setListening(payload.wakeListening ?? false)
            }
        default:
            break
        }
    }

    private func setListening(_ value: Bool) {
        guard value != listening else { return }
        listening = value
        onListeningChanged(value)
    }
}

/// The `wake_listening` flag rides on every `state` snapshot; everything else in
/// the payload is consumed by the session view model.
private struct WakeStatePayload: Decodable {
    let wakeListening: Bool?

    enum CodingKeys: String, CodingKey {
        case wakeListening = "wake_listening"
    }
}
