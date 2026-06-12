import Foundation
import XCTest
@testable import IrisKit

/// Intercepts APIClient traffic. XCTest runs these tests serially, so the
/// unsafe static handler is not raced in practice.
final class MockURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, Data))?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    // URLProtocol declares these as class funcs; overrides cannot be static.
    // swiftlint:disable static_over_final_class
    override class func canInit(with _: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    // swiftlint:enable static_over_final_class

    override func stopLoading() {}

    override func startLoading() {
        Self.lastRequest = request
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (status, data) = try handler(request)
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: status,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
}

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.handler = nil
        MockURLProtocol.lastRequest = nil
        super.tearDown()
    }

    private func makeClient(token: String? = "test-token") -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return APIClient(
            baseURL: URL(string: "http://127.0.0.1:8765")!,
            token: { token },
            session: URLSession(configuration: configuration)
        )
    }

    private func respond(status: Int, json: String) {
        MockURLProtocol.handler = { _ in (status, Data(json.utf8)) }
    }

    // MARK: - Success decoding

    func testHealthDecodesContractPayload() async throws {
        respond(status: 200, json: """
        {"ok": true, "agent": "Iris", "version": "0.1.0", "contract_version": 1,
         "pid": 42, "started_at": "2026-06-10T18:18:09Z", "unknown_field": "ignored"}
        """)
        let health = try await makeClient().health()
        XCTAssertTrue(health.ok)
        XCTAssertEqual(health.contractVersion, 1)
        XCTAssertEqual(health.startedAt.timeIntervalSince1970, 1_781_115_489, accuracy: 1)
    }

    func testVoiceStartDecodesDescriptorAndSendsAuth() async throws {
        respond(status: 202, json: """
        {"session": {"id": "voice-abc", "mode": "conversation",
         "started_at": "2026-06-10T18:00:00Z", "state": "connecting"}}
        """)
        let session = try await makeClient().startVoice()
        XCTAssertEqual(session.id, "voice-abc")
        XCTAssertEqual(session.state, .connecting)

        let request = MockURLProtocol.lastRequest
        XCTAssertEqual(request?.httpMethod, "POST")
        XCTAssertEqual(
            request?.value(forHTTPHeaderField: "Authorization"), "Bearer test-token"
        )
    }

    func testFractionalSecondTimestampsDecode() async throws {
        respond(status: 200, json: """
        {"ok": true, "agent": "Iris", "version": "0.1.0", "contract_version": 1,
         "pid": 42, "started_at": "2026-06-10T18:18:09.123Z"}
        """)
        let health = try await makeClient().health()
        XCTAssertEqual(health.startedAt.timeIntervalSince1970, 1_781_115_489.123, accuracy: 0.01)
    }

    func testUnknownEnumValuesDecodeToUnknown() async throws {
        respond(status: 200, json: """
        {"state": "daydreaming", "session": null, "subscribers": 0, "meeting_active": false}
        """)
        let status = try await makeClient().voiceStatus()
        XCTAssertEqual(status.state, .unknown)
    }

    func testActivityFeedDecodes() async throws {
        respond(status: 200, json: """
        {"stats": {"sessions_this_week": 2, "runs_completed": 3, "runs_failed": 1,
                   "last_active_at": null},
         "days": [{"date": "2026-06-10", "label": "today", "items": [
            {"id": "run-r1", "kind": "run", "time": "2026-06-10T04:13:02Z",
             "title": "Agent run", "preview": null, "status": "done"}]}],
         "next_cursor": null}
        """)
        let feed = try await makeClient().activityFeed()
        XCTAssertEqual(feed.stats.sessionsThisWeek, 2)
        XCTAssertEqual(feed.days.first?.items.first?.kind, .run)
        XCTAssertNil(feed.nextCursor)
    }

    func testSettingsDecodeHeterogeneousValues() async throws {
        respond(status: 200, json: """
        {"settings": {
            "wake_words": {"value": ["iris", "hey iris"], "source": "settings", "mutable": true},
            "voice": {"value": "marin", "source": "env", "mutable": false},
            "wake_word_enabled": {"value": false, "source": "default", "mutable": true}},
         "secrets": {"openai_api_key": {"is_set": true}}}
        """)
        let response = try await makeClient().settings()
        XCTAssertEqual(
            response.settings["wake_words"]?.value,
            .array([.string("iris"), .string("hey iris")])
        )
        XCTAssertEqual(response.settings["voice"]?.source, .env)
        XCTAssertEqual(response.settings["voice"]?.mutable, false)
        XCTAssertEqual(response.settings["wake_word_enabled"]?.value, .bool(false))
        XCTAssertEqual(response.secrets["openai_api_key"]?.isSet, true)
    }

    func testUpdateSettingsSendsPutWithJSONBody() async throws {
        respond(status: 200, json: """
        {"settings": {}, "secrets": {}}
        """)
        _ = try await makeClient().updateSettings(["voice": .string("cedar")])

        let request = MockURLProtocol.lastRequest
        XCTAssertEqual(request?.httpMethod, "PUT")
        XCTAssertEqual(request?.url?.path(), "/settings")
        let body = try XCTUnwrap(request?.bodyBytes)
        XCTAssertEqual(
            try JSONSerialization.jsonObject(with: body) as? [String: String],
            ["voice": "cedar"]
        )
    }

    func testApprovalsDecodeLegacyShape() async throws {
        respond(status: 200, json: """
        {"approvals": [{"approval_id": "ap1", "run_id": "r1",
          "action_name": "system.run", "risk": "sensitive", "status": "pending",
          "preview": "Run `rm -rf scratch/`", "created_at": "2026-06-12T00:01:02.123456+00:00",
          "expires_at": null, "decided_at": null, "details_json": "{}"}]}
        """)
        let approvals = try await makeClient().approvals()
        XCTAssertEqual(approvals.count, 1)
        XCTAssertEqual(approvals.first?.approvalId, "ap1")
        XCTAssertEqual(approvals.first?.preview, "Run `rm -rf scratch/`")
        XCTAssertEqual(approvals.first?.status, "pending")
    }

    func testDecideApprovalPostsToDecisionPath() async throws {
        respond(status: 200, json: #"{"ok": true, "status": "denied"}"#)
        let response = try await makeClient().decideApproval(id: "ap1", decision: .deny)
        XCTAssertEqual(response, ApprovalDecisionResponse(ok: true, status: "denied"))
        XCTAssertEqual(MockURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(MockURLProtocol.lastRequest?.url?.path(), "/approvals/ap1/deny")
    }

    func testUpdateSecretsSendsPutAndDecodesPresenceOnly() async throws {
        respond(status: 200, json: """
        {"settings": {}, "secrets": {"openai_api_key": {"is_set": true}}}
        """)
        let response = try await makeClient().updateSecrets(["openai_api_key": "sk-test"])
        XCTAssertEqual(response.secrets["openai_api_key"]?.isSet, true)

        let request = MockURLProtocol.lastRequest
        XCTAssertEqual(request?.httpMethod, "PUT")
        XCTAssertEqual(request?.url?.path(), "/secrets")
        let body = try XCTUnwrap(request?.bodyBytes)
        XCTAssertEqual(
            try JSONSerialization.jsonObject(with: body) as? [String: String],
            ["openai_api_key": "sk-test"]
        )
    }

    // MARK: - Error taxonomy

    func testUnauthorizedMapsDistinctly() async {
        respond(status: 401, json: #"{"error": "gateway authorization required"}"#)
        await assertThrows(.unauthorized) { try await self.makeClient().health() }
    }

    func testConflictCarriesSessionForAdoption() async {
        respond(status: 409, json: """
        {"error": {"code": "voice_already_running", "message": "a voice session is already active"},
         "session": {"id": "voice-live", "mode": "conversation", "started_at": "2026-06-10T18:00:00Z"}}
        """)
        do {
            _ = try await makeClient().startVoice()
            XCTFail("expected conflict")
        } catch let error as IrisAPIError {
            guard case let .conflict(code, _, session) = error else {
                return XCTFail("expected .conflict, got \(error)")
            }
            XCTAssertEqual(code, "voice_already_running")
            XCTAssertEqual(session?.id, "voice-live")
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testEnvelopeBadRequestMapsToInvalidRequest() async {
        respond(status: 400, json: """
        {"error": {"code": "invalid_request", "message": "unknown voice mode: 'karaoke'"}}
        """)
        await assertThrows(
            .invalidRequest(code: "invalid_request", message: "unknown voice mode: 'karaoke'")
        ) { try await self.makeClient().startVoice(mode: "karaoke") }
    }

    func testLegacyFlatErrorsAreNormalized() async {
        respond(status: 500, json: #"{"error": "boom"}"#)
        await assertThrows(
            .server(code: "legacy_error", message: "boom", status: 500)
        ) { try await self.makeClient().health() }
    }

    func testNotFoundMaps() async {
        respond(status: 404, json: """
        {"error": {"code": "not_found", "message": "activity item not found"}}
        """)
        await assertThrows(.notFound(message: "activity item not found")) {
            _ = try await self.makeClient().activityDetail(id: "ses-missing")
        }
    }

    func testConnectionFailureMapsToDaemonUnreachable() async {
        MockURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        do {
            _ = try await makeClient().health()
            XCTFail("expected daemonUnreachable")
        } catch let error as IrisAPIError {
            guard case .daemonUnreachable = error else {
                return XCTFail("expected .daemonUnreachable, got \(error)")
            }
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testMalformedSuccessBodyMapsToDecodingError() async {
        respond(status: 200, json: #"{"totally": "unrelated"}"#)
        do {
            _ = try await makeClient().health()
            XCTFail("expected decoding error")
        } catch let error as IrisAPIError {
            guard case .decoding = error else {
                return XCTFail("expected .decoding, got \(error)")
            }
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testNoTokenSendsNoAuthorizationHeader() async throws {
        respond(status: 200, json: """
        {"ok": true, "agent": "Iris", "version": "0.1.0", "contract_version": 1,
         "pid": 42, "started_at": "2026-06-10T18:18:09Z"}
        """)
        _ = try await makeClient(token: nil).health()
        XCTAssertNil(MockURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"))
    }

    // MARK: - Helpers

    private func assertThrows(
        _ expected: IrisAPIError,
        _ body: @escaping () async throws -> some Any
    ) async {
        do {
            _ = try await body()
            XCTFail("expected \(expected)")
        } catch let error as IrisAPIError {
            XCTAssertEqual(error, expected)
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }
}

private extension URLRequest {
    /// URLProtocol exposes bodies as a stream, not `httpBody`.
    var bodyBytes: Data? {
        if let httpBody { return httpBody }
        guard let stream = httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }
}
