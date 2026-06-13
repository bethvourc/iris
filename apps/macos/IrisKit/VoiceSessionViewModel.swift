import Foundation
import Observation

/// Voice control surface the overlay needs; `APIClient` conforms. A
/// protocol so the view model is unit-testable with a scripted fake.
public protocol VoiceControlling: Sendable {
    func startVoice(mode: String) async throws -> VoiceSessionDescriptor
    func stopVoice() async throws -> VoiceStopResponse
    func interruptVoice() async throws -> Bool
}

extension APIClient: VoiceControlling {}

/// Drives a live voice session and renders it: starts/stops/interrupts via
/// the daemon, consumes the `/voice/events` SSE stream, and folds the event
/// catalog (api-contract §4) into one `DisplayState` the overlay shows.
///
/// Snapshot-on-connect makes this reconnect-safe: the first frame after any
/// (re)connection is a `state` event that re-syncs the rendered state.
@MainActor
@Observable
public final class VoiceSessionViewModel {
    public enum DisplayState: Equatable, Sendable {
        case connecting
        case listening
        case userSpeaking
        case thinking
        case speaking
        /// Transient: barge-in cut. The daemon always follows with a state
        /// event, so no timer is needed to leave it.
        case interrupted
        case meeting
        case ended(summary: String?)
        case error(message: String, retryable: Bool)
    }

    public private(set) var displayState: DisplayState = .connecting
    public private(set) var userText = ""
    public private(set) var assistantText = ""
    /// Secondary line for tool/agent activity during `thinking`.
    public private(set) var statusLine: String?

    /// Reconcile the activation toggle with session truth (Step 4.1).
    public var onSessionActiveChanged: @MainActor (Bool) -> Void = { _ in }
    /// The session ended; the overlay should dismiss itself.
    public var onShouldDismiss: @MainActor () -> Void = {}
    /// A start request failed before any session existed — reset the
    /// activation toggle to idle (distinct from `onSessionActiveChanged`,
    /// whose `false` deliberately preserves `.pending`).
    public var onActivationFailed: @MainActor () -> Void = {}

    private let voiceControl: any VoiceControlling
    private let eventStream: @Sendable () -> AsyncStream<SSEClientEvent>
    private let endedDismissDelay: Duration
    private var streamTask: Task<Void, Never>?
    private var dismissTask: Task<Void, Never>?
    private var sessionActive = false
    /// True between `begin()` and `end()`. A start that completes after the
    /// user has cancelled (`false`) is torn down to avoid an orphaned,
    /// invisibly-live session.
    private var wantsSession = false

    public init(
        voiceControl: any VoiceControlling,
        endedDismissDelay: Duration = .seconds(2),
        eventStream: @escaping @Sendable () -> AsyncStream<SSEClientEvent>
    ) {
        self.voiceControl = voiceControl
        self.endedDismissDelay = endedDismissDelay
        self.eventStream = eventStream
    }

    // MARK: - Lifecycle

    /// Begin a session: open the event stream, then request a start. Stream
    /// first so the snapshot and early events are never missed.
    public func begin() {
        dismissTask?.cancel()
        dismissTask = nil
        resetTurn()
        wantsSession = true
        displayState = .connecting
        if streamTask == nil {
            streamTask = Task { [weak self] in await self?.consume() }
        }
        Task { [weak self] in await self?.requestStart() }
    }

    /// End the session and tear down the stream (hotkey deactivate / Esc).
    public func end() {
        wantsSession = false
        streamTask?.cancel()
        streamTask = nil
        dismissTask?.cancel()
        dismissTask = nil
        setSessionActive(false)
        // Idempotent: also covers a start still in flight — that start's
        // completion will issue its own stop once it knows it's unwanted.
        Task { [voiceControl] in _ = try? await voiceControl.stopVoice() }
    }

    /// Barge-in without speech (tap-to-interrupt). Voice barge-in is handled
    /// by the daemon and arrives as an `interrupted` event.
    public func interrupt() {
        Task { [voiceControl] in _ = try? await voiceControl.interruptVoice() }
    }

    public func retry() {
        begin()
    }

    /// Preview/screenshot/test hook: pin a rendered state without networking.
    public func forceState(
        _ state: DisplayState, userText: String = "", assistantText: String = "",
        statusLine: String? = nil
    ) {
        displayState = state
        self.userText = userText
        self.assistantText = assistantText
        self.statusLine = statusLine
    }

    // MARK: - Start

    private func requestStart() async {
        do {
            _ = try await voiceControl.startVoice(mode: "conversation")
        } catch let error as IrisAPIError {
            // 409: a session is already live — the SSE snapshot syncs us.
            // (Step 4.4 refines adoption UX.)
            if case .conflict = error { return }
            if wantsSession { presentStartError(error) }
            return
        } catch {
            if wantsSession { presentStartError(error) }
            return
        }
        // Start succeeded. If the user cancelled while it was in flight, the
        // daemon now has a live session with no UI — tear it down.
        if !wantsSession {
            Task { [voiceControl] in _ = try? await voiceControl.stopVoice() }
        }
    }

