import Foundation
import XCTest
@testable import IrisKit

/// Streams scripted SSE responses, one script per connection attempt.
/// Tests run serially, so the unsafe statics are not raced in practice.
final class SSEStubProtocol: URLProtocol {
    enum End {
        case finish
        case fail(URLError.Code)
        case hang
    }

    struct Script {
        var status = 200
        var chunks: [String] = []
        var end: End = .hang
    }

    nonisolated(unsafe) static var scripts: [Script] = []
    nonisolated(unsafe) static var attempts = 0
    nonisolated(unsafe) static var lastRequest: URLRequest?

    static func reset(scripts: [Script]) {
        self.scripts = scripts
        attempts = 0
        lastRequest = nil
    }

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
        let index = Self.attempts
        Self.attempts += 1
        let script = index < Self.scripts.count ? Self.scripts[index] : Script()

        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: script.status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "text/event-stream"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        for chunk in script.chunks {
            client?.urlProtocol(self, didLoad: Data(chunk.utf8))
        }
        switch script.end {
        case .finish:
            client?.urlProtocolDidFinishLoading(self)
        case let .fail(code):
            client?.urlProtocol(self, didFailWithError: URLError(code))
        case .hang:
            break // stays open until the client cancels
        }
    }
}

final class SSEClientTests: XCTestCase {
    private func makeClient() -> SSEClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [SSEStubProtocol.self]
        return SSEClient(
            url: URL(string: "http://127.0.0.1:8765/voice/events")!,
            token: { "test-token" },
            session: URLSession(configuration: configuration),
            configuration: SSEClientConfiguration(
                minBackoff: .milliseconds(10),
                maxBackoff: .milliseconds(40),
                idleTimeout: 5
            )
        )
    }

    /// Collects events until `count` is reached or the timeout elapses.
    private func collect(
        _ client: SSEClient, count: Int, timeout: Duration = .seconds(5)
    ) async -> [SSEClientEvent] {
        let stream = client.events()
        var collected: [SSEClientEvent] = []
        let deadline = ContinuousClock.now + timeout
        var iterator = stream.makeAsyncIterator()
        while collected.count < count, ContinuousClock.now < deadline {
            guard let event = await iterator.next() else { break }
            collected.append(event)
        }
        return collected
    }

    // MARK: - Connection lifecycle

    func testConnectsAndDeliversFramesSplitAcrossChunks() async {
        SSEStubProtocol.reset(scripts: [
            .init(chunks: ["even", "t: state\nda", "ta: {\"state\": \"idle\"}\n\n"])
        ])
        let events = await collect(makeClient(), count: 2)
        XCTAssertEqual(events, [
            .connected,
            .event(SSEEvent(type: "state", data: "{\"state\": \"idle\"}"))
        ])
        XCTAssertEqual(
            SSEStubProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"),
            "Bearer test-token"
        )
    }

    func testHeartbeatCommentsSurface() async {
        SSEStubProtocol.reset(scripts: [
            .init(chunks: [": hb\n\n", "event: x\ndata: {}\n\n"])
        ])
        let events = await collect(makeClient(), count: 3)
        XCTAssertEqual(events, [
            .connected,
            .heartbeat,
            .event(SSEEvent(type: "x", data: "{}"))
        ])
    }

    func testReconnectsAfterServerCloseWithEventsFromBothConnections() async {
        // A clean server close (EOF) delivers buffered bytes deterministically;
        // an abrupt URLError drop discards them, which is covered separately.
        SSEStubProtocol.reset(scripts: [
            .init(chunks: ["event: a\ndata: 1\n\n"], end: .finish),
            .init(chunks: ["event: b\ndata: 2\n\n"], end: .hang)
        ])
        let events = await collect(makeClient(), count: 5)
        XCTAssertEqual(events, [
            .connected,
            .event(SSEEvent(type: "a", data: "1")),
            .disconnected(reason: "stream ended"),
            .connected,
            .event(SSEEvent(type: "b", data: "2"))
        ])
    }

    func testAbruptConnectionFailureRetries() async {
        SSEStubProtocol.reset(scripts: [
            .init(end: .fail(.networkConnectionLost)),
            .init(chunks: ["event: b\ndata: 2\n\n"], end: .hang)
        ])
        let events = await collect(makeClient(), count: 4)
        // The error may race the response handshake, so assert on the
        // outcome rather than exact ordering: a disconnect happened, and
        // the second connection delivered its event.
        XCTAssertTrue(events.contains { event in
            if case .disconnected = event { return true }
            return false
        })
        XCTAssertEqual(events.last, .event(SSEEvent(type: "b", data: "2")))
    }

    func testHTTPErrorStatusDisconnectsWithReasonAndRetries() async {
        SSEStubProtocol.reset(scripts: [
            .init(status: 500, end: .finish),
            .init(chunks: ["event: ok\ndata: {}\n\n"], end: .hang)
        ])
        let events = await collect(makeClient(), count: 3)
        XCTAssertEqual(events.first, .disconnected(reason: "HTTP 500"))
        XCTAssertEqual(events.dropFirst().first, .connected)
    }

    func testCancellationStopsRetries() async throws {
        SSEStubProtocol.reset(scripts: []) // every attempt hangs
        let client = makeClient()
        let task = Task {
            for await _ in client.events() {}
        }
        try await Task.sleep(for: .milliseconds(100))
        let attemptsBeforeCancel = SSEStubProtocol.attempts
        XCTAssertGreaterThanOrEqual(attemptsBeforeCancel, 1)
        task.cancel()
        _ = await task.value
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertEqual(SSEStubProtocol.attempts, attemptsBeforeCancel)
    }

    // MARK: - Backoff

    func testBackoffGrowsAndIsCapped() {
        let minimum = Duration.milliseconds(100)
        let maximum = Duration.seconds(2)
        let first = SSEClient.backoff(attempt: 1, minimum: minimum, maximum: maximum)
        let tenth = SSEClient.backoff(attempt: 10, minimum: minimum, maximum: maximum)
        XCTAssertLessThanOrEqual(first, .milliseconds(120))
        XCTAssertGreaterThanOrEqual(first, .milliseconds(80))
        XCTAssertLessThanOrEqual(tenth, .seconds(2.4))
        XCTAssertGreaterThanOrEqual(tenth, .seconds(1.6))
    }
}

