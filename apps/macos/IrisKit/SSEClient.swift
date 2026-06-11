import Foundation
import os

/// One parsed `event:`/`data:` frame. `data` stays a raw JSON string here;
/// typed decoding belongs to the feature layer consuming the stream.
public struct SSEEvent: Equatable, Sendable {
    public let type: String
    public let data: String

    public init(type: String, data: String) {
        self.type = type
        self.data = data
    }

    public func decode<T: Decodable>(_ type: T.Type) throws -> T {
        try IrisJSON.decoder().decode(type, from: Data(data.utf8))
    }
}

/// Connection state is part of the stream so the UI can always render an
/// honest live/reconnecting indicator (contract client obligation #4).
public enum SSEClientEvent: Equatable, Sendable {
    case connected
    case event(SSEEvent)
    case heartbeat
    case disconnected(reason: String)
}

public struct SSEClientConfiguration: Sendable {
    public var minBackoff: Duration
    public var maxBackoff: Duration
    /// Idle timeout between bytes. The gateway heartbeats every 15s, so a
    /// connection silent past two heartbeats is stale and must reconnect.
    public var idleTimeout: TimeInterval

    public init(
        minBackoff: Duration = .milliseconds(500),
        maxBackoff: Duration = .seconds(15),
        idleTimeout: TimeInterval = 35
    ) {
        self.minBackoff = minBackoff
        self.maxBackoff = maxBackoff
        self.idleTimeout = idleTimeout
    }
}

/// Auto-reconnecting SSE consumer over `URLSession.bytes`.
///
/// The stream only ends when the consumer cancels; every server drop, HTTP
/// error, or stale connection yields `.disconnected` and retries with
/// jittered exponential backoff.
public struct SSEClient: Sendable {
    private let url: URL
    private let token: @Sendable () -> String?
    private let session: URLSession
    private let configuration: SSEClientConfiguration
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "sse")

    public init(
        url: URL,
        token: @escaping @Sendable () -> String?,
        session: URLSession = .shared,
        configuration: SSEClientConfiguration = SSEClientConfiguration()
    ) {
        self.url = url
        self.token = token
        self.session = session
        self.configuration = configuration
    }

    public func events() -> AsyncStream<SSEClientEvent> {
        AsyncStream { continuation in
            let task = Task {
                var attempt = 0
                while !Task.isCancelled {
                    do {
                        try await runConnection(continuation: continuation)
                        attempt = 0
                        continuation.yield(.disconnected(reason: "stream ended"))
                    } catch is CancellationError {
                        break
                    } catch {
                        if Task.isCancelled { break }
                        continuation.yield(
                            .disconnected(reason: Self.describe(error))
                        )
                    }
                    attempt += 1
                    let delay = Self.backoff(
                        attempt: attempt,
                        minimum: configuration.minBackoff,
                        maximum: configuration.maxBackoff
                    )
                    logger.info("sse reconnecting in \(delay) (attempt \(attempt))")
                    do {
                        try await Task.sleep(for: delay)
                    } catch {
                        break // cancelled during backoff
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func runConnection(
        continuation: AsyncStream<SSEClientEvent>.Continuation
    ) async throws {
        var request = URLRequest(url: url, timeoutInterval: configuration.idleTimeout)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let token = token() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SSEConnectionError.badStatus(0)
        }
        guard http.statusCode == 200 else {
            throw SSEConnectionError.badStatus(http.statusCode)
        }
        continuation.yield(.connected)
        logger.info("sse connected")

        var assembler = SSELineAssembler()
        var parser = SSEFrameParser()
        for try await byte in bytes {
            guard let line = assembler.consume(byte) else { continue }
            if let event = parser.consume(line: line) {
                continuation.yield(event)
            }
        }
    }

    static func backoff(attempt: Int, minimum: Duration, maximum: Duration) -> Duration {
        let exponent = min(attempt - 1, 8)
        let base = Self.seconds(minimum) * pow(2, Double(exponent))
        let capped = min(base, Self.seconds(maximum))
        return .seconds(capped * Double.random(in: 0.8 ... 1.2))
    }

    private static func seconds(_ duration: Duration) -> Double {
        Double(duration.components.seconds)
            + Double(duration.components.attoseconds) / 1e18
    }

    private static func describe(_ error: Error) -> String {
        if case let SSEConnectionError.badStatus(code) = error {
            return code == 0 ? "non-HTTP response" : "HTTP \(code)"
        }
        return error.localizedDescription
    }
}

enum SSEConnectionError: Error {
    case badStatus(Int)
}

/// Assembles bytes into lines. SSE frames are delimited by *blank* lines,
/// which `AsyncBytes.lines` does not reliably surface — so we do it by hand.
struct SSELineAssembler {
    private var buffer: [UInt8] = []

    mutating func consume(_ byte: UInt8) -> String? {
        if byte == 0x0A { // \n
            var bytes = buffer
            buffer.removeAll(keepingCapacity: true)
            if bytes.last == 0x0D { // strip \r
                bytes.removeLast()
            }
            // SSE is UTF-8 by spec; lossy decoding (never failing) is the
            // right behavior for a long-lived stream.
            // swiftlint:disable:next optional_data_string_conversion
            return String(decoding: bytes, as: UTF8.self)
        }
        buffer.append(byte)
        return nil
    }
}

/// Parses SSE lines into events per the WHATWG EventSource grammar subset
/// the gateway emits (`event:`, `data:`, `:` comments).
struct SSEFrameParser {
    private var eventType: String?
    private var dataLines: [String] = []

    mutating func consume(line: String) -> SSEClientEvent? {
        if line.isEmpty {
            defer {
                eventType = nil
                dataLines = []
            }
            guard !dataLines.isEmpty else { return nil }
            return .event(SSEEvent(
                type: eventType ?? "message",
                data: dataLines.joined(separator: "\n")
            ))
        }
        if line.hasPrefix(":") {
            return .heartbeat
        }
        if let value = Self.fieldValue(line: line, field: "event") {
            eventType = value
        } else if let value = Self.fieldValue(line: line, field: "data") {
            dataLines.append(value)
        }
        // id:/retry: fields are not used by the gateway; ignored.
        return nil
    }

    private static func fieldValue(line: String, field: String) -> String? {
        guard line.hasPrefix("\(field):") else { return nil }
        var value = line.dropFirst(field.count + 1)
        if value.first == " " {
            value = value.dropFirst()
        }
        return String(value)
    }
}
