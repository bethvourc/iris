import Foundation
import Observation

/// What Settings needs from the daemon — `APIClient` conforms. A protocol so
/// the view model is unit-testable with a scripted provider.
public protocol SettingsProviding: Sendable {
    func settings() async throws -> SettingsResponse
    func updateSettings(_ changes: [String: JSONValue]) async throws -> SettingsResponse
    func updateSecrets(_ changes: [String: String]) async throws -> SettingsResponse
}

extension APIClient: SettingsProviding {}

/// Drives the Settings panes backed by `GET/PUT /settings` and `PUT /secrets`.
///
/// Two rules shape this model: **save-on-change** (each field commits on edit,
/// surfacing inline per-field errors — never a toast or an OK button), and
/// **secrets never round-trip to the UI** (only presence, `is_set`, is known).
/// Env-managed keys arrive `mutable: false` and are shown disabled.
@MainActor
@Observable
public final class SettingsViewModel {
    public enum State: Equatable, Sendable {
        case loading
        case loaded
        case failed(message: String)
    }

    /// Known keys, centralized so the panes and the model agree.
    public enum Key {
        public static let wakeWords = "wake_words"
        public static let voice = "voice"
        public static let realtimeModel = "realtime_model"
        public static let notifyProvider = "notify_provider"
        public static let wakeWordEnabled = "wake_word_enabled"
        public static let openAIKey = "openai_api_key"
    }

    public static let notifyProviders = ["pushover", "ntfy", "none"]

    public private(set) var state: State = .loading
    public private(set) var settings: [String: SettingEntry] = [:]
    public private(set) var secrets: [String: SecretStatus] = [:]
    /// Inline, per-field validation/save errors (keyed by setting/secret key).
    public private(set) var fieldErrors: [String: String] = [:]
    /// Keys with an in-flight save (drives a per-field spinner).
    public private(set) var savingKeys: Set<String> = []

    private let provider: any SettingsProviding
    private var hasLoaded = false

    public init(provider: any SettingsProviding) {
        self.provider = provider
    }

    public func load() async {
        if !hasLoaded { state = .loading }
        do {
            try await apply(provider.settings())
            state = .loaded
            hasLoaded = true
        } catch {
            if !hasLoaded { state = .failed(message: Self.message(for: error)) }
        }
    }

    // MARK: - Accessors

    public func entry(_ key: String) -> SettingEntry? {
        settings[key]
    }

    /// Env-managed keys come back `mutable: false`; default true if unknown.
    public func isMutable(_ key: String) -> Bool {
        settings[key]?.mutable ?? true
    }

    public func isEnvManaged(_ key: String) -> Bool {
        settings[key]?.source == .env
    }

    public func stringValue(_ key: String) -> String {
        if case let .string(value)? = settings[key]?.value { return value }
        return ""
    }

    public func boolValue(_ key: String) -> Bool {
        if case let .bool(value)? = settings[key]?.value { return value }
        return false
    }

    public func stringList(_ key: String) -> [String] {
        guard case let .array(items)? = settings[key]?.value else { return [] }
        return items.compactMap { if case let .string(value) = $0 { value } else { nil } }
    }

    public func error(_ key: String) -> String? {
        fieldErrors[key]
    }

    public func isSaving(_ key: String) -> Bool {
        savingKeys.contains(key)
    }

    public func isSecretSet(_ key: String) -> Bool {
        secrets[key]?.isSet ?? false
    }

    // MARK: - Typed savers (save-on-change)

    public func saveWakeWords(_ raw: String) async {
        switch Self.parseWakeWords(raw) {
        case let .valid(list):
            await save(Key.wakeWords, .array(list.map(JSONValue.string)))
        case let .invalid(message):
            fieldErrors[Key.wakeWords] = message
        }
    }

    public func saveVoice(_ value: String) async {
        await saveNonEmptyString(Key.voice, value, label: "Voice")
    }

    public func saveRealtimeModel(_ value: String) async {
        await saveNonEmptyString(Key.realtimeModel, value, label: "Model")
    }

    public func saveNotifyProvider(_ value: String) async {
        await save(Key.notifyProvider, .string(value))
    }

    public func saveWakeWordEnabled(_ value: Bool) async {
        await save(Key.wakeWordEnabled, .bool(value))
    }

    /// Persist a secret (e.g. the OpenAI key). The response only ever reports
    /// presence, so the field can be cleared right after.
    public func saveSecret(_ key: String, _ value: String) async {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            fieldErrors[key] = "Enter a value."
            return
        }
        fieldErrors[key] = nil
        savingKeys.insert(key)
        defer { savingKeys.remove(key) }
        do {
            try await apply(provider.updateSecrets([key: trimmed]))
        } catch {
            fieldErrors[key] = Self.message(for: error)
        }
    }

    // MARK: - Save plumbing

    private func saveNonEmptyString(_ key: String, _ value: String, label: String) async {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else {
            fieldErrors[key] = "\(label) can't be empty."
            return
        }
        await save(key, .string(trimmed))
    }

    /// Optimistic, all-or-nothing save of one key. On failure the prior value
    /// stays put and the error renders inline beside the field.
    public func save(_ key: String, _ value: JSONValue) async {
        guard isMutable(key) else { return } // env-managed: not editable
        fieldErrors[key] = nil
        savingKeys.insert(key)
        defer { savingKeys.remove(key) }
        do {
            try await apply(provider.updateSettings([key: value]))
        } catch {
            fieldErrors[key] = Self.message(for: error)
        }
    }

    private func apply(_ response: SettingsResponse) {
        settings = response.settings
        secrets = response.secrets
    }

    // MARK: - Validation (testable, mirrors the daemon's rules)

    /// Outcome of parsing the wake-words field (carries an inline message on
    /// failure — `Result` can't, since its `Failure` must be an `Error`).
    public enum WakeWordsParse: Equatable, Sendable {
        case valid([String])
        case invalid(String)
    }

    /// Wake words are entered as a comma/newline separated string; at least one
    /// non-empty entry is required.
    public static func parseWakeWords(_ raw: String) -> WakeWordsParse {
        let parts = raw
            .split(whereSeparator: { $0 == "," || $0.isNewline })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !parts.isEmpty else {
            return .invalid("Add at least one wake word.")
        }
        return .valid(parts)
    }

    /// A human-readable, secret-free dump of the current settings for the
    /// diagnostics bundle. Secrets contribute presence only (never values).
    public func diagnosticsSummary() -> String {
        var lines = ["Settings:"]
        for (key, entry) in settings.sorted(by: { $0.key < $1.key }) {
            lines.append("  \(key) = \(Self.describe(entry.value)) [\(entry.source.rawValue)]")
        }
        lines.append("Secrets:")
        for (key, status) in secrets.sorted(by: { $0.key < $1.key }) {
            lines.append("  \(key): \(status.isSet ? "set" : "not set")")
        }
        return lines.joined(separator: "\n")
    }

    private static func describe(_ value: JSONValue) -> String {
        switch value {
        case let .string(string): string
        case let .bool(bool): String(bool)
        case let .number(number): String(number)
        case let .array(items): items.map(describe).joined(separator: ", ")
        case .object, .null: "—"
        }
    }

    static func message(for error: Error) -> String {
        switch error {
        case IrisAPIError.daemonUnreachable:
            "Can't reach Iris right now."
        case IrisAPIError.unauthorized:
            "Iris rejected the connection token."
        default:
            (error as? LocalizedError)?.errorDescription ?? "Something went wrong."
        }
    }
}
