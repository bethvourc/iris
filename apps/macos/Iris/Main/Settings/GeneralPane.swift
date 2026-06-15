import IrisKit
import KeyboardShortcuts
import ServiceManagement
import SwiftUI

/// General: launch at login, the activation hotkey recorder, and the
/// notification provider.
struct GeneralPane: View {
    let settings: SettingsViewModel
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        Form {
            Section("Startup") {
                Toggle("Launch Iris at login", isOn: $launchAtLogin)
                    .onChange(of: launchAtLogin) { _, enabled in setLaunchAtLogin(enabled) }
            }

            Section("Hotkey") {
                LabeledContent("Talk to Iris") {
                    KeyboardShortcuts.Recorder(for: .talkToIris)
                }
            }

            Section("Notifications") {
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    Picker("Provider", selection: providerBinding) {
                        ForEach(SettingsViewModel.notifyProviders, id: \.self) { provider in
                            Text(provider.capitalized).tag(provider)
                        }
                    }
                    .disabled(!settings.isMutable(SettingsViewModel.Key.notifyProvider))
                    .frame(maxWidth: 280)
                    FieldFootnote(
                        error: settings.error(SettingsViewModel.Key.notifyProvider),
                        managed: settings.isEnvManaged(SettingsViewModel.Key.notifyProvider)
                    )
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
    }

    private var providerBinding: Binding<String> {
        Binding(
            get: { settings.stringValue(SettingsViewModel.Key.notifyProvider) },
            set: { newValue in Task { await settings.saveNotifyProvider(newValue) } }
        )
    }

    /// Register/unregister the login item; revert the toggle if the system
    /// rejects the change (e.g. the app isn't installed where it can persist).
    private func setLaunchAtLogin(_ enabled: Bool) {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }
}
