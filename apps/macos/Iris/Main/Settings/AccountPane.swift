import IrisKit
import SwiftUI

/// Account: the OpenAI key (presence + replace) and gateway-token rotation.
/// Secrets never round-trip to the UI — only `is_set` is known, and the field
/// is write-only.
struct AccountPane: View {
    let settings: SettingsViewModel
    let onRotateToken: () -> Void

    @State private var newKey = ""
    @State private var rotateConfirm = false

    private let openAIKey = SettingsViewModel.Key.openAIKey

    var body: some View {
        Form {
            Section("OpenAI API key") {
                LabeledContent("Status") {
                    Text(settings.isSecretSet(openAIKey) ? "Set" : "Not set")
                        .foregroundStyle(settings.isSecretSet(openAIKey)
                            ? DesignSystem.Colors.textSecondary
                            : DesignSystem.Colors.rust)
                }
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    LabeledContent(settings.isSecretSet(openAIKey) ? "Replace" : "Set key") {
                        HStack(spacing: DesignSystem.Spacing.sm) {
                            SecureField("sk-…", text: $newKey)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 240)
                            Button("Save") {
                                Task {
                                    await settings.saveSecret(openAIKey, newKey)
                                    if settings.error(openAIKey) == nil { newKey = "" }
                                }
                            }
                            .disabled(newKey.isEmpty || settings.isSaving(openAIKey))
                        }
                    }
                    FieldFootnote(error: settings.error(openAIKey))
                    Text("The key is stored by the daemon and never shown again.")
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
            }

            Section("Gateway token") {
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    if rotateConfirm {
                        Text("Rotating restarts Iris with a new token. Continue?")
                            .font(DesignSystem.Typography.callout)
                            .foregroundStyle(DesignSystem.Colors.textSecondary)
                        HStack(spacing: DesignSystem.Spacing.sm) {
                            Button("Cancel") { rotateConfirm = false }
                            Button("Rotate token", role: .destructive) {
                                rotateConfirm = false
                                onRotateToken()
                            }
                        }
                    } else {
                        Button("Rotate token…") { rotateConfirm = true }
                        Text("Generates a new loopback token and reconnects the daemon.")
                            .font(DesignSystem.Typography.caption)
                            .foregroundStyle(DesignSystem.Colors.textTertiary)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
    }
}
