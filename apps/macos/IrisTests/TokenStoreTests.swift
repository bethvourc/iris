import Foundation
import XCTest
@testable import IrisKit

/// In-memory backend for logic tests; the real keychain is exercised by
/// the integration tests below.
final class FakeKeychainBackend: KeychainBackend, @unchecked Sendable {
    private var storage: [String: Data] = [:]
    private let lock = NSLock()
    var failNextWrite: TokenStoreError?

    private func key(_ service: String, _ account: String) -> String {
        "\(service)|\(account)"
    }

    func read(service: String, account: String) throws -> Data? {
        lock.withLock { storage[key(service, account)] }
    }

    func write(service: String, account: String, data: Data) throws {
        if let failure = failNextWrite {
            failNextWrite = nil
            throw failure
        }
        lock.withLock { storage[key(service, account)] = data }
    }

    func delete(service: String, account: String) throws {
        _ = lock.withLock { storage.removeValue(forKey: key(service, account)) }
    }
}

final class TokenStoreTests: XCTestCase {
    private func makeStore(backend: FakeKeychainBackend = FakeKeychainBackend()) -> TokenStore {
        TokenStore(service: "test-service", account: "test-account", backend: backend)
    }

    func testGenerateTokenIs256BitHexAndUnique() {
        let first = TokenStore.generateToken()
        let second = TokenStore.generateToken()
        XCTAssertEqual(first.count, 64)
        XCTAssertTrue(first.allSatisfy(\.isHexDigit))
        XCTAssertNotEqual(first, second)
    }

    func testLoadOrCreateBootstrapsOnceAndIsStable() throws {
        let store = makeStore()
        XCTAssertNil(try store.load())

        let created = try store.loadOrCreate()
        XCTAssertEqual(created.count, 64)
        XCTAssertEqual(try store.loadOrCreate(), created)
        XCTAssertEqual(try store.load(), created)
    }

    func testRotateReplacesTheToken() throws {
        let store = makeStore()
        let original = try store.loadOrCreate()
        let rotated = try store.rotate()
        XCTAssertNotEqual(rotated, original)
        XCTAssertEqual(try store.load(), rotated)
    }

    func testDeleteRemovesTokenAndIsIdempotent() throws {
        let store = makeStore()
        _ = try store.loadOrCreate()
        try store.delete()
        XCTAssertNil(try store.load())
        try store.delete() // no throw on missing
    }

    func testCorruptItemSurfacesAsTypedError() throws {
        let backend = FakeKeychainBackend()
        try backend.write(service: "test-service", account: "test-account", data: Data())
        let store = makeStore(backend: backend)
        XCTAssertThrowsError(try store.load()) { error in
            XCTAssertEqual(error as? TokenStoreError, .corruptItem)
        }
    }

    func testWriteFailurePropagates() {
        let backend = FakeKeychainBackend()
        backend.failNextWrite = .keychain(errSecInteractionNotAllowed)
        let store = makeStore(backend: backend)
        XCTAssertThrowsError(try store.loadOrCreate())
    }
}

/// Exercises the real Keychain. Uses a unique throwaway service name and
/// always cleans up; skips gracefully where the keychain is unavailable
/// (e.g. locked CI keychains).
final class SecKeychainBackendIntegrationTests: XCTestCase {
    private let service = "com.bethvour.iris.tests.\(UUID().uuidString)"

    private func makeStore() throws -> TokenStore {
        let store = TokenStore(service: service, backend: SecKeychainBackend())
        do {
            _ = try store.loadOrCreate()
        } catch let TokenStoreError.keychain(status) {
            throw XCTSkip("keychain unavailable in this environment (OSStatus \(status))")
        }
        return store
    }

    func testFullLifecycleAgainstRealKeychain() throws {
        let store = try makeStore()
        defer { try? store.delete() }

        let token = try XCTUnwrap(try store.load())
        XCTAssertEqual(token.count, 64)

        let rotated = try store.rotate()
        XCTAssertNotEqual(rotated, token)
        XCTAssertEqual(try store.load(), rotated)

        try store.delete()
        XCTAssertNil(try store.load())
    }

    func testStoredItemIsNotSynchronizable() throws {
        // Device-only on macOS means: never marked synchronizable, so iCloud
        // Keychain will not sync it. The kSecAttrAccessible attribute only
        // applies to the data-protection keychain, which needs entitlements
        // that arrive with release signing (Step 7.2 re-verifies there).
        let store = try makeStore()
        defer { try? store.delete() }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: TokenStore.defaultAccount,
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        XCTAssertEqual(SecItemCopyMatching(query as CFDictionary, &result), errSecSuccess)
        let attributes = try XCTUnwrap(result as? [String: Any])
        let synchronizable = attributes[kSecAttrSynchronizable as String] as? Bool
        XCTAssertNotEqual(synchronizable, true)
    }
}

final class AppPreferencesTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "iris-prefs-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testDefaults() {
        let preferences = AppPreferences(defaults: defaults)
        XCTAssertEqual(preferences.gatewayPort, 8765)
        XCTAssertFalse(preferences.onboardingComplete)
        XCTAssertEqual(preferences.gatewayBaseURL.absoluteString, "http://127.0.0.1:8765")
    }

    func testRoundTrip() {
        let preferences = AppPreferences(defaults: defaults)
        preferences.gatewayPort = 9000
        preferences.onboardingComplete = true

        let reloaded = AppPreferences(defaults: defaults)
        XCTAssertEqual(reloaded.gatewayPort, 9000)
        XCTAssertTrue(reloaded.onboardingComplete)
        XCTAssertEqual(reloaded.gatewayBaseURL.absoluteString, "http://127.0.0.1:9000")
    }
}
