import AppKit

/// The Iris brand mark — Hugeicons "SiriNew" (wave through circle), the
/// same glyph the gateway dashboard uses as its logo (gateway_ui.py).
/// Rendered from inline SVG as a template image so the menu bar tints it.
@MainActor
enum SiriNewIcon {
    enum Variant {
        /// Full strength — daemon healthy.
        case normal
        /// Dimmed — stopped, launching, restarting.
        case dimmed
        /// Corner dot — something needs attention.
        case badged
    }

    static func image(_ variant: Variant) -> NSImage {
        switch variant {
        case .normal: normalImage
        case .dimmed: dimmedImage
        case .badged: badgedImage
        }
    }

    private static let normalImage = render(svg())
    private static let dimmedImage = render(svg(opacity: 0.45))
    private static let badgedImage = render(svg(badge: true))

    private static let wavePath =
        "M11.7805 14C10.4461 15.3922 8.56592 17 7 17C4.23858 17 2 14.7614 2 12C2 "
            + "9.23858 4.23858 7 7 7C12.0899 7 13.5399 15.5 18.5217 15.5C20.4427 15.5 "
            + "22 13.933 22 12C22 10.067 20.4427 8.5 18.5217 8.5C17.6263 8.5 16.4746 "
            + "9.26045 15.5 10.0724"

    private static func svg(opacity: Double = 1.0, badge: Bool = false) -> String {
        let badgeMark = badge
            ? #"<circle cx="19.5" cy="19.5" r="4.5" fill="black" stroke="none"/>"#
            : ""
        return """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" \
        fill="none" stroke="black" stroke-width="1.5" stroke-linecap="round" \
        stroke-linejoin="round" opacity="\(opacity)">\
        <circle cx="12" cy="12" r="10"/>\
        <path d="\(wavePath)"/>\(badgeMark)</svg>
        """
    }

    private static func render(_ svg: String) -> NSImage {
        guard let image = NSImage(data: Data(svg.utf8)) else {
            // Should never happen with a static SVG; degrade to a symbol.
            return NSImage(
                systemSymbolName: "waveform.circle", accessibilityDescription: nil
            ) ?? NSImage()
        }
        image.isTemplate = true
        return image
    }
}