    private func presentStartError(_ error: Error) {
        let message = (error as? IrisAPIError)?.errorDescription
            ?? "Couldn't start Iris."
        displayState = .error(message: message, retryable: true)
        setSessionActive(false)
        onActivationFailed() // reset the toggle from .pending to .idle
    }

    // MARK: - Event consumption

    private func consume() async {
        for await clientEvent in eventStream() {
            if Task.isCancelled { return }
            switch clientEvent {
            case let .event(event):
                handle(event)
            case .connected, .disconnected, .heartbeat:
                break // Step 4.4 renders reconnecting/staleness.
            }
        }
    }

    /// Dispatch table keeps `handle` flat; unknown types are ignored.
    private static let handlers:
        [String: (VoiceSessionViewModel, SSEEvent) -> Void] = [
            "state": { $0.applyState($1) },
            "user_transcript": { $0.applyUserTranscript($1) },
            "assistant_delta": { $0.applyAssistantDelta($1) },
            "assistant_done": { $0.applyAssistantDone($1) },
            "interrupted": { model, _ in model.displayState = .interrupted },
            "tool_call": { $0.applyToolCall($1) },
            "agent_run": { $0.applyAgentRun($1) },
            "session_ended": { model, _ in model.applySessionEnded() },
            "error": { $0.applyError($1) }
        ]

    private func handle(_ event: SSEEvent) {
        Self.handlers[event.type]?(self, event)
    }

    /// Daemon VoiceState → rendered state. Absent keys (idle/error/unknown)
    /// are handled out-of-band in `applyState`.
    private static let stateMap: [VoiceState: DisplayState] = [
        .connecting: .connecting,
        .listening: .listening,
        .userSpeaking: .userSpeaking,
        .transcribing: .userSpeaking,
        .thinking: .thinking,
        .speaking: .speaking,
        .meeting: .meeting
    ]

    private func applyState(_ event: SSEEvent) {
        guard let payload = try? event.decode(StatePayload.self) else { return }
        if let display = Self.stateMap[payload.state] {
            displayState = display
            setSessionActive(true)
        } else if payload.state == .idle, sessionActive {
            // Defensive: session over without an explicit session_ended.
            setSessionActive(false)
        }
        // .error carries its message via the error event; .unknown is ignored.
    }

    private func applyUserTranscript(_ event: SSEEvent) {
        guard let payload = try? event.decode(TextPayload.self) else { return }
        assistantText = "" // a new user turn
        userText = payload.text
    }

    private func applyAssistantDelta(_ event: SSEEvent) {
        guard let payload = try? event.decode(TextPayload.self) else { return }
        assistantText += payload.text
        displayState = .speaking
        setSessionActive(true)
    }

    private func applyAssistantDone(_ event: SSEEvent) {
        guard let payload = try? event.decode(TextPayload.self), !payload.text.isEmpty
        else { return }
        assistantText = payload.text
    }

    private func applyToolCall(_ event: SSEEvent) {
        guard let payload = try? event.decode(ToolCallPayload.self) else { return }
        statusLine = payload.status == "started" ? payload.name : nil
    }

    private func applyAgentRun(_ event: SSEEvent) {
        guard let payload = try? event.decode(AgentRunPayload.self) else { return }
        statusLine = payload.status == "started" ? (payload.summary ?? "Working…") : nil
    }

    private func applySessionEnded() {
        displayState = .ended(summary: assistantText.isEmpty ? nil : assistantText)
        setSessionActive(false)
        scheduleDismiss()
    }

    private func applyError(_ event: SSEEvent) {
        guard let payload = try? event.decode(ErrorPayload.self), payload.terminal
        else { return }
        displayState = .error(message: payload.message, retryable: true)
        setSessionActive(false)
    }

    // MARK: - Helpers

    private func resetTurn() {
        userText = ""
        assistantText = ""
        statusLine = nil
    }

    private func setSessionActive(_ active: Bool) {
        guard active != sessionActive else { return }
        sessionActive = active
        onSessionActiveChanged(active)
    }

    private func scheduleDismiss() {
        dismissTask?.cancel()
        dismissTask = Task { [weak self, endedDismissDelay] in
            try? await Task.sleep(for: endedDismissDelay)
            guard !Task.isCancelled else { return }
            self?.onShouldDismiss()
        }
    }
}

// MARK: - Event payloads

private struct StatePayload: Decodable {
    let state: VoiceState
}

private struct TextPayload: Decodable {
    let text: String
}

private struct ToolCallPayload: Decodable {
    let name: String
    let status: String
}

private struct AgentRunPayload: Decodable {
    let summary: String?
    let status: String
}

private struct ErrorPayload: Decodable {
    let code: String
    let message: String
    let terminal: Bool
}
