import Foundation
import Security

/// The gateway token is the only credential between the app and the daemon
/// (docs/desktop/architecture.md §4). It lives exclusively in the Keychain:
/// device-only, never synced, never written to defaults, files, or logs.
public struct TokenStore: Sendable {
    public static let defaultService = "com.bethvour.iris"
    public static let defaultAccount = "gateway-token"

    private let service: String
    private let account: String
    private let backend: any KeychainBackend

    public init(
        service: String = TokenStore.defaultService,
        account: String = TokenStore.defaultAccount,
        backend: any KeychainBackend = SecKeychainBackend()
    ) {
        self.service = service
        self.account = account
        self.backend = backend
    }

    /// The stored token, or nil if none exists yet.
    public func load() throws -> String? {
        guard let data = try backend.read(service: service, account: account) else {
            return nil
        }
        guard let token = String(bytes: data, encoding: .utf8), !token.isEmpty else {
            throw TokenStoreError.corruptItem
        }
        return token
    }

    /// First-run bootstrap: returns the existing token or generates and
    /// persists a fresh one.
    public func loadOrCreate() throws -> String {
        if let existing = try load() {
            return existing
        }
        let token = Self.generateToken()
        try backend.write(service: service, account: account, data: Data(token.utf8))
        return token
    }

    /// Replaces the token — the recovery action for token mismatch (F4).
    /// The daemon must be restarted with the new value afterwards.
    public func rotate() throws -> String {
        let token = Self.generateToken()
        try backend.write(service: service, account: account, data: Data(token.utf8))
        return token
    }

    public func delete() throws {
        try backend.delete(service: service, account: account)
    }

    /// 256 bits from the system CSPRNG, hex-encoded (64 chars).
    public static func generateToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        precondition(status == errSecSuccess, "system CSPRNG unavailable")
        return bytes.map { String(format: "%02x", $0) }.joined()
    }
}

public enum TokenStoreError: Error, Equatable, Sendable {
    case keychain(OSStatus)
    case corruptItem
}

extension TokenStoreError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case let .keychain(status):
            let detail = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
            return "Keychain operation failed: \(detail)"
        case .corruptItem:
            return "The stored gateway token is unreadable."
        }
    }
}

// MARK: - Keychain backend

/// Thin seam over Keychain Services so TokenStore logic is testable
/// without touching the real keychain.
public protocol KeychainBackend: Sendable {
    func read(service: String, account: String) throws -> Data?
    /// Upsert.
    func write(service: String, account: String, data: Data) throws
    /// Deleting a missing item is not an error.
    func delete(service: String, account: String) throws
}

public struct SecKeychainBackend: KeychainBackend {
    public init() {}

    public func read(service: String, account: String) throws -> Data? {
        var query = baseQuery(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            return result as? Data
        case errSecItemNotFound:
            return nil
        default:
            throw TokenStoreError.keychain(status)
        }
    }

    public func write(service: String, account: String, data: Data) throws {
        let query = baseQuery(service: service, account: account)
        let update: [String: Any] = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw TokenStoreError.keychain(updateStatus)
        }
        var add = query
        add[kSecValueData as String] = data
        // Device-only, available after first unlock, never synced.
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw TokenStoreError.keychain(addStatus)
        }
    }

    public func delete(service: String, account: String) throws {
        let status = SecItemDelete(baseQuery(service: service, account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw TokenStoreError.keychain(status)
        }
    }

    private func baseQuery(service: String, account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }
}
