import AppKit
import IrisKit
import os

/// App-side wrapper around `Diagnostics`: reads the daemon log tail, assembles
/// the (redacted) bundle, zips it, and presents a save panel. The redaction and
/// bundle contents live in `IrisKit` so they're unit-tested; this layer only
/// does file/zip/UI work.
enum DiagnosticsExporter {
    private static let logger = Logger(subsystem: "com.bethvour.iris", category: "diagnostics")

    static var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Logs/Iris/daemon.log")
    }

    /// Last `maxBytes` of the daemon log — for the log viewer and the bundle.
    static func readLogTail(maxBytes: Int = 200_000) -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "" }
        return String(decoding: data.suffix(maxBytes), as: UTF8.self)
    }

    static func appVersion() -> String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }

    /// Build → write → zip → save panel. Returns silently on cancel; beeps on
    /// failure (no modal error — consistent with the app's quiet posture).
    @MainActor
    static func export(settingsSummary: String) {
        let files = Diagnostics.bundle(
            logText: readLogTail(),
            settingsJSON: settingsSummary,
            appVersion: appVersion(),
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString
        )
        do {
            let zipURL = try makeZip(files: files)
            presentSavePanel(zipURL: zipURL)
        } catch {
            logger.error("diagnostics export failed: \(error.localizedDescription)")
            NSSound.beep()
        }
    }

    private static func makeZip(files: [Diagnostics.File]) throws -> URL {
        let fileManager = FileManager.default
        let staging = fileManager.temporaryDirectory
            .appending(path: "Iris-Diagnostics-\(UUID().uuidString)")
        try fileManager.createDirectory(at: staging, withIntermediateDirectories: true)
        for file in files {
            try file.contents.write(
                to: staging.appending(path: file.name), atomically: true, encoding: .utf8
            )
        }

        var coordinatorError: NSError?
        var zipURL: URL?
        NSFileCoordinator().coordinate(
            readingItemAt: staging, options: [.forUploading], error: &coordinatorError
        ) { tempZip in
            let destination = fileManager.temporaryDirectory.appending(path: "Iris-Diagnostics.zip")
            try? fileManager.removeItem(at: destination)
            do {
                try fileManager.copyItem(at: tempZip, to: destination)
                zipURL = destination
            } catch {
                logger.error("zip copy failed: \(error.localizedDescription)")
            }
        }
        if let coordinatorError { throw coordinatorError }
        guard let zipURL else {
            throw CocoaError(.fileWriteUnknown)
        }
        return zipURL
    }

    @MainActor
    private static func presentSavePanel(zipURL: URL) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "Iris-Diagnostics.zip"
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.copyItem(at: zipURL, to: destination)
        } catch {
            logger.error("saving diagnostics failed: \(error.localizedDescription)")
            NSSound.beep()
        }
    }
}
