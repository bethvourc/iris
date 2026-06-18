import SwiftUI

// Step bodies for OnboardingView. Each permission screen explains *why*
// Iris wants it and works when skipped — trust is built here.

struct WelcomeStep: View {
    var body: some View {
        VStack(spacing: 14) {
            SiriNewMark()
                .frame(width: 56, height: 56)
                .foregroundStyle(.tint)
            Text("Welcome to Iris")
                .font(.largeTitle.weight(.semibold))
            Text(
                """
                Iris is a local-first assistant that lives in your menu bar. \
                A few quick steps connect it to your OpenAI account and give \
                it the macOS permissions it needs.
                """
            )
            .multilineTextAlignment(.center)
            .foregroundStyle(.secondary)
            .frame(maxWidth: 360)
        }
    }
}

struct OpenAIKeyStep: View {
    @Bindable var model: OnboardingModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepHeader(
                title: "OpenAI API Key",
                subtitle: "Voice and reasoning run on your own OpenAI account. "
                    + "The key is stored by the Iris daemon on this Mac and never leaves it."
            )
            switch model.keyStatus {
            case .checking:
                ProgressView()
                    .controlSize(.small)
            case .alreadySet, .saved:
                Label(
                    model.keyStatus == .saved
                        ? "Key saved."
                        : "A key is already configured.",
                    systemImage: "checkmark.circle.fill"
                )
                .foregroundStyle(.green)
            case .idle, .saving, .failed:
                keyEntry
            }
            Spacer()
        }
    }

    @ViewBuilder
    private var keyEntry: some View {
        SecureField("sk-…", text: $model.apiKey)
            .textFieldStyle(.roundedBorder)
            .accessibilityIdentifier("onboarding-api-key")
        if case let .failed(message) = model.keyStatus {
            Text(message)
                .font(.callout)
                .foregroundStyle(.red)
        }
        HStack {
            Button("Save Key") {
                Task { await model.saveKey() }
            }
            .disabled(
                model.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || model.keyStatus == .saving
            )
            if model.keyStatus == .saving {
                ProgressView()
                    .controlSize(.small)
            }
        }
        Text("You can also add or replace the key later in Settings.")
            .font(.callout)
            .foregroundStyle(.secondary)
    }
}

struct MicrophoneStep: View {
    let model: OnboardingModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepHeader(
                title: "Microphone",
                subtitle: "Iris listens only when you activate it. Without this "
                    + "permission, voice conversations are unavailable; everything "
                    + "else still works."
            )
            PermissionRow(
                name: "Microphone",
                state: model.microphoneState,
                allowTitle: "Allow Microphone",
                onAllow: { Task { await model.requestMicrophone() } },
                onOpenSettings: { model.openSettings(.microphone) },
                onVerify: { model.refreshPermissions() }
            )
            Spacer()
        }
    }
}

struct SystemAccessStep: View {
    let model: OnboardingModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepHeader(
                title: "Mac Control",
                subtitle: "Accessibility lets Iris click and type for you; Screen "
                    + "Recording lets it see what's on screen when you ask. Both are "
                    + "optional — skip them and Iris stays voice-only."
            )
            PermissionRow(
                name: "Accessibility",
                state: model.accessibilityState,
                allowTitle: "Grant Access",
                onAllow: { model.requestAccessibility() },
                onOpenSettings: { model.openSettings(.accessibility) },
                onVerify: { model.refreshPermissions() }
            )
            PermissionRow(
                name: "Screen Recording",
                state: model.screenRecordingState,
                allowTitle: "Grant Access",
                onAllow: { model.requestScreenRecording() },
                onOpenSettings: { model.openSettings(.screenRecording) },
                onVerify: { model.refreshPermissions() }
            )
            Spacer()
        }
    }
}

struct DoneStep: View {
    var body: some View {
        VStack(spacing: 14) {
            SiriNewMark()
                .frame(width: 44, height: 44)
                .foregroundStyle(.tint)
            Text("You're set")
                .font(.largeTitle.weight(.semibold))
            Text(
                """
                Iris lives in your menu bar — look for the wave. Open the menu \
                to talk to Iris, see what it's doing, or change settings.
                """
            )
            .multilineTextAlignment(.center)
            .foregroundStyle(.secondary)
            .frame(maxWidth: 360)
        }
    }
}

// MARK: - Shared pieces

struct StepHeader: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.title.weight(.semibold))
            Text(subtitle)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct PermissionRow: View {
    let name: String
    let state: PermissionState
    let allowTitle: String
    let onAllow: () -> Void
    let onOpenSettings: () -> Void
    let onVerify: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            statusBadge
            Text(name)
            Spacer()
            switch state {
            case .granted:
                EmptyView()
            case .undetermined:
                Button(allowTitle, action: onAllow)
                Button("Verify", action: onVerify)
            case .denied:
                Button("Open System Settings", action: onOpenSettings)
                Button("Verify", action: onVerify)
            }
        }
        .padding(12)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(.separator, lineWidth: 1)
        )
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch state {
        case .granted:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .accessibilityLabel("\(name) granted")
        case .denied:
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
                .accessibilityLabel("\(name) denied")
        case .undetermined:
            Image(systemName: "circle.dashed")
                .foregroundStyle(.secondary)
                .accessibilityLabel("\(name) not determined")
        }
    }
}
