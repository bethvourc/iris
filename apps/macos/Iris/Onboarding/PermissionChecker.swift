import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics

enum PermissionState: Equatable {
    case granted
    case denied
    case undetermined
}

enum PrivacyPane: String {
    case microphone = "Privacy_Microphone"
    case accessibility = "Privacy_Accessibility"
    case screenRecording = "Privacy_ScreenCapture"
}

/// Seam over TCC so onboarding logic is testable and screenshots can pin
/// permission states.
@MainActor
protocol PermissionChecking {
    func microphoneState() -> PermissionState
    func requestMicrophone() async -> PermissionState
    func accessibilityState() -> PermissionState
    func requestAccessibility()
    func screenRecordingState() -> PermissionState
    func requestScreenRecording()
    func openSystemSettings(_ pane: PrivacyPane)
}

@MainActor
struct SystemPermissionChecker: PermissionChecking {
    /// Thread-safe mic check for the voice model's `@Sendable` pre-check
    /// (`AVCaptureDevice.authorizationStatus` is callable from any actor).
    nonisolated static func microphoneIsAuthorized() -> Bool {
        AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
    }

    func microphoneState() -> PermissionState {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: .granted
        case .denied, .restricted: .denied
        case .notDetermined: .undetermined
        @unknown default: .undetermined
        }
    }

    func requestMicrophone() async -> PermissionState {
        await AVCaptureDevice.requestAccess(for: .audio) ? .granted : .denied
    }

    /// Accessibility and Screen Recording have no "denied" signal distinct
    /// from "not yet granted"; both read as undetermined until granted.
    func accessibilityState() -> PermissionState {
        AXIsProcessTrusted() ? .granted : .undetermined
    }

    func requestAccessibility() {
        // kAXTrustedCheckOptionPrompt is a C global var and not
        // concurrency-safe to reference in Swift 6; its value is stable.
        _ = AXIsProcessTrustedWithOptions(
            ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        )
    }

    func screenRecordingState() -> PermissionState {
        CGPreflightScreenCaptureAccess() ? .granted : .undetermined
    }

    func requestScreenRecording() {
        _ = CGRequestScreenCaptureAccess()
    }

    func openSystemSettings(_ pane: PrivacyPane) {
        let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?\(pane.rawValue)"
        )!
        NSWorkspace.shared.open(url)
    }
}

/// Launch-arg-driven checker (`--ui-test-permissions <state>`): requests
/// always succeed, so UI tests and screenshots are deterministic.
@MainActor
final class ScriptedPermissionChecker: PermissionChecking {
    private var microphone: PermissionState
    private var accessibility: PermissionState
    private var screenRecording: PermissionState

    init(initial: PermissionState) {
        microphone = initial
        accessibility = initial
        screenRecording = initial
    }

    func microphoneState() -> PermissionState {
        microphone
    }

    func requestMicrophone() async -> PermissionState {
        microphone = .granted
        return microphone
    }

    func accessibilityState() -> PermissionState {
        accessibility
    }

    func requestAccessibility() {
        accessibility = .granted
    }

    func screenRecordingState() -> PermissionState {
        screenRecording
    }

    func requestScreenRecording() {
        screenRecording = .granted
    }

    func openSystemSettings(_: PrivacyPane) {}
}
