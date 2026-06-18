import Foundation
import XCTest
@testable import IrisKit

final class DiagnosticsTests: XCTestCase {
    func testRedactScrubsKnownKeyShapes() {
        let samples = [
            "OPENAI key sk-abcdEFGH1234567890",
            "groq gsk_abcdEFGH1234567890",
            "resend re_abcdEFGH1234567890",
            "Authorization: Bearer abcDEF1234567890",
            "token=supersecretvalue123"
        ]
        for sample in samples {
            let redacted = Diagnostics.redact(sample)
            XCTAssertTrue(redacted.contains("***REDACTED***"), "should redact: \(sample)")
        }
    }

    func testRedactLeavesOrdinaryTextAlone() {
        let text = "Started daemon on port 8765; healthy in 1.2s."
        XCTAssertEqual(Diagnostics.redact(text), text)
    }

    func testBundleContainsNoSecrets() {
        // A leaked key in the logs must not survive into the bundle.
        let logs = """
        {"event": "request", "auth": "Bearer abcDEF1234567890"}
        {"event": "configure", "openai": "sk-LIVEKEY1234567890"}
        """
        let settingsJSON = #"{"secrets": {"openai_api_key": {"is_set": true}}}"#
        let files = Diagnostics.bundle(
            logText: logs,
            settingsJSON: settingsJSON,
            appVersion: "1.0.0",
            osVersion: "15.0"
        )

        XCTAssertEqual(files.map(\.name).sorted(), ["daemon.log", "settings.json", "summary.txt"])
        for file in files {
            XCTAssertFalse(file.contents.contains("sk-LIVEKEY1234567890"), "\(file.name) leaked a key")
            XCTAssertFalse(file.contents.contains("abcDEF1234567890"), "\(file.name) leaked a token")
        }
    }
}
