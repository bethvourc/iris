import IrisKit
import SwiftUI

/// Advanced: daemon status/port, restart, a log tail viewer, and the
/// diagnostics export — the self-service recovery surface.
struct AdvancedPane: View {
    let model: AppModel
    let settings: SettingsViewModel

    @State private var port = ""
    @State private var logText = ""

    var body: some View {
        Form {
            Section("Daemon") {
                LabeledContent("Status") {
                    Text(model.daemonState.menuDescription)
                        .foregroundStyle(DesignSystem.Colors.textSecondary)
                }
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    LabeledContent("Port") {
                        TextField("", text: $port, prompt: Text("8765"))
                            .labelsHidden()
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 90)
                            .onSubmit(savePort)
                    }
                    Text("Restart Iris to apply a new port.")
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
                Button("Restart Iris") { model.restartDaemon() }
            }

            Section("Logs") {
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.sm) {
                    HStack {
                        Text("daemon.log")
                            .font(DesignSystem.Typography.callout)
                            .foregroundStyle(DesignSystem.Colors.textSecondary)
                        Spacer()
                        Button("Refresh", action: refreshLogs)
                            .controlSize(.small)
                    }
                    LogViewer(text: logText)
                }
            }

            Section("Support") {
                VStack(alignment: .leading, spacing: DesignSystem.Spacing.xs) {
                    Button("Export Diagnostics…") {
                        DiagnosticsExporter.export(settingsSummary: settings.diagnosticsSummary())
                    }
                    Text("A zip of logs, redacted settings, and versions — safe to share.")
                        .font(DesignSystem.Typography.caption)
                        .foregroundStyle(DesignSystem.Colors.textTertiary)
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
        .onAppear {
            port = String(model.preferences.gatewayPort)
            refreshLogs()
        }
    }

    private func savePort() {
        guard let value = Int(port.trimmingCharacters(in: .whitespaces)), value > 0 else {
            port = String(model.preferences.gatewayPort)
            return
        }
        model.preferences.gatewayPort = value
    }

    private func refreshLogs() {
        logText = DiagnosticsExporter.readLogTail()
    }
}

/// Read-only monospaced tail of the log. Shows a quiet placeholder when empty.
private struct LogViewer: View {
    let text: String

    var body: some View {
        ScrollView {
            Text(displayText)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(text.isEmpty
                    ? DesignSystem.Colors.textTertiary
                    : DesignSystem.Colors.textSecondary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(DesignSystem.Spacing.sm)
        }
        .frame(height: 160)
        .background(DesignSystem.Colors.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: DesignSystem.Radius.sm))
    }

    private var displayText: String {
        text.isEmpty ? "No logs yet." : text
    }
}
