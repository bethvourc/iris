import Foundation
import os

/// Typed HTTP boundary to the Iris daemon. All transport failures are
/// normalized to `IrisAPIError`; nothing stringly-typed escapes this layer.
/// Requests are logged (path, status, duration) — never the token, never
/// bodies.
public struct APIClient: Sendable {
    private let baseURL: URL
    private let token: @Sendable () -> String?
    private let session: URLSession
    private let logger = Logger(subsystem: "com.bethvour.iris", category: "api")

    public init(
        baseURL: URL,
        token: @escaping @Sendable () -> String?,
        session: URLSession = .shared
    ) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    // MARK: - Health

    public func health() async throws -> HealthStatus {
        try await get("/health")
    }

    // MARK: - Voice

    public func voiceStatus() async throws -> VoiceStatus {
        try await get("/voice/status")
    }

    /// Starts a voice session. On `voice_already_running` the thrown
    /// `.conflict` carries the live session descriptor for adoption.
    public func startVoice(mode: String = "conversation") async throws -> VoiceSessionDescriptor {
        let response: VoiceStartResponse = try await send(
            "POST", "/voice/start", body: ["mode": JSONValue.string(mode)]
        )
        return response.session
    }

    public func stopVoice() async throws -> VoiceStopResponse {
        try await send("POST", "/voice/stop")
    }

    /// Returns whether an in-flight response was actually interrupted.
    public func interruptVoice() async throws -> Bool {
        let response: OkResponse = try await send("POST", "/voice/interrupt")
        return response.ok
    }

    // MARK: - Activity

    public func activityFeed(
        days: Int = 7,
        limit: Int = 50,
        cursor: String? = nil,
        timezone: String = TimeZone.current.identifier
    ) async throws -> ActivityFeed {
        var query = [
            URLQueryItem(name: "days", value: String(days)),
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "tz", value: timezone)
        ]
        if let cursor {
            query.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return try await get("/activity", query: query)
    }

    public func activityDetail(id: String) async throws -> ActivityDetail {
        try await get("/activity/\(id)")
    }

    // MARK: - Settings

    public func settings() async throws -> SettingsResponse {
        try await get("/settings")
    }

    public func updateSettings(_ changes: [String: JSONValue]) async throws -> SettingsResponse {
        try await send("PUT", "/settings", body: changes)
    }

    // MARK: - Transport

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await perform(method: "GET", path: path, query: query, bodyData: nil)
    }

    private func send<T: Decodable>(
        _ method: String,
        _ path: String,
        body: [String: JSONValue]? = nil
    ) async throws -> T {
        let bodyData: Data? = if let body {
            try JSONEncoder().encode(body)
        } else {
            nil
        }
        return try await perform(method: method, path: path, query: [], bodyData: bodyData)
    }

    private func perform<T: Decodable>(
        method: String,
        path: String,
        query: [URLQueryItem],
        bodyData: Data?
    ) async throws -> T {
        var components = URLComponents(
            url: baseURL.appending(path: path), resolvingAgainstBaseURL: false
        )
        if !query.isEmpty {
            components?.queryItems = query
        }
        guard let url = components?.url else {
            throw IrisAPIError.invalidRequest(code: "invalid_url", message: path)
        }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = method
        if let token = token() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let bodyData {
            request.httpBody = bodyData
            request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        }

        let started = ContinuousClock.now
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            logger.warning("\(method) \(path) failed: daemon unreachable")
            throw IrisAPIError.daemonUnreachable(detail: error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw IrisAPIError.daemonUnreachable(detail: "non-HTTP response")
        }
        let elapsed = started.duration(to: .now)
        logger.info("\(method) \(path) -> \(http.statusCode) in \(elapsed)")

        guard (200 ... 299).contains(http.statusCode) else {
            throw Self.mapError(status: http.statusCode, data: data)
        }
        do {
            return try IrisJSON.decoder().decode(T.self, from: data)
        } catch {
            logger.error("\(method) \(path): contract decode failure")
            throw IrisAPIError.decoding(detail: String(describing: error))
        }
    }

    // MARK: - Error mapping

    static func mapError(status: Int, data: Data) -> IrisAPIError {
        var code = "unknown"
        var message = HTTPURLResponse.localizedString(forStatusCode: status)
        var session: VoiceSessionDescriptor?
        let decoder = IrisJSON.decoder()
        if let envelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
            code = envelope.error.code
            message = envelope.error.message
            session = envelope.session
        } else if let legacy = try? decoder.decode(LegacyError.self, from: data) {
            code = "legacy_error"
            message = legacy.error
        }
        switch status {
        case 401:
            return .unauthorized
        case 404:
            return .notFound(message: message)
        case 409:
            return .conflict(code: code, message: message, session: session)
        case 400 ..< 500:
            return .invalidRequest(code: code, message: message)
        default:
            return .server(code: code, message: message, status: status)
        }
    }
}

// MARK: - Error envelope shapes

private struct ErrorPayload: Decodable {
    let code: String
    let message: String
}

private struct ErrorEnvelope: Decodable {
    let error: ErrorPayload
    let session: VoiceSessionDescriptor?
}

private struct LegacyError: Decodable {
    let error: String
}
