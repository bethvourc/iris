import Foundation

/// Builds the support bundle's contents and — critically — scrubs secrets from
/// them. The export is meant to be safe to hand to support, so the redaction is
/// pure and unit-tested; the app layer only wraps these files in a zip.
public enum Diagnostics {
    /// One named file destined for the diagnostics zip.
    public struct File: Equatable, Sendable {
        public let name: String
        public let contents: String

        public init(name: String, contents: String) {
            self.name = name
            self.contents = contents
        }
    }

    /// Assemble the (already-redacted) bundle. `settingsJSON` comes from
    /// `GET /settings`, which is presence-only for secrets by contract; logs
    /// and settings are still run through the redactor as defense in depth.
    public static func bundle(
        logText: String,
        settingsJSON: String,
        appVersion: String,
        osVersion: String,
        generatedAt: Date = .now
    ) -> [File] {
        let summary = """
        Iris diagnostics
        Generated: \(ISO8601DateFormatter().string(from: generatedAt))
        App version: \(appVersion)
        macOS: \(osVersion)
        """
        return [
            File(name: "summary.txt", contents: redact(summary)),
            File(name: "settings.json", contents: redact(settingsJSON)),
            File(name: "daemon.log", contents: redact(logText))
        ]
    }

    /// Replace anything that looks like a credential. Patterns cover the
    /// provider keys Iris handles plus generic bearer tokens, so even a log
    /// line that should never contain a secret can't leak one.
    public static func redact(_ text: String) -> String {
        var result = text
        for pattern in secretPatterns {
            result = result.replacingOccurrences(
                of: pattern,
                with: "***REDACTED***",
                options: [.regularExpression, .caseInsensitive]
            )
        }
        return result
    }

    /// Token shapes: OpenAI (`sk-…`), Groq (`gsk_…`), Resend (`re_…`), and a
    /// `Bearer <token>` / `token=<value>` catch-all.
    private static let secretPatterns = [
        #"sk-[A-Za-z0-9_\-]{8,}"#,
        #"gsk_[A-Za-z0-9_\-]{8,}"#,
        #"re_[A-Za-z0-9_\-]{8,}"#,
        #"(?:Bearer\s+)[A-Za-z0-9._\-]{8,}"#,
        #"(?:token["']?\s*[:=]\s*["']?)[A-Za-z0-9._\-]{8,}"#
    ]
}
