import IrisKit
import Observation
import SwiftUI

/// First-run flow state. Every permission has detection, explanation, and a
/// recovery path; every step can be skipped (degraded mode) except welcome.
@MainActor
@Observable
final class OnboardingModel {
    enum Step: Int, CaseIterable {
        case welcome, openAIKey, microphone, systemAccess, done
    }

    enum KeyStatus: Equatable {
        case checking
        case alreadySet
        case idle
        case saving
        case saved
        case failed(String)
    }

    var step: Step
    var apiKey = ""
    var keyStatus: KeyStatus = .checking
    var microphoneState: PermissionState = .undetermined
    var accessibilityState: PermissionState = .undetermined
    var screenRecordingState: PermissionState = .undetermined
    var onFinished: () -> Void = {}

    private let client: APIClient
    private let permissions: PermissionChecking
    private let preferences: AppPreferences

    init(
        client: APIClient,
        permissions: PermissionChecking,
        preferences: AppPreferences,
        startStep: Step = .welcome
    ) {
        self.client = client
        self.permissions = permissions
        self.preferences = preferences
        step = startStep
        refreshPermissions()
        if startStep == .openAIKey {
            Task { await refreshKeyPresence() }
        }
    }

    func advance() {
        guard let next = Step(rawValue: step.rawValue + 1) else { return }
        step = next
        if step == .openAIKey {
            Task { await refreshKeyPresence() }
        }
        refreshPermissions()
    }

    func back() {
        guard let previous = Step(rawValue: step.rawValue - 1) else { return }
        step = previous
    }

    func finish() {
        preferences.onboardingComplete = true
        onFinished()
    }

    // MARK: - OpenAI key

    func refreshKeyPresence() async {
        keyStatus = .checking
        do {
            let response = try await client.settings()
            let isSet = response.secrets["openai_api_key"]?.isSet ?? false
            keyStatus = isSet ? .alreadySet : .idle
        } catch {
            // Daemon unreachable is not fatal to onboarding; the key can be
            // entered later in Settings.
            keyStatus = .idle
        }
    }

    func saveKey() async {
        let trimmed = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        keyStatus = .saving
        do {
            let response = try await client.updateSecrets(["openai_api_key": trimmed])
            let isSet = response.secrets["openai_api_key"]?.isSet ?? false
            keyStatus = isSet ? .saved : .failed("The daemon did not confirm the key.")
            apiKey = ""
        } catch let error as IrisAPIError {
            keyStatus = .failed(error.errorDescription ?? "Could not save the key.")
        } catch {
            keyStatus = .failed("Could not save the key.")
        }
    }

    // MARK: - Permissions

    func refreshPermissions() {
        microphoneState = permissions.microphoneState()
        accessibilityState = permissions.accessibilityState()
        screenRecordingState = permissions.screenRecordingState()
    }

    func requestMicrophone() async {
        microphoneState = await permissions.requestMicrophone()
    }

    func requestAccessibility() {
        permissions.requestAccessibility()
        refreshPermissions()
    }

    func requestScreenRecording() {
        permissions.requestScreenRecording()
        refreshPermissions()
    }

    func openSettings(_ pane: PrivacyPane) {
        permissions.openSystemSettings(pane)
    }
}

/// The onboarding window: one step at a time, Back/Continue footer.
struct OnboardingView: View {
    @Bindable var model: OnboardingModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(spacing: 0) {
            stepContent
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(32)
            Divider()
            footer
                .padding(.horizontal, 20)
                .padding(.vertical, 14)
        }
        .frame(width: 480, height: 520)
        .onAppear {
            model.onFinished = { dismissWindow(id: "onboarding") }
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        switch model.step {
        case .welcome: WelcomeStep()
        case .openAIKey: OpenAIKeyStep(model: model)
        case .microphone: MicrophoneStep(model: model)
        case .systemAccess: SystemAccessStep(model: model)
        case .done: DoneStep()
        }
    }

    private var footer: some View {
        HStack {
            if model.step != .welcome, model.step != .done {
                Button("Back") { model.back() }
            }
            Spacer()
            switch model.step {
            case .welcome:
                Button("Continue") { model.advance() }
                    .keyboardShortcut(.defaultAction)
            case .openAIKey:
                Button(model.keyStatus == .alreadySet ? "Continue" : "Skip for Now") {
                    model.advance()
                }
            case .microphone, .systemAccess:
                Button(continueOrSkipTitle) { model.advance() }
            case .done:
                Button("Start Using Iris") { model.finish() }
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private var continueOrSkipTitle: String {
        let granted = model.step == .microphone
            ? model.microphoneState == .granted
            : model.accessibilityState == .granted
            && model.screenRecordingState == .granted
        return granted ? "Continue" : "Skip for Now"
    }
}
