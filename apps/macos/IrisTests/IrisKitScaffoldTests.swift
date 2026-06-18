import XCTest
@testable import IrisKit

final class IrisKitScaffoldTests: XCTestCase {
    func testContractVersionIsPinnedToGatewayContract() {
        // Mirrors CONTRACT_VERSION in src/iris/gateway.py; bump both
        // sides in one change (docs/desktop/api-contract.md §1).
        XCTAssertEqual(IrisKitInfo.contractVersion, 1)
    }
}
