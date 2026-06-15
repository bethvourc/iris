import IrisKit
import SwiftUI

/// Voice: wake words, the realtime voice, and the model — backed by
/// `GET/PUT /settings`. Env-overridden keys render disabled with a note.
struct VoicePane: View {
    let settings: SettingsViewModel

    private typealias Key = SettingsViewModel.Key

    var body: some View {
        Form {
            Section("Wake words") {
                SettingTextField(
                    title: "Phrases",
                    value: settings.stringList(Key.wakeWords).joined(separator: ", "),
                    prompt: "iris, hey iris",
                    enabled: settings.isMutable(Key.wakeWords),
                    error: settings.error(Key.wakeWords)
                ) { await settings.saveWakeWords($0) }
                Text("Comma-separated. Used to start a conversation hands-free.")
                    .font(DesignSystem.Typography.caption)
                    .foregroundStyle(DesignSystem.Colors.textTertiary)
            }

            Section("Realtime") {
                SettingTextField(
                    title: "Voice",
                    value: settings.stringValue(Key.voice),
                    prompt: "marin",
                    enabled: settings.isMutable(Key.voice),
                    error: settings.error(Key.voice)
                ) { await settings.saveVoice($0) }

                SettingTextField(
                    title: "Model",
                    value: settings.stringValue(Key.realtimeModel),
                    prompt: "gpt-realtime-2",
                    enabled: settings.isMutable(Key.realtimeModel),
                    error: settings.error(Key.realtimeModel)
                ) { await settings.saveRealtimeModel($0) }
            }

            Section("Always-on") {
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    Toggle("Listen for the wake word", isOn: wakeWordEnabledBinding)
                        .disabled(!settings.isMutable(Key.wakeWordEnabled))
                    Text("When on, the microphone is processed on-device to detect "
                        + "the wake word. The macOS mic indicator stays lit while active.")
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                    FieldFootnote(
                        error: settings.error(Key.wakeWordEnabled),
                        managed: settings.isEnvManaged(Key.wakeWordEnabled)
                    )
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
    }

    private var wakeWordEnabledBinding: Binding<Bool> {
        Binding(
            get: { settings.boolValue(Key.wakeWordEnabled) },
            set: { newValue in Task { await settings.saveWakeWordEnabled(newValue) } }
        )
    }
}
