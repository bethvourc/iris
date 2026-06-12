import Foundation

// Codable mirrors of docs/desktop/api-contract.md. Decoding is lenient by
// contract: unknown JSON fields are ignored (JSONDecoder default) and
// unknown enum values decode to `.unknown` so additive server evolution
// never breaks the client.

// MARK: - Shared decoding

public enum IrisJSON {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            if let date = try? Date(raw, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)) {
                return date
            }
            if let date = try? Date(raw, strategy: .iso8601) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unrecognized ISO 8601 date: \(raw)"
            )
        }
        return decoder
    }
}

/// String enums that decode unknown raw values to `.unknown` instead of
/// failing (client obligation #3 in the contract).
public protocol LenientRawDecodable: RawRepresentable, Decodable, Sendable where RawValue == String {
    static var unknown: Self { get }
}

public extension LenientRawDecodable {
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .unknown
    }
}

// MARK: - Health

public struct HealthStatus: Decodable, Equatable, Sendable {
    public let ok: Bool
    public let agent: String
    public let version: String
    public let contractVersion: Int
    public let pid: Int
    public let startedAt: Date

    enum CodingKeys: String, CodingKey {
        case ok, agent, version, pid
        case contractVersion = "contract_version"
        case startedAt = "started_at"
    }
}

// MARK: - Voice

public enum VoiceState: String, LenientRawDecodable, Encodable {
    case idle, connecting, listening, transcribing, thinking, speaking, meeting, error
    case userSpeaking = "user_speaking"
    case unknown
}

public struct VoiceSessionDescriptor: Decodable, Equatable, Sendable {
    public let id: String
    public let mode: String
    public let startedAt: Date
    public let state: VoiceState?

    enum CodingKeys: String, CodingKey {
        case id, mode, state
        case startedAt = "started_at"
    }
}

public struct VoiceStatus: Decodable, Equatable, Sendable {
    public let state: VoiceState
    public let session: VoiceSessionDescriptor?
    public let subscribers: Int
    public let meetingActive: Bool

    enum CodingKeys: String, CodingKey {
        case state, session, subscribers
        case meetingActive = "meeting_active"
    }
}

public struct VoiceStopResponse: Decodable, Equatable, Sendable {
    public let ok: Bool
    public let wasRunning: Bool

    enum CodingKeys: String, CodingKey {
        case ok
        case wasRunning = "was_running"
    }
}

struct VoiceStartResponse: Decodable {
    let session: VoiceSessionDescriptor
}

struct OkResponse: Decodable {
    let ok: Bool
}

// MARK: - Activity

public enum ActivityKind: String, LenientRawDecodable {
    case voiceSession = "voice_session"
    case run, meeting, message
    case unknown
}

public enum ActivityStatus: String, LenientRawDecodable {
    case done, failed, cancelled, blocked, running
    case unknown
}

public struct ActivityStats: Decodable, Equatable, Sendable {
    public let sessionsThisWeek: Int
    public let runsCompleted: Int
    public let runsFailed: Int
    public let lastActiveAt: Date?

    enum CodingKeys: String, CodingKey {
        case sessionsThisWeek = "sessions_this_week"
        case runsCompleted = "runs_completed"
        case runsFailed = "runs_failed"
        case lastActiveAt = "last_active_at"
    }
}

public struct ActivityItem: Decodable, Equatable, Sendable, Identifiable {
    public let id: String
    public let kind: ActivityKind
    public let time: Date
    public let title: String
    public let preview: String?
    public let status: ActivityStatus
}

public struct ActivityDay: Decodable, Equatable, Sendable {
    public let date: String
    public let label: String?
    public let items: [ActivityItem]
}

public struct ActivityFeed: Decodable, Equatable, Sendable {
    public let stats: ActivityStats
    public let days: [ActivityDay]
    public let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case stats, days
        case nextCursor = "next_cursor"
    }
}

public struct TranscriptEntry: Decodable, Equatable, Sendable {
    public let role: String
    public let text: String
    public let time: Date?
}

public struct ActivityRunLink: Decodable, Equatable, Sendable {
    public let runId: String
    public let eventsUrl: String

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case eventsUrl = "events_url"
    }
}

public struct ActivityDetail: Decodable, Equatable, Sendable {
    public let id: String
    public let kind: ActivityKind
    public let time: Date?
    public let status: ActivityStatus
    public let title: String
    public let transcript: [TranscriptEntry]?
    public let run: ActivityRunLink?
    public let summary: String?
}

// MARK: - Approvals (legacy endpoint shapes, contract §8)

public struct Approval: Decodable, Equatable, Sendable, Identifiable {
    public let approvalId: String
    public let runId: String?
    public let actionName: String
    public let risk: String
    public let status: String
    public let preview: String
    // Raw ISO strings: the daemon emits Python isoformat (+00:00 offset,
    // microseconds), which the shared Date strategy does not guarantee to
    // parse. The approvals view (5.4) owns display formatting.
    public let createdAt: String
    public let expiresAt: String?
    public let decidedAt: String?

    public var id: String {
        approvalId
    }

    enum CodingKeys: String, CodingKey {
        case risk, status, preview
        case approvalId = "approval_id"
        case runId = "run_id"
        case actionName = "action_name"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case decidedAt = "decided_at"
    }
}

struct ApprovalsResponse: Decodable {
    let approvals: [Approval]
}

public enum ApprovalDecision: String, Sendable {
    case approve, deny
}

public struct ApprovalDecisionResponse: Decodable, Equatable, Sendable {
    public let ok: Bool
    public let status: String
}

// MARK: - Settings

public enum SettingSource: String, LenientRawDecodable {
    case env, settings
    case `default`
    case unknown
}

/// Heterogeneous JSON for setting values (lists, strings, booleans).
public indirect enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case bool(Bool)
    case number(Double)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value): try container.encode(value)
        case let .bool(value): try container.encode(value)
        case let .number(value): try container.encode(value)
        case let .array(value): try container.encode(value)
        case let .object(value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

public struct SettingEntry: Decodable, Equatable, Sendable {
    public let value: JSONValue
    public let source: SettingSource
    public let mutable: Bool
}

public struct SecretStatus: Decodable, Equatable, Sendable {
    public let isSet: Bool

    enum CodingKeys: String, CodingKey {
        case isSet = "is_set"
    }
}

public struct SettingsResponse: Decodable, Equatable, Sendable {
    public let settings: [String: SettingEntry]
    public let secrets: [String: SecretStatus]
}