// MARK: - Pure parser tests

final class SSEFrameParserTests: XCTestCase {
    private func parse(_ lines: [String]) -> [SSEClientEvent] {
        var parser = SSEFrameParser()
        return lines.compactMap { parser.consume(line: $0) }
    }

    func testMultiLineDataIsJoinedWithNewlines() {
        let events = parse(["event: x", "data: line1", "data: line2", ""])
        XCTAssertEqual(events, [.event(SSEEvent(type: "x", data: "line1\nline2"))])
    }

    func testDefaultEventTypeIsMessage() {
        let events = parse(["data: {}", ""])
        XCTAssertEqual(events, [.event(SSEEvent(type: "message", data: "{}"))])
    }

    func testFieldValueWithoutLeadingSpace() {
        let events = parse(["event:x", "data:1", ""])
        XCTAssertEqual(events, [.event(SSEEvent(type: "x", data: "1"))])
    }

    func testEmptyFramesAndUnknownFieldsAreIgnored() {
        let events = parse(["", "id: 7", "retry: 100", "", "data: 1", ""])
        XCTAssertEqual(events, [.event(SSEEvent(type: "message", data: "1"))])
    }

    func testStateIsResetBetweenFrames() {
        let events = parse(["event: a", "data: 1", "", "data: 2", ""])
        XCTAssertEqual(events, [
            .event(SSEEvent(type: "a", data: "1")),
            .event(SSEEvent(type: "message", data: "2"))
        ])
    }

    func testLineAssemblerHandlesCRLFAndSplitBytes() {
        var assembler = SSELineAssembler()
        var lines: [String] = []
        for byte in Array("data: 1\r\n\r\nda".utf8) {
            if let line = assembler.consume(byte) {
                lines.append(line)
            }
        }
        XCTAssertEqual(lines, ["data: 1", ""])
        for byte in Array("ta: 2\n".utf8) {
            if let line = assembler.consume(byte) {
                lines.append(line)
            }
        }
        XCTAssertEqual(lines, ["data: 1", "", "data: 2"])
    }
}
