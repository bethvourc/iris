import Foundation
import XCTest
@testable import IrisKit

@MainActor
final class SettingsViewModelTests: XCTestCase {
    // MARK: - Fixtures

    private func entry(_ value: JSONValue, source: SettingSource = .settings, mutable: Bool = true) -> SettingEntry {
        SettingEntry(value: value, source: source, mutable: mutable)
    }

    private func defaultResponse() -> SettingsResponse {
        SettingsResponse(
            settings: [
                "wake_words": entry(.array([.string("iris"), .string("hey iris")])),
                "voice": entry(.string("marin")),
                "realtime_model": entry(.string("gpt-realtime-2"), source: .env, mutable: false),
                "notify_provider": entry(.string("pushover")),
                "wake_word_enabled": entry(.bool(false))
            ],
            secrets: [
                "openai_api_key": SecretStatus(isSet: true),
                "groq_api_key": SecretStatus(isSet: false)
            ]
        )
    }

    private func model(_ response: SettingsResponse? = nil) -> (SettingsViewModel, FakeSettingsProvider) {
        let provider = FakeSettingsProvider(response ?? defaultResponse())
        return (SettingsViewModel(provider: provider), provider)
    }

    // MARK: - Load

    func testStartsLoading() {
        let (vm, _) = model()
        XCTAssertEqual(vm.state, .loading)
    }

    func testLoadPopulatesSettingsAndSecrets() async {
        let (vm, _) = model()
        await vm.load()
        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.stringValue("voice"), "marin")
        XCTAssertEqual(vm.stringList("wake_words"), ["iris", "hey iris"])
        XCTAssertFalse(vm.boolValue("wake_word_enabled"))
        XCTAssertTrue(vm.isSecretSet("openai_api_key"))
        XCTAssertFalse(vm.isSecretSet("groq_api_key"))
    }

    func testEnvManagedKeyIsImmutable() async {
        let (vm, _) = model()
        await vm.load()
        XCTAssertTrue(vm.isEnvManaged("realtime_model"))
        XCTAssertFalse(vm.isMutable("realtime_model"))
        XCTAssertTrue(vm.isMutable("voice"))
    }

    func testLoadFailureSurfacesError() async {
        let (vm, provider) = model()
        await provider.setFailLoad(true)
        await vm.load()
        XCTAssertEqual(vm.state, .failed(message: "Can't reach Iris right now."))
    }

    // MARK: - Save-on-change

    func testSaveVoiceUpdatesValue() async {
        let (vm, provider) = model()
        await vm.load()
        await vm.saveVoice("cedar")
        XCTAssertEqual(vm.stringValue("voice"), "cedar")
        let updates = await provider.settingsUpdates
        XCTAssertEqual(updates, [["voice": .string("cedar")]])
    }

    func testSaveVoiceTrimsAndRejectsEmpty() async {
        let (vm, provider) = model()
        await vm.load()
        await vm.saveVoice("   ")
        XCTAssertEqual(vm.error("voice"), "Voice can't be empty.")
        let updates = await provider.settingsUpdates
        XCTAssertTrue(updates.isEmpty, "no request for an invalid value")
    }

    func testSaveEnvManagedKeyIsNoOp() async {
        let (vm, provider) = model()
        await vm.load()
        await vm.saveRealtimeModel("gpt-x")
        let updates = await provider.settingsUpdates
        XCTAssertTrue(updates.isEmpty, "env-managed keys are not editable")
    }

    func testSaveFailureSetsFieldError() async {
        let (vm, provider) = model()
        await vm.load()
        await provider.setFailUpdates(IrisAPIError.invalidRequest(code: "bad", message: "nope"))
        await vm.saveVoice("cedar")
        XCTAssertEqual(vm.error("voice"), "nope")
    }

    func testSaveWakeWordEnabledTogglesBool() async {
        let (vm, _) = model()
        await vm.load()
        await vm.saveWakeWordEnabled(true)
        XCTAssertTrue(vm.boolValue("wake_word_enabled"))
    }

    // MARK: - Wake words validation

    func testParseWakeWordsSplitsAndTrims() {
        XCTAssertEqual(
            SettingsViewModel.parseWakeWords("iris, hey iris\ncomputer"),
            .valid(["iris", "hey iris", "computer"])
        )
    }

    func testParseWakeWordsRejectsEmpty() {
        if case .valid = SettingsViewModel.parseWakeWords("  ,  \n ") {
            XCTFail("expected failure for empty input")
        }
    }

    func testSaveWakeWordsInvalidSetsFieldErrorOnly() async {
        let (vm, provider) = model()
        await vm.load()
        await vm.saveWakeWords("   ")
        XCTAssertEqual(vm.error("wake_words"), "Add at least one wake word.")
        let updates = await provider.settingsUpdates
        XCTAssertTrue(updates.isEmpty)
    }

    // MARK: - Secrets

    func testSaveSecretMarksItSet() async {
        let response = SettingsResponse(
            settings: defaultResponse().settings,
            secrets: ["openai_api_key": SecretStatus(isSet: false)]
        )
        let (vm, provider) = model(response)
        await vm.load()
        XCTAssertFalse(vm.isSecretSet("openai_api_key"))

        await vm.saveSecret("openai_api_key", "sk-test-123456789")

        XCTAssertTrue(vm.isSecretSet("openai_api_key"))
        let updates = await provider.secretUpdates
        XCTAssertEqual(updates.first?["openai_api_key"], "sk-test-123456789")
    }

    func testSaveSecretRejectsEmpty() async {
        let (vm, provider) = model()
        await vm.load()
        await vm.saveSecret("openai_api_key", "  ")
        XCTAssertEqual(vm.error("openai_api_key"), "Enter a value.")
        let updates = await provider.secretUpdates
        XCTAssertTrue(updates.isEmpty)
    }
}

/// Scripted `SettingsProviding`. An actor so it's `Sendable`; reflects updates
/// back into its response so the view model's optimistic state can be checked.
private actor FakeSettingsProvider: SettingsProviding {
    private var response: SettingsResponse
    private var failLoad = false
    private var updateError: Error?
    private(set) var settingsUpdates: [[String: JSONValue]] = []
    private(set) var secretUpdates: [[String: String]] = []

    init(_ response: SettingsResponse) {
        self.response = response
    }

    func setFailLoad(_ value: Bool) { failLoad = value }
    func setFailUpdates(_ error: Error) { updateError = error }

    func settings() async throws -> SettingsResponse {
        if failLoad { throw IrisAPIError.daemonUnreachable(detail: "x") }
        return response
    }

    func updateSettings(_ changes: [String: JSONValue]) async throws -> SettingsResponse {
        settingsUpdates.append(changes)
        if let updateError { throw updateError }
        var merged = response.settings
        for (key, value) in changes {
            let existing = merged[key]
            merged[key] = SettingEntry(value: value, source: existing?.source ?? .settings, mutable: existing?.mutable ?? true)
        }
        response = SettingsResponse(settings: merged, secrets: response.secrets)
        return response
    }

    func updateSecrets(_ changes: [String: String]) async throws -> SettingsResponse {
        secretUpdates.append(changes)
        if let updateError { throw updateError }
        var merged = response.secrets
        for key in changes.keys { merged[key] = SecretStatus(isSet: true) }
        response = SettingsResponse(settings: response.settings, secrets: merged)
        return response
    }
}
