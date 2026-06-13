import CoreGraphics

/// Pure overlay placement math, factored out of the AppKit controller so
/// it can be unit-tested without a window server.
public enum OverlayGeometry {
    /// Bottom-center of the working area, sitting `bottomMargin` above the
    /// bottom edge (clear of the Dock).
    public static func origin(
        screenFrame: CGRect, size: CGSize, bottomMargin: CGFloat
    ) -> CGPoint {
        CGPoint(
            x: screenFrame.midX - size.width / 2,
            y: screenFrame.minY + bottomMargin
        )
    }
}
