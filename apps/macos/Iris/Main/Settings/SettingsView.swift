import IrisKit
import SwiftUI

/// Settings: four panes backed by `GET/PUT /settings` and local preferences.
/// Save-on-change with inline per-field errors — no Save button, no toasts.
enum SettingsTab: String {
    case general, voice, account, advanced

    /// `--ui-test-settings-tab <name>` opens straight to a pane for screenshots.
    static var launchTab: SettingsTab? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--ui-test-settings-tab"),
              arguments.indices.contains(index + 1) else { return nil }
        return SettingsTab(rawValue: arguments[index + 1])
    }
}

struct SettingsView: View {
    let model: AppModel
    @State private var settings: SettingsViewModel
    @State private var tab: SettingsTab

    init(model: AppModel) {
        self.model = model
        _settings = State(initialValue: model.makeSettingsModel())
        _tab = State(initialValue: SettingsTab.launchTab ?? .general)
    }

    var body: some View {
        Group {
            switch settings.state {
            case .loading:
                ProgressView()
                    .controlSize(.small)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case let .failed(message):
                SettingsLoadError(message: message) { Task { await settings.load() } }
            case .loaded:
                TabView(selection: $tab) {
                    GeneralPane(settings: settings)
                        .tabItem { Label("General", systemImage: "gearshape") }
                        .tag(SettingsTab.general)
                    VoicePane(settings: settings)
                        .tabItem { Label("Voice", systemImage: "waveform") }
                        .tag(SettingsTab.voice)
                    AccountPane(settings: settings) { model.rotateGatewayToken() }
                        .tabItem { Label("Account", systemImage: "key") }
                        .tag(SettingsTab.account)
                    AdvancedPane(model: model, settings: settings)
                        .tabItem { Label("Advanced", systemImage: "slider.horizontal.3") }
                        .tag(SettingsTab.advanced)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(DesignSystem.Colors.canvas)
        .accessibilityIdentifier("section-settings")
        .task { await settings.load() }
    }
}

// MARK: - Shared field controls

/// A text field that commits on Enter or when focus leaves (save-on-change),
/// reflecting the model's value and any inline error or env-managed note.
struct SettingTextField: View {
    let title: String
    let value: String
    var prompt: String = ""
    var enabled = true
    var error: String?
    /// A compact, fixed field width — settings values are short.
    var fieldWidth: CGFloat = 200
    let onCommit: (String) async -> Void

    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
            HStack(spacing: DesignSystem.Spacing.md) {
                Text(title)
                    .foregroundStyle(DesignSystem.Colors.textPrimary)
                Spacer(minLength: DesignSystem.Spacing.md)
                TextField("", text: $text, prompt: prompt.isEmpty ? nil : Text(prompt))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .frame(width: fieldWidth)
                    .disabled(!enabled)
                    .focused($focused)
                    .onSubmit(commit)
                    .onChange(of: focused) { _, isFocused in if !isFocused { commit() } }
            }
            FieldFootnote(error: error, managed: !enabled)
        }
        .onChange(of: value, initial: true) { _, newValue in
            if !focused { text = newValue }
        }
    }

    private func commit() {
        guard text != value else { return }
        Task { await onCommit(text) }
    }
}

/// The inline note under a field: an error in rust, or the env-managed hint.
struct FieldFootnote: View {
    var error: String?
    var managed = false

    var body: some View {
        if let error {
            Text(error)
                .font(DesignSystem.Typography.caption)
                .foregroundStyle(DesignSystem.Colors.rust)
        } else if managed {
            Text("Managed by environment")
                .font(DesignSystem.Typography.caption)
                .foregroundStyle(DesignSystem.Colors.textTertiary)
        }
    }
}

private struct SettingsLoadError: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: DesignSystem.Spacing.md) {
            Text(message)
                .font(DesignSystem.Typography.body)
                .foregroundStyle(DesignSystem.Colors.textPrimary)
            Button("Retry", action: onRetry)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }
}
