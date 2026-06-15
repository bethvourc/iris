import Foundation
import IrisKit

/// Deterministic `SettingsProviding` for screenshots and UI tests, selected via
/// `--ui-test-settings <scenario>`. Decodes canned JSON through the real
/// contract decoder. The `fielderror` scenario fails updates so an inline
/// field error can be captured.
struct ScriptedSettingsProvider: SettingsProviding {
    let scenario: String

    func settings() async throws -> SettingsResponse {
        if scenario == "error" {
            throw IrisAPIError.daemonUnreachable(detail: "no daemon")
        }
        return try Self.decode(Self.settingsJSON)
    }

    func updateSettings(_: [String: JSONValue]) async throws -> SettingsResponse {
        if scenario == "fielderror" {
            throw IrisAPIError.invalidRequest(code: "invalid", message: "Voice isn't available.")
        }
        return try Self.decode(Self.settingsJSON)
    }

    func updateSecrets(_: [String: String]) async throws -> SettingsResponse {
        try Self.decode(Self.settingsJSON)
    }

    private static func decode(_ json: String) throws -> SettingsResponse {
        try IrisJSON.decoder().decode(SettingsResponse.self, from: Data(json.utf8))
    }

    private static let settingsJSON = """
    {
      "settings": {
        "wake_words": {"value": ["iris", "hey iris"], "source": "settings", "mutable": true},
        "voice": {"value": "marin", "source": "settings", "mutable": true},
        "realtime_model": {"value": "gpt-realtime-2", "source": "env", "mutable": false},
        "notify_provider": {"value": "pushover", "source": "settings", "mutable": true},
        "wake_word_enabled": {"value": false, "source": "settings", "mutable": true}
      },
      "secrets": {
        "openai_api_key": {"is_set": true},
        "groq_api_key": {"is_set": false}
      }
    }
    """
}
