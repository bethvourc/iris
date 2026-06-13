import CoreGraphics
import XCTest
@testable import IrisKit

final class OverlayGeometryTests: XCTestCase {
    func testCentersHorizontallyAndSitsAboveBottomEdge() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let origin = OverlayGeometry.origin(
            screenFrame: screen, size: CGSize(width: 360, height: 72), bottomMargin: 140
        )
        XCTAssertEqual(origin.x, 540) // (1440 - 360) / 2
        XCTAssertEqual(origin.y, 140)
    }

    func testRespectsNonZeroScreenOrigin() {
        // A secondary display offset to the right and up.
        let screen = CGRect(x: 1440, y: 200, width: 1000, height: 600)
        let origin = OverlayGeometry.origin(
            screenFrame: screen, size: CGSize(width: 360, height: 72), bottomMargin: 100
        )
        XCTAssertEqual(origin.x, 1440 + (1000 - 360) / 2)
        XCTAssertEqual(origin.y, 300)
    }

    func testWiderOverlayStaysCentered() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let narrow = OverlayGeometry.origin(
            screenFrame: screen, size: CGSize(width: 360, height: 72), bottomMargin: 140
        )
        let wide = OverlayGeometry.origin(
            screenFrame: screen, size: CGSize(width: 520, height: 120), bottomMargin: 140
        )
        // Both share the same center x.
        XCTAssertEqual(narrow.x + 180, wide.x + 260)
    }
}
