import AppKit
import SwiftUI

/// The single source of truth for the desktop console's visual language.
///
/// Centralizing tokens here is what keeps the no-AI-slop standard alive across
/// every later view: light surfaces, hairline borders, one royal-blue accent,
/// and moss/amber/rust reserved strictly for status. No gradients, no glows,
/// no decorative shapes live in this set. The desktop app deliberately owns
/// its own rules (sidebar layout) rather than inheriting the web dashboard's
/// top-nav language (docs/desktop/architecture.md).
enum DesignSystem {
    // MARK: - Color

    /// Semantic colors. Every token adapts to light/dark appearance so the
    /// shell reads correctly in both; callers never reach for system colors
    /// directly, so a palette change happens in exactly one place.
    enum Colors {
        /// The window canvas behind cards and content.
        static let canvas = dynamic(light: 0xF7F7F5, dark: 0x1C1C1E)
        /// Raised surface — cards, the selected sidebar row.
        static let surface = dynamic(light: 0xFFFFFF, dark: 0x252527)
        /// A quieter fill for nested wells and the sidebar background.
        static let surfaceSecondary = dynamic(light: 0xF0F0ED, dark: 0x2D2D30)

        /// Hairline borders and dividers — never heavier than 1pt.
        static let border = dynamic(light: 0xE3E3DE, dark: 0x3A3A3D)

        // Text tiers all clear WCAG AA (4.5:1) for normal text against every
        // surface they appear on, while staying visually distinct from each
        // other — verified in both appearances.
        static let textPrimary = dynamic(light: 0x1A1A18, dark: 0xF2F2F0)
        static let textSecondary = dynamic(light: 0x595954, dark: 0xB2B2AD)
        static let textTertiary = dynamic(light: 0x6E6E68, dark: 0x95958F)

        /// The lone accent — a single royal blue. Brightened in dark mode so it
        /// clears AA against the dark canvas *and* raised card surfaces (links).
        static let accent = dynamic(light: 0x2D54CE, dark: 0x7891FF)

        /// Status hues, used only for status: healthy/success, caution,
        /// failure/destructive. Never decorative.
        static let moss = dynamic(light: 0x4E7A4F, dark: 0x74A975)
        static let amber = dynamic(light: 0xB5811F, dark: 0xE0A53E)
        static let rust = dynamic(light: 0xB14A2C, dark: 0xE07B5B)
    }

    // MARK: - Typography

    /// The type scale. System font everywhere; data uses tabular, monospaced
    /// digits so numbers align and don't jitter as they update.
    enum Typography {
        static let title = Font.system(size: 22, weight: .semibold)
        static let heading = Font.system(size: 17, weight: .semibold)
        static let body = Font.system(size: 13)
        static let callout = Font.system(size: 13, weight: .medium)
        static let caption = Font.system(size: 11)

        /// Uppercase mono section headers (e.g. the Activity day dividers).
        static let sectionLabel = Font.system(size: 11, weight: .medium, design: .monospaced)

        /// Large numerals for stats — tabular so columns line up.
        static let dataLarge = Font.system(size: 28, weight: .semibold).monospacedDigit()
        /// Inline numerals inside running text or rows.
        static let data = Font.system(size: 13).monospacedDigit()
    }

    // MARK: - Spacing

    /// A 4pt-based spacing scale; using named steps keeps rhythm consistent.
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
    }

    // MARK: - Radius

    /// Corner radii live in a tight 6–8pt band — soft, never pill-shaped.
    enum Radius {
        static let sm: CGFloat = 6
        static let md: CGFloat = 8
    }

    /// One hairline width for every border and divider.
    static let hairline: CGFloat = 1
}

private extension DesignSystem {
    /// Build an appearance-adaptive `Color` from light/dark sRGB hex values.
    static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }
}

private extension NSColor {
    convenience init(hex: UInt32) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
